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
use std::path::{Path, PathBuf};
use std::ffi::OsStr;
use std::process::{Command, Output};
use padsi::trace::{info, debug, warn, error};
use std::{thread, time};

use crate::config::{AgentConfig, VMUsage};
use crate::agent::OsAgent;
use crate::task::Task;

const PADSI_AGENT_DRIVE: &str = "Z:";
const PADSI_AGENT_MOUNTPOINT: &str = "Z:\\";
const WINFSP_LAUNCHCTL: &str = r"C:\Program Files (x86)\WinFsp\bin\launchctl-x64.exe";
const WINFSP_RUN: &str = r"C:\Program Files (x86)\WinFsp\bin\fsreg.bat";
const WINFSP_EXE: &str = r"C:\Program Files\Virtio-Win\VioFS\virtiofs.exe";

pub struct WindowsAgent {
    vm_config: AgentConfig,
    user: Option<User>, // will never be None if mode is RUN
    extensions: Vec<&'static str>,
    last_drive_used: char,
    user_session_opened: RefCell<bool>,
    next_task_id: RefCell<u64>,
    tasks: RefCell<HashMap<u64,Task>>
}

pub struct User {
    home_dir: PathBuf
}

pub type PlatformAgent = WindowsAgent;

pub fn log_dir() -> String {
    let exe=std::env::current_exe().expect("current_exe() failed");
    let dir=exe.parent().expect(&format!("WTF: current exe '{:?}' has no parent directory!", exe));
    dir.to_string_lossy().into()
}

impl WindowsAgent {
    pub fn new() -> Result<Self> {
        WindowsAgent::start_win_fsp()?;

        debug!("Mounting the padsi-agent FS");
        virtio_mount("padsi-agent", PADSI_AGENT_DRIVE, 'Z', None)?; // always mounted as Z:
        debug!("Loading configuration");
        let vm_config = AgentConfig::from_config_in_dir(PADSI_AGENT_MOUNTPOINT)?;
        debug!("User name: {}", &vm_config.user_name);
        let user = get_local_user(&vm_config.user_name);
        if let None=user && vm_config.usage==VMUsage::RUN {
            return Err(anyhow!("User {} does not exist (in RUN mode)", vm_config.user_id))
        }
        Ok(Self {
            vm_config,
            user: user,
            extensions: vec!["ps1", "bat"],
            last_drive_used: 'Z',
            user_session_opened: RefCell::new(false),
            next_task_id:RefCell::new(0),
            tasks: RefCell::new(HashMap::default())
        })
    }

    fn start_win_fsp() -> Result<()> {
        debug!("Starting WinFSP");
        let mut cmd=Command::new(WINFSP_RUN);
        cmd.args(vec!["virtiofs", WINFSP_EXE, "-t %1 -m %2"]);
        debug!("{:?}", cmd);
        match cmd.output() {
            Ok(res) => {
                if ! res.status.success() {
                    let errstr = String::from_utf8_lossy(&res.stderr[..]).into_owned();
                    error!("failed to start WinFSP: {}", errstr);
                    return Err(anyhow!("failed to start WinFSP: {}", errstr))
                }
            },
            Err(err) => return Err(anyhow!("failed to start WinFSP: {}", err.to_string()))
        }
        debug!("WinFSP is started");
        Ok(())
    }
}

impl OsAgent for WindowsAgent {
    fn config(&self) -> &AgentConfig {
        &self.vm_config
    }

    fn agent_dir(&self) -> &str {
        PADSI_AGENT_MOUNTPOINT
    }

    fn user_home_dir(&self) -> &Path {
        match &self.user {
            Some(u) => {
                debug!("User homedir: {}", u.home_dir().display());
                u.home_dir()
            },
            None => panic!("CODEBUG: user is not yet defined")
        }
    }

    fn platform_extensions(&self) -> &Vec<&str> {
        &self.extensions
    }

    fn build_command<S, A, I>(&self, program:S, args:Option<I>) -> Command
        where S:AsRef<OsStr>,
            A:AsRef<OsStr>,
            I: IntoIterator<Item = A> {
        build_command(program, args)
    }

