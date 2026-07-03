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

use nix::sys::signal;
use nix::sys::wait::{WaitPidFlag, WaitStatus, waitpid};
use nix::unistd::Pid;
use std::process;
use std::sync::Arc;
use std::{thread, time::Duration};
use tokio::signal::unix::{SignalKind, signal};

use padsi::trace::{debug, error, info, warn};

use crate::config::{Config, SharedConfig};
use crate::process::{Process, ProcessState};

fn may_restart_process(shconf: &SharedConfig, process: &Process) {
    if process.spec.may_restart() {
        let shconf = Arc::clone(&shconf);
        let spec = process.spec.clone();
        let current_pid = process.pid;
        tokio::spawn(async move {
            if let Ok(new_process) = spec.start().await {
                let mut config = shconf.lock().await;
                config.procs.remove(&current_pid);
                config.procs.insert(new_process.pid, new_process);
            };
        });
    } else if process.spec.is_restart() {
        error!(
            program = process.spec.program(),
            "not restarting, restart rate exceeded"
        );
    }
}

// reap children, called when the SIGCHLD has been received
pub async fn reap_children(shconf: &SharedConfig) {
    loop {
        match waitpid(Pid::from_raw(-1), Some(WaitPidFlag::WNOHANG)) {
            Ok(WaitStatus::Exited(pid, code)) => {
                debug!("reaped process {pid} (exited with status {code})");
                let mut config = shconf.lock().await;
                let pid = pid.as_raw() as i32;
                if let Some(process) = config.procs.get_mut(&pid) {
                    process.state = ProcessState::Exited { code };
                    if process.spec.is_required() {
                        info!("Required process {pid} exited (with status {code})");
                        shutdown(&config)
                    }
                    may_restart_process(shconf, &process);
                }
            }
            Ok(WaitStatus::Signaled(pid, sig, _)) => {
                debug!("reaped process {pid} (killed via signal {sig})");
                let signal_num = sig as i32;
                let mut config = shconf.lock().await;
                let pid = pid.as_raw() as i32;
                if let Some(process) = config.procs.get_mut(&pid) {
                    process.state = ProcessState::Killed { signal: signal_num };
                    if process.spec.is_required() {
                        info!("Required process {pid} killed (via signal {sig})");
                        shutdown(&config)
                    }
                    may_restart_process(shconf, &process);
                }
            }
            Ok(_) | Err(nix::errno::Errno::ECHILD) => break,
            Err(e) => {
                debug!("waitpid error: {e}");
                break;
            }
        }
    }
}

pub fn chld_reaper_setup(shconf: &SharedConfig) {
    // An infinite stream of hangup signals.
    let mut stream = signal(SignalKind::child()).unwrap();

    let shconf = Arc::clone(&shconf);
    tokio::spawn(async move {
        loop {
            stream.recv().await;
            reap_children(&shconf).await;
        }
    });
}

pub fn shutdown(config: &Config) -> ! {
    info!("Shutdown");
    std::fs::remove_file(&config.socket_file).unwrap_or_else(|_| warn!("Failed to remove socket"));
    for proc in config.procs.values() {
        info!("Killing process {} (PID {})", proc.spec.program(), proc.pid);
        signal::kill(Pid::from_raw(proc.pid as i32), signal::SIGTERM)
            .unwrap_or_else(|e| warn!("Failed to kill process {}: {}", proc.pid, e.desc()))
    }
    info!("Shutdown done");
    thread::sleep(Duration::from_millis(1000));
    process::exit(0)
}
