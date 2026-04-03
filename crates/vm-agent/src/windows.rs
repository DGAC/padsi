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
use shell_words;
use std::cell::{RefCell};
use std::collections::HashMap;
use std::ffi::OsStr;
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use crate::config::AgentConfig;
use crate::agent::OsAgent;
use crate::task::Task;

const PADSI_AGENT_MOUNTPOINT: &str = "Z:";
const WINFSP_LAUNCHCTL: &str = r"C:\Program Files (x86)\WinFsp\bin\launchctl-x64.exe";
const WINFSP_RUN: &str = r"C:\Program Files (x86)\WinFsp\bin\fsreg.bat";
const WINFSP_EXE: &str = r"C:\Program Files\Virtio-Win\VioFS\virtiofs.exe";

pub struct WindowsAgent {
    vm_config: AgentConfig,
    user: Option<User>,
    extensions: Vec<&'static str>,
    win_fsp_started: bool,
    last_drive_used: Option<char>,
    user_session_opened: RefCell<bool>,
    next_task_id: RefCell<u64>,
    tasks: RefCell<HashMap<u64,Task>>
}

pub type PlatformAgent = WindowsAgent;

impl WindowsAgent {
    pub fn new() -> Result<Self> {
        virtio_mount("padsi-agent", PADSI_AGENT_MOUNTPOINT)?;
        let vm_config = AgentConfig::from_config_in_dir(PADSI_AGENT_MOUNTPOINT)?;
        let user = get_user_by_name(&vm_config.user_name);
        Ok(Self {
            vm_config,
            user,
            extensions: vec!["ps1", "bat"],
            win_fsp_started: false,
            last_drive_used: None,
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

    fn build_command(&self, progname: &str, args:Option<Vec<&str>>) -> Command {
        let mut prog_path=PathBuf::from(progname);
        if ! prog_path.is_absolute() {
            let exe=std::env::current_exe().expect("current_exe() failed");
            let dir=exe.parent().expect(&format!("WTF: current exe '{:?}' has no parent directory!", exe));
            prog_path=PathBuf::from(dir);
            prog_path.push(progname);
        }
        let ext=prog_path.extension().and_then(|s| s.to_str());
        let mut cmd=match ext {
            Some (e) => {
                match self.platform_runner(e) {
                    Some(runner) => {
                        if runner.len()>0 {
                            let mut cmd=Command::new(runner[0]);
                            if runner.len()>1 {
                                cmd.args(&runner[1..]);
                            }
                            cmd.arg(progname);
                            cmd
                        }
                        else {
                            Command::new(progname)
                        }
                    },
                    None => {
                        Command::new(progname)
                    }
                }
            },
            None => {
                Command::new(progname)
            }
        };
        if args.is_some() {
            cmd.args(args.unwrap());
        }
        cmd
    }

    fn virtio_mount(&mut self, fsname: &str, mountpoint: impl AsRef<Path>) -> Result<()> {
        // check if already mounted
        match virtiofs_mapped_drive(fsname) {
            Ok(Some(mountpoint)) => {
                println!("VirtioFS '{}' is already mapped to {}", fsname, mountpoint);
                return Ok(())
            },
            Ok(None) => {},
            Err(err) => return Err(anyhow!(err.to_string())),
        }

        // run WinFsp if not yet done
        if ! self.win_fsp_started {
            match self.build_command(WINFSP_RUN, Some(vec!["virtiofs", WINFSP_EXE, "-t %1 -m %2"])).output() {
                Ok(_output) => {
                    self.win_fsp_started=true
                },
                Err(err) => {
                    return Err(anyhow!("failed to start WinFSP: {}", err.to_string()))
                }
            }
        }

        // mount if not yet mounted
        let letter=match self.last_drive_used {
            Some(letter) => {
                if letter=='D' {
                    return Err(anyhow!("no more drive letters available"))
                }
                std::char::from_u32(letter as u32 - 1).unwrap()
            },
            None => 'Z'
        };
        let drive=format!("{}:", letter);
        let s = String::from_utf8_lossy(OsStr::new(mountpoint.as_ref()).as_bytes());
        println!("Mounting fs '{}' to mountpoint '{}'", fsname, s);

        let mp: &OsStr = OsStr::new(mountpoint.as_ref());

    }
}

impl OsAgent for WindowsAgent {
    fn config(&self) -> &AgentConfig {
        &self.vm_config
    }

    fn agent_dir(&self) -> &str {
        return PADSI_AGENT_MOUNTPOINT;
    }

    fn platform_extensions(&self) -> &Vec<&str> {
        &self.extensions
    }

    fn platform_runner(&self, ext: &str) -> Option<Vec<&str>> {
        match ext {
            "ps1" => Some(vec!["powershell"]),
            "bat" => Some(vec!["cmd", "/c"]),
            _ => None
        }
    }

    fn user_session_opened(&self) -> bool {
        let mut b_val=self.user_session_opened.borrow_mut();
        if ! *b_val {
            let mut cmd=self.build_command("user-session-opened.ps1", None);
            cmd.env("PADSI_USER_NAME", &self.config().user_name);
            match cmd.output() {
                Ok(output) => {
                    if output.status.success() {
                        if String::from_utf8_lossy(&output.stdout[..]).to_lowercase()=="true" {
                            *b_val=true;
                        }
                    }
                    else {
                        println!("failed to execute {:?}: {}", cmd, String::from_utf8_lossy(&output.stderr[..]))
                    }
                },
                Err(err) => println!("failed to execute {:?}: {}", cmd, err.to_string())
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

fn virtiofs_mapped_drive(fsname: &str) -> Result<Option<String>> {
    let res = Command::new(WINFSP_LAUNCHCTL)
        .arg("list")
        .output()?;
    if ! res.status.success() {
        return Err(anyhow!(format!("error executing WinFSP launctl: {}",
            String::from_utf8_lossy(&res.stderr[..]).into_owned())))
    }
    // stdout will be like:
    // OK
    // virtiofs viofsZ
    // virtiofs viofsY
    for line in String::from_utf8_lossy(&res.stdout[..]).lines() {
        let parts=line.split(" ").collect::<Vec<&str>>();
        if parts.len()==2 && parts[0]=="virtiofs" && parts[1].len()==6 && parts[1].starts_with("viofs") {
            let drive=&parts[1][5..6];
            let res = Command::new(WINFSP_LAUNCHCTL)
                .arg("info")
                .arg("virtiofs")
                .arg(parts[1])
                .output()?;
            if ! res.status.success() {
                return Err(anyhow!(format!("error executing WinFSP launctl to get info about drive {}: {}",
                    drive, String::from_utf8_lossy(&res.stderr[..]).into_owned())))
            }

            // stdout will be like:
            // OK
            // virtiofs viofsX
            // "C:\Program Files\Virtio-Win\VioFS\virtiofs.exe" -t "Tools_PADSI_vm-management" -m "X:"
            let out=String::from_utf8_lossy(&res.stdout[..]);
            let lines=out.lines().collect::<Vec<&str>>();
            if lines.len()<3 || lines[0]!="OK" {
            return Err(anyhow!(format!("unexpected info output about WinFsp mapped drive '{}': {}",
                drive, String::from_utf8_lossy(&res.stdout[..]))))
            }
            match shell_words::split(lines[2]) {
                Ok(parts) => {
                    if parts.len()!=5 {
                        return Err(anyhow!(format!("unexpected info output about WinFsp mapped drive '{}': {}",
                            drive, lines[2])))
                    }
                    if parts[2]==fsname {
                        return Ok(Some(parts[4].clone()))
                    }
                },
                Err(_) => return Err(anyhow!(format!("unexpected info output about WinFsp mapped drive '{}': {}",
                    drive, lines[2])))
            }
        }
    }
    return Ok(None)
}