    fn user_session_opened(&self) -> bool {
        let mut b_val=self.user_session_opened.borrow_mut();
        if ! *b_val {
            let mut cmd=build_command("user-session-opened.ps1", None::<Vec<String>>);
            cmd.env("PADSI_USER_NAME", &self.config().user_name);
            match cmd.output() {
                Ok(output) => {
                    if output.status.success() {
                        if String::from_utf8_lossy(&output.stdout[..]).to_lowercase().trim()=="true" {
                            *b_val=true;
                        }
                    }
                    else {
                        error!("failed to execute {:?}: {}", cmd, String::from_utf8_lossy(&output.stderr[..]))
                    }
                },
                Err(err) => error!("failed to execute {:?}: {}", cmd, err.to_string())
            }
        }
        *b_val
    }

    fn mount_shared_dirs(&mut self) -> Result<()> {
        let mut warnings: Vec<String> = vec![];
        for (fsname, mountpoint) in self.vm_config.mountpoints.iter() {
            // compute driver letter to use
            if self.last_drive_used=='D' {
                return Err(anyhow!("no more drive letters available"))
            }
            let drive_letter=std::char::from_u32(self.last_drive_used as u32 - 1).unwrap();

            // actual mounting
            if let Err(e) = virtio_mount(fsname, mountpoint, drive_letter, Some(self.user_home_dir())) {
                warnings.push(e.to_string());
            }
            self.last_drive_used=drive_letter;
        }

        match warnings.len() {
            0 => Ok(()),
            _ => Err(anyhow!("warnings: {}", warnings.join(", "))),
        }
    }

    fn shutdown(&self) -> Result<()>{
        info!("Shutting down the system");
        let args:Vec<&str>=vec!["/s", "/t", "0"];
        let mut cmd=build_command("shutdown", Some(args));
        match cmd.output() {
            Ok(out) => {
                if !out.status.success() {
                    let msg=String::from_utf8_lossy(&out.stderr);
                    error!("could not shut down the system: {}", msg);
                    return Err(anyhow!("could not shut down the system: {}", msg))
                }
                Ok(())
            },
            Err(err) => Err(anyhow!("could not shut down the system: {}", err.to_string()))
        }
    }

