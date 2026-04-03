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
use std::cell::{RefCell};
use std::collections::HashMap;
use std::ffi::OsStr;
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use users::os::unix::UserExt;
use users::{User, get_user_by_uid};

use crate::config::AgentConfig;
use crate::agent::OsAgent;
use crate::task::Task;

const PADSI_AGENT_MOUNTPOINT: &str = "/run/padsi-agent";

pub struct LinuxAgent {
    vm_config: AgentConfig,
    user: Option<User>,
    extensions: Vec<&'static str>,
    has_gui: RefCell<Option<bool>>,
    user_session_opened: RefCell<bool>,
    next_task_id: RefCell<u64>,
    tasks: RefCell<HashMap<u64,Task>>
}

pub type PlatformAgent = LinuxAgent;

impl LinuxAgent {
    pub fn new() -> Result<Self> {
        virtio_mount("padsi-agent", PADSI_AGENT_MOUNTPOINT)?;
        let vm_config = AgentConfig::from_config_in_dir(PADSI_AGENT_MOUNTPOINT)?;
        let user = get_user_by_uid(vm_config.user_id);
        Ok(Self {
            vm_config,
            user,
            extensions: vec!["sh", "py"],
            has_gui: RefCell::new(None),
            user_session_opened: RefCell::new(false),
            next_task_id:RefCell::new(0),
            tasks: RefCell::new(HashMap::default())
        })
    }

    fn home_dir(&self) -> Option<&Path> {
        match &self.user {
            Some(u) => Some(u.home_dir()),
            None => None,
        }
    }

