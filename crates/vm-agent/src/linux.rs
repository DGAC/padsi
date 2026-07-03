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
use padsi::trace::{error, info};
use std::cell::RefCell;
use std::collections::HashMap;
use std::ffi::OsStr;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use uzers::os::unix::UserExt;
use uzers::{User, get_user_by_uid};

use crate::agent::OsAgent;
use crate::config::{AgentConfig, VMUsage};
use crate::task::Task;

const PADSI_AGENT_MOUNTPOINT: &str = "/run/padsi-agent";
const LOG_DIR: &str = "/var/log/padsi-agent";

pub struct LinuxAgent {
    vm_config: AgentConfig,
    user: Option<User>, // will never be None if mode is RUN
    extensions: Vec<&'static str>,
    has_gui: RefCell<Option<bool>>,
    user_session_opened: RefCell<bool>,
    next_task_id: RefCell<u64>,
    tasks: RefCell<HashMap<u64, Task>>,
}

pub type PlatformAgent = LinuxAgent;

pub fn log_dir() -> String {
    String::from(LOG_DIR)
}

impl LinuxAgent {
    pub fn new() -> Result<Self> {
        virtio_mount("padsi-agent", PADSI_AGENT_MOUNTPOINT, None, 0, 0)?;
        let vm_config = AgentConfig::from_config_in_dir(PADSI_AGENT_MOUNTPOINT)?;
        let user = get_user_by_uid(vm_config.user_id);
        if let None = user
            && vm_config.usage == VMUsage::RUN
        {
            return Err(anyhow!(
                "User {} does not exist (in RUN mode)",
                vm_config.user_id
            ));
        }
        Ok(Self {
            vm_config,
            user: user,
            extensions: vec!["sh", "py"],
            has_gui: RefCell::new(None),
            user_session_opened: RefCell::new(false),
            next_task_id: RefCell::new(0),
            tasks: RefCell::new(HashMap::default()),
        })
    }

    // Tell if the OS has a GUI
    fn has_gui(&self) -> bool {
        let mut b_has_gui = self.has_gui.borrow_mut();
        if b_has_gui.is_none() {
            *b_has_gui = match Command::new("systemctl").output() {
                Ok(output) => {
                    if output.status.success() {
                        let mut found = false;
                        let data = String::from_utf8_lossy(&output.stdout);
                        for line in data.lines() {
                            if line.contains("gdm")
                                || line.contains("sddm")
                                || line.contains("lightdm")
                            {
                                found = true;
                                break;
                            }
                        }
                        Some(found)
                    } else {
                        Some(false)
                    }
                }
                Err(_err) => {
                    error!("systemctl cound not be run...");
                    Some(false)
                }
            };
        }
        b_has_gui.unwrap()
    }
}

impl OsAgent for LinuxAgent {
    fn config(&self) -> &AgentConfig {
        &self.vm_config
    }

    fn agent_dir(&self) -> &str {
        PADSI_AGENT_MOUNTPOINT
    }

    fn user_home_dir(&self) -> &Path {
        match &self.user {
            Some(u) => u.home_dir(),
            None => panic!("CODEBUG: user is not yet defined"),
        }
    }

    fn platform_extensions(&self) -> &Vec<&str> {
        &self.extensions
    }

    fn build_command<S, A, I>(&self, program: S, args: Option<I>) -> Command
    where
        S: AsRef<OsStr>,
        A: AsRef<OsStr>,
        I: IntoIterator<Item = A>,
    {
        let mut cmd = Command::new(&program);
        if let Some(sargs) = args {
            cmd.args(sargs);
        }
        cmd
    }

    fn user_session_opened(&self) -> bool {
        let mut b_val = self.user_session_opened.borrow_mut();
        if !*b_val {
            let mut runtime_dir = PathBuf::from("/run/user");
            runtime_dir.push(format!("{}", self.vm_config.user_id));
            if self.has_gui() {
                let mut path = runtime_dir.clone();
                path.push("wayland-0");
                if path.exists() {
                    *b_val = true;
                } else {
                    let mut path = runtime_dir.clone();
                    path.push("ICEauthority");
                    if path.exists() {
                        *b_val = true;
                    }
                }
            } else if runtime_dir.exists() {
                *b_val = true;
            }
        }
        *b_val
    }

    fn mount_shared_dirs(&mut self) -> Result<()> {
        let mut warnings: Vec<String> = vec![];
        let user = match &self.user {
            Some(u) => u,
            None => panic!("CODEBUG: user is not yet defined"),
        };
        for (fsname, mountpoint) in self.vm_config.mountpoints.iter() {
            if let Err(e) = virtio_mount(
                fsname,
                mountpoint,
                Some(self.user_home_dir()),
                user.uid(),
                user.primary_group_id(),
            ) {
                warnings.push(e.to_string());
            }
        }

        match warnings.len() {
            0 => Ok(()),
            _ => Err(anyhow!("warnings: {}", warnings.join(", "))),
        }
    }

    fn shutdown(&self) -> Result<()> {
        match Command::new("poweroff").output() {
            Ok(o) => {
                if o.status.success() {
                    Ok(())
                } else {
                    let msg = format!(
                        "failed to run 'poweroff': {}",
                        String::from_utf8_lossy(&o.stderr)
                    );
                    error!(msg);
                    Err(anyhow!(msg))
                }
            }
            Err(err) => {
                error!("failed to run poweroff: {}", err.to_string());
                Err(anyhow!(err))
            }
        }
    }