    fn new_task(&self, args:&Vec<String>, with_status:bool) -> Result<u64> {
        let mut b_id=self.next_task_id.borrow_mut();
        let tid=*b_id;
        *b_id+=1;
        let mut b_tasks=self.tasks.borrow_mut();
        let task=Task::new(self, args, with_status)?;
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
                        info!("Getting rid of task {} which has been queried", id);
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

///
/// Find out if an FS name is already mapped and where
///
fn virtiofs_is_mapped(fsname: &str) -> Result<Option<String>> {
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

fn virtiofs_map_drive(fsname: &str, drive_letter:char) -> Result<()> {
    info!("Mapping fs '{}' to drive letter '{}'", fsname, drive_letter);
    let drive=format!("{}:", drive_letter);
    let vdrive=format!("viofs{}", drive_letter);
    let args:Vec<&str>=vec!["start", "virtiofs", &vdrive, fsname, &drive];
    info!("Running: {}", args.join(" "));
    let mut cmd=build_command(r"C:\Program Files (x86)\WinFsp\bin\launchctl-x64.exe", Some(args));
    match cmd.output() {
        Ok(output) => {
            if output.status.success() {
                info!("VirtioFS '{}' will soon be mapped to {}, waiting a bit", fsname, drive);
                let mut counter=0;
                let step_ms=250;
                let delay = time::Duration::from_millis(step_ms);
                let drive_p=PathBuf::from(&drive);
                while counter<100 {
                    counter+=1;
                    thread::sleep(delay);
                    match drive_p.try_exists() {
                        Ok(e) => {
                            if e {
                                info!("VirtioFS '{}' is now mapped to {}", fsname, drive);
                                return Ok(())
                            } else {
                                debug!("VirtioFS '{}' is not yet mapped to {}, waiting a bit...", fsname, drive);
                            }
                        },
                        Err(err) => {
                            warn!("Failed to determine if {} exists: {}", drive, err.to_string())
                        }
                    }
                }

                let msg=format!("VirtioFS '{}' has not been mapped to {}, even though we waited for {} ms", fsname, drive, counter*step_ms);
                error!(msg);
                return Err(anyhow!(msg))
            }
            else {
                let msg=format!("could not map '{}' as drive {}: {}", fsname, drive, String::from_utf8_lossy(&output.stderr[..]));
                error!(msg);
                return Err(anyhow!(msg))
            }
        },
        Err(err) => {
            let msg=format!("could not map '{}' as drive {}: {}", fsname, drive, err.to_string());
            error!(msg);
            return Err(anyhow!(msg))
        }
    }
}

impl User {
    fn home_dir(&self) -> &Path {
        self.home_dir.as_ref()
    }
}

fn is_drive_only(path: &Path) -> bool {
    let mut components = path.components();
    if let Some(c0) = components.next() {
        let oss0=c0.as_os_str().as_encoded_bytes();
        if oss0.len()==2 && oss0[1]>=b':' &&
            ((oss0[0]>=b'a' && oss0[0]<=b'z') || (oss0[0]>=b'A' && oss0[0]<=b'Z')) &&
            components.next().is_none() {
            return true
        }
    }
    false
}

///
/// Actually mount a FS
///
fn virtio_mount(fsname: &str, mountpoint: &str, drive_letter:char, home_dir:Option<&Path>) -> Result<()> {
    // prepare mount point, creating directories if necessary
    let mut mp_path = PathBuf::from(mountpoint);
    let mut mp_path_display=mp_path.display();
    info!("Virtio mount FS '{}' to mountpoint '{}', drive '{}', home dir: '{:?}'", fsname, mountpoint, drive_letter, home_dir);
    let mut mp_path_str=match mp_path.to_str() {
        Some(p) => p,
        None => return Err(anyhow!("could not convert directory {} to &str", mp_path_display))
    };
    let drive=format!("{}:", drive_letter);

    if ! is_drive_only(&mp_path) {
        if home_dir==None {
            return Err(anyhow!("CODEBUG: mountpoint directory {} is not absolue and yet home dir is None", mountpoint))
        }
        let mut new_mp_path=PathBuf::from(home_dir.unwrap());
        new_mp_path.push(mp_path);

        // create directory if it does not exist
        debug!("Creating directory {} if not yet present", new_mp_path.display());
        if let Err(err)=std::fs::create_dir_all(&new_mp_path) {
            if err.kind()!=std::io::ErrorKind::AlreadyExists {
                return Err(anyhow!("could not create mountpoint directory {}: {} ({})", new_mp_path.display(), err.to_string(), err.kind()))
            }
        }
        mp_path=new_mp_path;

        mp_path_display=mp_path.display();
        mp_path_str=match mp_path.to_str() {
            Some(p) => p,
            None => return Err(anyhow!("could not convert directory {} to &str", mp_path_display))
        };
        info!("Real mount point will be {}", mp_path_display);

        // to bind the mapped drive to the expected moint point, the actual directory must not yet exist (even though all its parents must)
        if let Err(err)=std::fs::remove_dir(&mp_path) {
            if err.kind()==std::io::ErrorKind::PermissionDenied {
                info!("Unlinking {}", mp_path_display);
                let args:Vec<&str>=vec!["/c", "rmdir", "/Q", "/S", mp_path_str];
                let mut cmd=build_command("cmd", Some(args));
                match cmd.output() {
                    Ok(out) => {
                        if !out.status.success() {
                            let msg=format!("could not unlink directory {}: {}", mp_path_display, String::from_utf8_lossy(&out.stderr));
                            error!(msg);
                            return Err(anyhow!(msg))
                        }
                    },
                    Err(err) => return Err(anyhow!("could not unlink directory {}: {}", mp_path_display, err.to_string()))
                }
            }
            else {
                return Err(anyhow!("could not remove mountpoint top directory {}: {}", mp_path_display, err.to_string()))
            }
        }
        else {
            debug!("Removed directory {mp_path_display}");
        }
    }
    else {
        debug!("Mount directory is a drive, no directory to create")
    }

    // check if FS is already mapped to a drive letter
    match virtiofs_is_mapped(fsname) {
        Ok(Some(mp)) => {
            info!("VirtioFS '{}' is already mapped to {}", fsname, mp)
        },
        Ok(None) => {
            virtiofs_map_drive(fsname, drive_letter)?
        },
        Err(err) => return Err(anyhow!(err.to_string())),
    }

    // check mountpoint and drive are compatible
    if let Some(v) = mp_path.to_str() {
        if v.len()==2 && v.chars().nth(1)==Some(':') && v!=drive {
            return Err(anyhow!("not handled: mounting drive {} to {}", drive, v))
        }
    }

    // link mountpoint to drive
    if ! is_drive_only(&mp_path) {
        info!("Linking drive '{}' to mountpoint {}", drive, mp_path_display);
        let args:Vec<&str>=vec!["/c", "mklink", "/d", mp_path_str, &drive];
        let mut cmd=build_command("cmd", Some(args));
        match cmd.output() {
            Ok(out) => {
                if !out.status.success() {
                    let msg=format!("could not link directory {} to drive {}: {}", mp_path_display, drive, String::from_utf8_lossy(&out.stderr));
                    error!(msg);
                    return Err(anyhow!(msg))
                }
            },
            Err(err) => return Err(anyhow!("could not link directory {} to drive {}: {}", mp_path_display, drive, err.to_string()))
        }
    }

    Ok(())
}

///
/// Find a local user
///
fn get_local_user(username: &str) -> Option<User> {
    use windows::Win32::NetworkManagement::NetManagement::{
        NetUserGetInfo, USER_INFO_2, NERR_Success,
    };
    use windows::core::PCWSTR;

    let wide_name: Vec<u16> = username.encode_utf16().chain([0]).collect();
    let mut buf_ptr = std::ptr::null_mut();

    unsafe {
        let result = NetUserGetInfo(
            PCWSTR::null(),          // null = localhost
            PCWSTR(wide_name.as_ptr()),
            2,                       // info level: USER_INFO_2 has most attributes
            &mut buf_ptr,
        );

        match result {
            #[allow(non_upper_case_globals)]
            NERR_Success => {
                let info = &*(buf_ptr as *const USER_INFO_2);
                let mut home_dir=match info.usri2_home_dir.to_string() {
                    Ok(hd) => hd,
                    Err(err) => {
                        error!("failed to get information about user '{}': {}", username, err.to_string());
                        return None
                    }
                };
                // in most cases, the previous method does not work for whatever reason, so fall back to what should be
                // the actual user's home directory
                if home_dir=="" {
                    info!("Microsoft's API return '' as the actual profile directory");
                    home_dir=format!(r"C:\Users\{}", username);
                }
                Some(User{
                    home_dir: home_dir.into()
                })
            },
            2221 => {
                // user not found
                None
            },
            code => {
                error!("failed to get information about user '{}': code {}", username, code);
                None
            }
        }
    }
}


///
/// Commands execution tools
///
fn platform_runner(ext: &str) -> Option<Vec<&'static str>> {
    match ext {
        "ps1" => Some(vec!["powershell", "-File"]),
        "bat" => Some(vec!["cmd", "/c"]),
        _ => None
    }
}

///
/// Create a command object which can be specialized before
/// being executed
///
fn build_command<S, A, I>(program:S, args:Option<I>) -> Command
        where S:AsRef<OsStr>,
            A:AsRef<OsStr>,
            I: IntoIterator<Item = A> {
    let mut prog_path=PathBuf::from(program.as_ref());
    if ! prog_path.is_absolute() {
        let exe=std::env::current_exe().expect("current_exe() failed");
        let dir=exe.parent().expect(&format!("WTF: current exe '{:?}' has no parent directory!", exe));
        prog_path=PathBuf::from(dir);
        prog_path.push(program.as_ref());
    }

    let ext=prog_path.extension().and_then(|s| s.to_str());
    let mut cmd=match ext {
        Some (e) => {
            match platform_runner(e) {
                Some(runner) => {
                    if runner.len()>0 {
                        let mut cmd=Command::new(runner[0]);
                        if runner.len()>1 {
                            cmd.args(&runner[1..]);
                        }
                        cmd.arg(prog_path);
                        cmd
                    }
                    else {
                        Command::new(program)
                    }
                },
                None => {
                    Command::new(program)
                }
            }
        },
        None => {
            Command::new(program)
        }
    };
    if args.is_some() {
        cmd.args(args.unwrap());
    }
    debug!("Full command to run: {:?}", cmd);
    cmd
}
