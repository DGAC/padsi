//
// Copyright (c) 2025-2026 DGAC/DSNA
//
// This file is part of PADSI.
//
// This software is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This software is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this software.  If not, see <http://www.gnu.org/licenses/>.
//

use anyhow::{Result, anyhow};
use caps::Capability;
use serde::{Serialize, Serializer};
use std::collections::HashMap;
use std::process::Stdio;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::fs::File;
use tokio::io::AsyncWriteExt;

use padsi::trace::{error, info};

use crate::capabilities::{apply_capabilities, capabilities_to_string};

const RESTART_EVENTS_COUNT: usize = 5; // number of last restart events to consider
const RESTART_MIN_DELAY: u64 = 10; // number of seconds below which restarts won't be donc

///
/// Holds information about how to start a process
///
#[derive(Debug, Clone, Serialize)]
pub struct ProcessSpec {
    args: Vec<String>,
    environ: HashMap<String, String>,
    #[serde(serialize_with = "serialize_caps")]
    capabilities: Vec<Capability>,
    stdin_file: Option<String>,
    stdout_file: Option<String>,
    stderr_file: Option<String>,
    required: bool,
    restart: bool,
    restart_events: Vec<u64>,
}

impl ProcessSpec {
    pub fn new(
        args: Vec<String>,
        global_env: &HashMap<String, String>,
        extra_env: Option<&HashMap<String, String>>,
        capabilities: Vec<Capability>,
        stdin_file: Option<String>,
        stdout_file: Option<String>,
        stderr_file: Option<String>,
        required: bool,
        restart: bool,
    ) -> ProcessSpec {
        // compute actually used environment
        let mut env: HashMap<String, String> = HashMap::new();
        for (k, v) in global_env {
            env.insert(k.clone(), v.clone());
        }
        if let Some(eenv) = extra_env {
            for (k, v) in eenv {
                env.insert(k.clone(), v.clone());
            }
        }
        ProcessSpec {
            args,
            environ: env,
            capabilities,
            stdin_file,
            stdout_file,
            stderr_file,
            required,
            restart,
            restart_events: Vec::with_capacity(RESTART_EVENTS_COUNT),
        }
    }

    pub fn program(&self) -> &str {
        self.args.first().unwrap()
    }
    pub fn is_required(&self) -> bool {
        self.required
    }

    pub fn is_restart(&self) -> bool {
        self.restart
    }

    /// Tell if the process may be restarted, which is the case if
    /// its 'restart' attribute was set to true and if it did not previously
    /// restart too quickly
    pub fn may_restart(&self) -> bool {
        match self.restart {
            false => false,
            true => {
                return if self.restart_events.len() == RESTART_EVENTS_COUNT
                    && self.restart_events[RESTART_EVENTS_COUNT - 1] - self.restart_events[0]
                        < RESTART_MIN_DELAY
                {
                    false
                } else {
                    true
                };
            }
        }
    }

    pub fn env_string(&self) -> String {
        self.environ
            .iter()
            .map(|(k, v)| format!("{}={}", k, v))
            .collect::<Vec<String>>()
            .join(",")
    }

    pub fn args(&self) -> &Vec<String> {
        &self.args
    }

    /// Start or re-start the process
    pub async fn start(mut self) -> Result<Process> {
        let msg = match self.restart_events.is_empty() {
            true => "starting program",
            false => "restarting program",
        };
        info!(
            program = self.program(),
            args = self.args[1..].join(" "),
            env = self.env_string(),
            capabilities = capabilities_to_string(&self.capabilities),
            stdin = self.stdin_file,
            stdout = self.stdout_file,
            stderr = self.stderr_file,
            msg
        );

        let mut cmd = tokio::process::Command::new(&self.program());
        cmd.args(&self.args[1..]);
        cmd.envs(&self.environ);
        cmd.kill_on_drop(false);

        // honor stdin spec
        let mut stdin_data: Option<&String> = None;
        match &self.stdin_file {
            Some(fname)=> match File::open(&fname).await {
                Ok(f) => cmd.stdin(f.into_std().await),
                Err(_) => {
                    stdin_data = Some(fname);
                    cmd.stdin(Stdio::piped())
                }
            },
            None => cmd.stdin(Stdio::null())
        };

        // honor stdout spec
        match &self.stdout_file {
            Some(fname) => {
                let f = match File::create(&fname).await {
                    Ok(f) => f.into_std().await,
                    Err(err) => {
                        let msg = format!("failed to create '{}': {}", &fname, err.to_string());
                        error!(program = self.program(), msg);
                        return Err(anyhow!(msg));
                    }
                };
                cmd.stdout(f)
            },
            None => cmd.stdout(Stdio::null())
        };

        // honor stderr spec
        match &self.stderr_file {
            Some(fname) => {
                let f = match File::create(&fname).await {
                    Ok(f) => f.into_std().await,
                    Err(e) => {
                        let msg = format!("failed to create '{}': {}", &fname, e.to_string());
                        error!(program = self.program(), msg);
                        return Err(anyhow!(msg));
                    }
                };
                cmd.stderr(f)
            }
            None => cmd.stderr(Stdio::null())
        };

        // pre_exec runs after fork() in the child, before exec()
        let caps = self.capabilities.clone();
        unsafe {
            cmd.pre_exec(move || {
                apply_capabilities(&caps)
                    .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e.to_string()))
            });
        }

        match cmd.spawn() {
            Ok(mut child) => {
                if let Some(data) = stdin_data {
                    let mut stdin = child.stdin.take().unwrap();
                    stdin.write_all(data.as_bytes()).await?;
                }

                let pid = child.id().unwrap_or(0) as i32;
                std::mem::forget(child);

                let started_at = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_secs();

                if self.restart_events.len() >= RESTART_EVENTS_COUNT {
                    self.restart_events.remove(0);
                }
                self.restart_events.push(started_at);
                info!(
                    program = self.program(),
                    pid = pid,
                    started_at = started_at,
                    "started"
                );

                Ok(Process {
                    spec: self,
                    started_at,
                    pid,
                    state: ProcessState::Running,
                })
            }
            Err(e) => {
                let msg = format!("failed to start: {}", e.to_string());
                error!(program = self.program(), msg);
                Err(anyhow!(msg))
            }
        }
    }
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum ProcessState {
    Running,
    Exited { code: i32 },
    Killed { signal: i32 },
}

///
/// Holds information about a managed process which has been started
///
#[derive(Debug, Clone, Serialize)]
pub struct Process {
    pub spec: ProcessSpec,
    pub pid: i32,
    pub started_at: u64,
    pub state: ProcessState,
}

fn serialize_caps<S>(items: &Vec<Capability>, serializer: S) -> Result<S::Ok, S::Error>
where
    S: Serializer,
{
    serializer.collect_seq(items.iter().map(ToString::to_string))
}