    // Tell if the OS has a GUI
    fn has_gui(&self) -> bool {
        let mut b_has_gui=self.has_gui.borrow_mut();
        if b_has_gui.is_none() {
            *b_has_gui= match Command::new("systemctl").output() {
                Ok(output) => {
                    if output.status.success() {
                        let mut found=false;
                        let data=String::from_utf8_lossy(&output.stdout);
                        for line in data.lines() {
                            if line.contains("gdm") || line.contains("sddm") || line.contains("lightdm") {
                                found=true;
                                break
                            }
                        }
                        Some(found)
                    }
                    else {
                        Some(false)
                    }
                },
                Err(_err) => Some(false)
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
        return PADSI_AGENT_MOUNTPOINT;
    }

    fn platform_extensions(&self) -> &Vec<&str> {
        &self.extensions
    }

    fn platform_runner(&self, _ext: &str) -> Option<Vec<&str>> {
        None
    }

    fn user_session_opened(&self) -> bool {
        let mut b_val=self.user_session_opened.borrow_mut();
        if ! *b_val {
            let mut runtime_dir=PathBuf::from("/run/user");
            runtime_dir.push(format!("{}", self.vm_config.user_id));
            if self.has_gui() {
                let mut path=runtime_dir.clone();
                path.push("wayland-0");
                if path.exists() {
                    *b_val=true;
                }
                else {
                    let mut path=runtime_dir.clone();
                    path.push("ICEauthority");
                    if path.exists() {
                        *b_val=true;
                    }
                }
            }
            else if runtime_dir.exists() {
                *b_val=true;
            }
        }
        *b_val
    }

    fn mount_shared_dirs(&self) -> Result<()> {
        let mut warnings: Vec<String> = vec![];
        for (fsname, mountpoint) in self.vm_config.mountpoints.iter() {
            let mp = PathBuf::from(mountpoint);
            if mp.is_absolute() {
                return Err(anyhow!(
                    "CODEBUG: mountpoint {} should not be an absolute path",
                    mountpoint
                ));
            }

            let real_mp = match self.home_dir() {
                Some(p) => {
                    let mut res = PathBuf::from(p);
                    res.push(mp);
                    res
                }
                None => {
                    return Err(anyhow!(
                        "CODEBUG: user '{}' (UID {}) does not exist in system",
                        self.vm_config.user_name,
                        self.vm_config.user_id
                    ));
                }
            };

            // actual mounting
            println!(
                "Mounting FS '{}' to '{}' ==> '{:?}'",
                fsname, mountpoint, real_mp
            );
            if let Err(e) = virtio_mount(fsname, real_mp) {
                warnings.push(e.to_string());
            }
        }

        match warnings.len() {
            0 => Ok(()),
            _ => Err(anyhow!("warnings: {}", warnings.join(", "))),
        }
    }

    fn shutdown(&self) -> Result<()>{
        match Command::new("poweroff").output() {
            Ok(o) => {
                if o.status.success() {
                    Ok(())
                }
                else {
                    Err(anyhow!("failed to run 'poweroff': {}", String::from_utf8_lossy(&o.stderr)))
                }
            }
            Err(err) => Err(anyhow!(err))
        }
    }

    fn new_task(&self, args:&Vec<String>, with_status:bool) -> Result<u64> {
        let mut b_id=self.next_task_id.borrow_mut();
        let tid=*b_id;
        *b_id+=1;
        let mut b_tasks=self.tasks.borrow_mut();
        let task=Task::new(args, with_status)?;
        b_tasks.insert(tid, task);
        Ok(tid)
    }

    fn task_output(&mut self, id: u64) -> Result<Option<Output>> {
        let mut b_tasks=self.tasks.borrow_mut();
        match b_tasks.get_mut(&id) {
            Some(task) => {
                match task.result() {
                    Some(output) => {
                        let o=output.to_owned();
                        b_tasks.remove(&id); // get rid of the task
                        println!("Getting rid of task {} which has been queried", id);
                        Ok(Some(o))
                    }
                    None => Ok(None)
                }
            }
            None => Err(anyhow!("no task with ID '{}'", id))
        }
    }

    fn tasks(&self) -> Vec<u64> {
        let b_tasks=self.tasks.borrow();
        b_tasks.iter().map(|(k, _v)| *k).collect()
    }

    fn reap_tasks(&self) {
        let mut b_tasks=self.tasks.borrow_mut();
        let mut to_del:Vec<u64>=vec![];
        for (id, task) in b_tasks.iter_mut() {
            if let Some(_output)=task.result() {
                if ! task.keep_status {
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
    let res = Command::new("findmnt")
        .arg("-n")
        .arg("-o")
        .arg("SOURCE")
        .arg(mp)
        .output()?;
    if res.status.success() {
        return Ok(true);
    }
    if res.stderr.len() == 0 {
        return Ok(false);
    }
    let errstr = String::from_utf8_lossy(&res.stderr[..]).into_owned();
    Err(anyhow!(errstr))
}

fn virtio_mount(fsname: &str, mountpoint: impl AsRef<Path>) -> Result<()> {
    // check if already mounted
    match virtiofs_mounted(&mountpoint) {
        Ok(mounted) => {
            if mounted {
                let s = String::from_utf8_lossy(OsStr::new(mountpoint.as_ref()).as_bytes());
                println!("Mountpoint '{}' already mounted", s);
                return Ok(());
            }
        }
        Err(err) => return Err(anyhow!(err.to_string())),
    }

    // mount if not yet mounted
    let s = String::from_utf8_lossy(OsStr::new(mountpoint.as_ref()).as_bytes());
    println!("Mounting fs '{}' to mountpoint '{}'", fsname, s);

    let mp: &OsStr = OsStr::new(mountpoint.as_ref());
    let res = Command::new("mount")
        .arg("-t")
        .arg("virtiofs")
        .arg(fsname)
        .arg(mp)
        .output()?;
    match res.status.success() {
        true => Ok(()),
        false => Err(anyhow!(
            "failed to mount virtiofs named {} to {:?}: {}",
            fsname,
            mp,
            String::from_utf8_lossy(&res.stderr)
        )),
    }
}