    fn new_task(&self, args: &Vec<String>, with_status: bool) -> Result<u64> {
        let mut b_id = self.next_task_id.borrow_mut();
        let tid = *b_id;
        *b_id += 1;
        let mut b_tasks = self.tasks.borrow_mut();
        let task = Task::new(self, args, with_status)?;
        b_tasks.insert(tid, task);
        Ok(tid)
    }

    fn task_output(&mut self, id: u64) -> Result<Option<Output>> {
        let mut b_tasks = self.tasks.borrow_mut();
        match b_tasks.get_mut(&id) {
            Some(task) => {
                match task.result() {
                    Some(output) => {
                        let o = output.to_owned();
                        b_tasks.remove(&id); // get rid of the task
                        info!("Getting rid of task {} which has been queried", id);
                        Ok(Some(o))
                    }
                    None => Ok(None),
                }
            }
            None => Err(anyhow!("no task with ID '{}'", id)),
        }
    }

    fn tasks(&self) -> Vec<u64> {
        let b_tasks = self.tasks.borrow();
        b_tasks.iter().map(|(k, _v)| *k).collect()
    }

    fn reap_tasks(&self) {
        let mut b_tasks = self.tasks.borrow_mut();
        let mut to_del: Vec<u64> = vec![];
        for (id, task) in b_tasks.iter_mut() {
            if let Some(_output) = task.result() {
                if !task.keep_status {
                    // getting rid of task which is not kept
                    to_del.push(*id);
                }
            }
        }
        for id in to_del {
            b_tasks.remove(&id);
        }
    }
}

fn virtiofs_mounted(mountpoint: impl AsRef<Path>) -> Result<bool> {
    let mp: &OsStr = OsStr::new(mountpoint.as_ref());
    match Command::new("findmnt")
        .arg("-n")
        .arg("-o")
        .arg("SOURCE")
        .arg(mp)
        .output()
    {
        Ok(res) => {
            if res.status.success() {
                return Ok(true);
            }
            if res.stderr.len() == 0 {
                return Ok(false);
            }
            let msg = String::from_utf8_lossy(&res.stderr[..]).into_owned();
            error!(msg);
            Err(anyhow!(msg))
        }
        Err(err) => {
            let msg = format!("could not run findmnt: {}", err.to_string());
            error!(msg);
            Err(anyhow!(msg))
        }
    }
}

fn virtio_mount(
    fsname: &str,
    mountpoint: &str,
    home_dir: Option<&Path>,
    uid: u32,
    gid: u32,
) -> Result<()> {
    // prepare actual mount point, creating directories if necessary
    let mut mp_path = PathBuf::from(mountpoint);
    if !mp_path.is_absolute() {
        if home_dir == None {
            return Err(anyhow!(
                "CODEBUG: mountpoint directory {} is not absolue and yet home dir is None",
                mountpoint
            ));
        }
        let mut new_mp_path = PathBuf::from(home_dir.unwrap());
        new_mp_path.push(mp_path);

        // create directory if it does not exist
        if let Err(err) = std::fs::create_dir_all(&new_mp_path) {
            return Err(anyhow!(
                "could not create mountpoint directory {}: {}",
                new_mp_path.display(),
                err.to_string()
            ));
        }

        // set ownership
        if uid != 0 && gid != 0 {
            let mut dir = Some(new_mp_path.as_path());
            loop {
                if dir == None {
                    break;
                }
                let sdir = dir.unwrap();
                if let Err(err) = std::os::unix::fs::chown(sdir, Some(uid), Some(gid)) {
                    // a readonly filesystem probably it's mean it's already mounted, so no error here
                    if err.kind() != std::io::ErrorKind::ReadOnlyFilesystem {
                        return Err(anyhow!(
                            "could not chown {} to {}:{}: {}",
                            sdir.display(),
                            uid,
                            gid,
                            err.to_string()
                        ));
                    }
                }
                if sdir == home_dir.unwrap() {
                    break;
                }
                dir = sdir.parent();
            }
        }
        mp_path = new_mp_path
    } else {
        // create directory if it does not exist
        if let Err(err) = std::fs::create_dir_all(&mp_path) {
            return Err(anyhow!(
                "could not create mountpoint directory {}: {}",
                mp_path.display(),
                err.to_string()
            ));
        }
    }

    let mp_path_display = mp_path.display();

    // check if already mounted
    match virtiofs_mounted(&mp_path) {
        Ok(mounted) => {
            if mounted {
                info!("Mountpoint '{}' already mounted", mp_path_display);
                return Ok(());
            }
        }
        Err(err) => return Err(anyhow!(err.to_string())),
    }

    info!(
        "Mounting fs '{}' to mountpoint '{}'",
        fsname, mp_path_display
    );
    match Command::new("mount")
        .arg("-t")
        .arg("virtiofs")
        .arg(fsname)
        .arg(mp_path.as_os_str())
        .output()
    {
        Ok(res) => match res.status.success() {
            true => Ok(()),
            false => {
                let msg = format!(
                    "failed to mount virtiofs named {} to {:?}: {}",
                    fsname,
                    mp_path_display,
                    String::from_utf8_lossy(&res.stderr)
                );
                error!(msg);
                Err(anyhow!(msg))
            }
        },
        Err(err) => {
            let msg = format!(
                "failed to mount virtiofs named {} to {:?}: {}",
                fsname,
                mp_path_display,
                err.to_string()
            );
            error!(msg);
            Err(anyhow!(msg))
        }
    }
}
