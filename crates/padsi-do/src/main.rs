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
use nix::NixPath;
use nix::mount::{MsFlags, mount};
use nix::sched::{CloneFlags, unshare};
use nix::unistd::{self, Gid, Uid, User, execv, getuid};
use padsi::trace::{LevelFilter, TraceConfig, error, info, tracing_setup_json, warn};
use std::env::{self, current_dir};
use std::ffi::{CString, OsStr, OsString};
use std::fs;
use std::fs::File;
use std::io::Write;
use std::os::fd::AsFd;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::thread::sleep;
use std::time::Duration;
use tempfile::NamedTempFile;

const NAMESERVER: &str = "192.168.128.1";
const PROG_PATH: &str = "/usr/bin/padsi-do"; // avoid using current_exe()

/// Command line arguments
#[derive(Debug)]
struct DoArgs {
    /// help
    help: bool,

    /// list of admin. NS
    list: bool,

    /// name of the admin NS to use
    adminns: Option<String>,

    /// optional delay to wait for the namespace to be present (0 for no delay)
    /// in 10th of a second
    delay: u32,

    /// command to run
    command: Vec<String>,
}

impl DoArgs {
    /// Parse the command line arguments
    fn parse() -> Result<Self> {
        let mut help = false;
        let mut list = false;
        let mut adminns: Option<String> = None;
        let mut delay: u32 = 0;
        let mut command: Vec<String> = vec![];
        let mut args = env::args();
        if let None = args.next() {
            return Err(anyhow!("Malformed arguments"));
        }

        loop {
            match args.next() {
                Some(a1) => match &*a1 {
                    "-a" | "--adminns" => {
                        adminns = match args.next() {
                            Some(s) => Some(s),
                            None => return Err(anyhow!("Admin namespace not specified")),
                        };
                    }
                    "-d" | "--delay" => {
                        delay = match args.next() {
                            Some(s) => match s.parse::<u32>() {
                                Ok(d) => {
                                    if d > u32::MAX / 10 {
                                        return Err(anyhow!("Delay is too long"));
                                    }
                                    d * 10
                                }
                                Err(_err) => return Err(anyhow!("Invalid specified delay")),
                            },
                            None => return Err(anyhow!("Delay not specified")),
                        };
                    }
                    "-l" | "--list" => list = true,
                    "-h" | "--help" => help = true,
                    _ => {
                        command.push(a1);
                        break;
                    }
                },
                None => break,
            }
        }
        // copy the rest
        for item in args {
            command.push(item)
        }

        Ok(DoArgs {
            help,
            list,
            adminns,
            delay,
            command,
        })
    }

    /// Tell if a command was actually specified
    fn is_empty(&self) -> bool {
        self.command.len() == 0
    }

    /// Print usage
    fn usage() {
        println!(
            "Command line arguments

Usage: padsi-do [OPTIONS] <COMMAND>...

Arguments:
    <COMMAND>...  command to run

    Options:
        -l, --list               list all configured admin. NS
        -a, --adminns <ADMINNS>  name of the admin. NS to use
        -d, --delay <DELAY>      delay in seconds to wait for the admin. NS to be present
                                 before failing
        -h, --help               print this help"
        )
    }
}

///
/// Information associated with the adminns to use
///
struct AdminNS {
    path: PathBuf,
    proxy: bool,
}

impl AdminNS {
    /// Try to find the adminns to use based on the files present in /run/netns and
    /// possibily a specified named adminns
    fn find(name: Option<&str>, delay: u32) -> Result<Self> {
        match delay {
            0 => Self::find_no_delay(name),
            _ => {
                let mut remain = delay;
                loop {
                    match Self::find_no_delay(name) {
                        Ok(s) => return Ok(s),
                        Err(_) => {
                            const D: u32 = 5;
                            remain -= D; // in 10th of a second
                            if remain == 0 {
                                return Err(anyhow!("delay elapsed"));
                            }
                            sleep(Duration::from_millis(100 * D as u64));
                        }
                    }
                }
            }
        }
    }

    fn find_no_delay(name: Option<&str>) -> Result<Self> {
        match name {
            Some(adminns) => {
                let mut path = PathBuf::from(format!("/run/netns/admns-{}-p", adminns));
                if !path.exists() {
                    path = PathBuf::from(format!("/run/netns/admns-{}-P", adminns));
                    if !path.exists() {
                        match path.as_os_str().to_str() {
                            Some(p) => return Err(anyhow!("admin NS '{}' does not exist", p)),
                            None => {
                                return Err(anyhow!(
                                    "admin NS does not exist (name uses non UTF-8 characters)"
                                ));
                            }
                        }
                    }
                }
                let proxy = Self::parse_netns_path(&path);
                match proxy {
                    Some(p) => Ok(Self {
                        path: PathBuf::from(path),
                        proxy: p,
                    }),
                    None => Err(anyhow!("invalid admin NS '{}'", adminns)),
                }
            }
            None => match Self::get_single_adminns() {
                Ok(a) => Ok(a),
                Err(_) => return Err(anyhow!("failed to locate admin NS")),
            },
        }
    }

    ///
    /// Get the name of the admin NS (as identified by the user)
    ///
    fn name(&self) -> String {
        let raw = self.path.to_string_lossy();
        String::from(&raw[17..raw.len() - 2])
    }

    ///
    /// Parse a path name to determine if it corresponds to an admin. NS and, if so, returns if it uses a proxy or not
    /// admin. NS names are like "/run/netns/admns-<name>-[pP]"
    ///
    fn parse_netns_path(path: &impl AsRef<Path>) -> Option<bool> {
        let path = match path.as_ref().as_os_str().to_str() {
            Some(p) => p,
            None => return None,
        };
        if !path.starts_with("/run/netns/admns-") {
            return None;
        }
        let parts: Vec<&str> = path.split("-").collect();
        match parts.len() {
            3 => {
                let proxy = match parts[2] {
                    "P" => true,  // uppercase P
                    "p" => false, // lowercase p
                    _ => {
                        eprintln!("Warning: malformed net NS {}", path);
                        warn!("malformed net NS {}", path);
                        return None;
                    }
                };
                Some(proxy)
            }
            _ => {
                eprintln!("Warning: malformed net NS {}", path);
                warn!("malformed net NS {}", path);
                return None;
            }
        }
    }

    ///
    /// List all available adminns
    ///
    fn get_all_adminns() -> Vec<Self> {
        let paths = fs::read_dir("/run/netns").unwrap();
        let mut res: Vec<Self> = vec![];
        for path in paths {
            let de = path.unwrap();
            let path = de.path();
            match Self::parse_netns_path(&path) {
                Some(proxy) => res.push(Self { path, proxy }),
                None => {}
            }
        }
        res
    }

    ///
    /// Finds the unique adminns from the files in in /run/netns and returns the path and if a Web proxy needs to be used
    ///
    fn get_single_adminns() -> Result<Self> {
        let paths = fs::read_dir("/run/netns").unwrap();
        let mut res: Option<Self> = None;
        for path in paths {
            let de = path.unwrap();
            let path = de.path();
            match Self::parse_netns_path(&path) {
                Some(proxy) => match res {
                    Some(_) => return Err(anyhow!("more than one admin NS found")),
                    None => res = Some(Self { path, proxy }),
                },
                None => {}
            }
        }
        match res {
            Some(p) => Ok(p),
            None => Err(anyhow!("no admin NS found")),
        }
    }
}

///
/// Get the full path of a file, may iterate through all the PATH elements to find it
///
fn get_full_path<P>(prog_name: P) -> Option<PathBuf>
where
    P: AsRef<Path>,
{
    if prog_name.as_ref().len() == 0 {
        return None;
    }

    // if starts with '/', then return AS-IS, or if it's '.', then prepend the CWD
    let first_item = prog_name.as_ref().iter().nth(0).unwrap();
    match first_item.to_str() {
        Some(s1) => {
            match s1.chars().nth(0).unwrap() {
                '/' => return Some(prog_name.as_ref().into()), // we already have a full path
                '.' => match prog_name.as_ref().iter().nth(1) {
                    Some(s2) => {
                        if let Ok(cwd) = current_dir() {
                            let mut p = PathBuf::new();
                            p.push(cwd);
                            p.push(s2);
                            return Some(p);
                        }
                    }
                    None => return None,
                },
                _ => {}
            }
        }
        None => {}
    }

    // iterate over PATH
    env::var_os("PATH").and_then(|paths| {
        env::split_paths(&paths)
            .filter_map(|dir| {
                let full_path = dir.join(&prog_name);
                if full_path.is_file() {
                    Some(full_path)
                } else {
                    None
                }
            })
            .next()
    })
}

fn err_exit_expl<T: std::fmt::Debug>(msg: &str, e: T) -> ! {
    eprintln!("Error: {} ({:?})", msg, e);
    error!("{} ({:?})", msg, e);
    std::process::exit(1);
}

///
/// Get the name, UID and GID of the actual user
/// using sudo's env. variables
fn get_calling_user() -> Result<(String, Uid, Gid)> {
    // Check all SUDO's env. variables are present or none is
    let sudo_user = env::var("SUDO_USER");
    let sudo_uid = env::var("SUDO_UID");
    let sudo_gid = env::var("SUDO_GID");

    if !(sudo_user.is_ok() && sudo_uid.is_ok() && sudo_gid.is_ok()
        || sudo_user.is_err() && sudo_uid.is_err() && sudo_gid.is_err())
    {
        return Err(anyhow!("Could not determine if sudo was used"));
    }

    let username = match sudo_user {
        Ok(u) => u,
        Err(_err) => "root".into(),
    };
    let uid = match sudo_uid {
        Ok(uid_s) => uid_s.parse::<u32>()?,
        Err(_err) => 0,
    };
    let gid = match sudo_gid {
        Ok(gid_s) => gid_s.parse::<u32>()?,
        Err(_err) => 0,
    };
    Ok((username, Uid::from_raw(uid), Gid::from_raw(gid)))
}

///
/// Get the user name from its UID
///
fn username_from_uid(uid: Uid) -> Option<String> {
    User::from_uid(uid).ok().flatten().map(|u| u.name)
}

#[allow(dead_code)]
fn drop_privileges(username: &String, uid: Uid, gid: Gid) -> Result<()> {
    // Set the supplementary groups to the user's groups (initgroups)
    let cusername = CString::new(username.clone())?;
    unistd::initgroups(&cusername, gid)?;

    // Clear all GIDs (real, effective, saved)
    unistd::setresgid(gid, gid, gid)?;

    // Now drop UIDs (do setresuid if available)
    unistd::setresuid(uid, uid, uid)?;

    // Verify
    if unistd::geteuid() != uid || unistd::getegid() != gid {
        return Err(anyhow!("failed to drop privileges"));
    }

    Ok(())
}

///
/// Create a TMP file containing resolv. information
///
fn prepare_etc_resolv() -> Result<NamedTempFile> {
    let mut tmp = NamedTempFile::new()?;
    writeln!(tmp, "nameserver {}", NAMESERVER)?;
    let path = tmp.path();
    fs::set_permissions(path, fs::Permissions::from_mode(0o644))?;
    Ok(tmp)
}

///
/// Check the user can execute the program via sudo
///
fn check_user_privileges(args: &Vec<impl AsRef<OsStr>>, uid: Uid) {
    let cmde: Vec<String> = args
        .iter()
        .map(|a| String::from(a.as_ref().to_str().unwrap_or("???")))
        .collect();
    let cmdestr = cmde.join(" ");
    match Command::new("/usr/bin/sudo")
        .arg("-U")
        .arg(&username_from_uid(uid).expect("User is not declared???"))
        .arg("-l")
        .args(args)
        .output()
    {
        Ok(res) => {
            if !res.status.success() {
                eprintln!("Not allowed to run via sudo: {}", cmdestr);
                error!(cmde = cmdestr, "Not allowed to run via sudo");
                std::process::exit(2);
            }
        }
        Err(err) => {
            eprintln!(
                "Could not run sudo to check user's privileges: {}",
                err.to_string()
            );
            error!(
                cmde = cmdestr,
                error = err.to_string(),
                "Not allowed to run via sudo"
            );
            std::process::exit(3);
        }
    }
}

fn main() {
    let args = match DoArgs::parse() {
        Ok(a) => a,
        Err(e) => {
            DoArgs::usage();
            err_exit_expl("invalid arguments", e)
        }
    };
    if args.help {
        DoArgs::usage();
        std::process::exit(0)
    }
    if args.list {
        let alist = AdminNS::get_all_adminns();
        if alist.len() == 0 {
            println!("No admin. NS found")
        }
        for ans in alist {
            println!("{}", ans.name())
        }
        std::process::exit(0)
    }
    if args.is_empty() {
        DoArgs::usage();
        eprintln!("No command specified");
        std::process::exit(1);
    }

    // if not running as root, then execv using sudo
    if getuid().as_raw() != 0 {
        // spawn this program via sudo
        let iter = args.command.into_iter();
        let mut cstrs: Vec<CString> = iter
            .map(|s| CString::new(s.as_str()).expect("CString::new failed"))
            .collect();

        if let Some(adminns) = args.adminns {
            cstrs.insert(0, CString::new(adminns.as_bytes().to_vec()).unwrap());
            cstrs.insert(0, CString::new("-a".as_bytes().to_vec()).unwrap());
        }

        cstrs.insert(0, CString::new(PROG_PATH.as_bytes().to_vec()).unwrap());
        cstrs.insert(
            0,
            CString::new("/usr/bin/sudo".as_bytes().to_vec()).unwrap(),
        );

        let Err(e) = execv(&cstrs[0], &cstrs);
        eprintln!("Sudo exec failed: {:?}", e);
        error!("Sudo exec failed: {:?}", e);
        std::process::exit(1);
    }

    // init logging
    let trace_conf = TraceConfig::new("/var/padsi/log", "padsi-do")
        .with_stdout_output(false)
        .with_file_level(LevelFilter::INFO);
    let _t = tracing_setup_json(&trace_conf).expect("Failed to initialize logging");

    // get the actual user running this program
    let (_username, uid, _gid) = match get_calling_user() {
        Ok((username, uid, gid)) => (username, uid, gid),
        Err(_err) => {
            eprintln!("Could not get actual caller's UID and GID");
            error!("Could not get actual caller's UID and GID");
            std::process::exit(1);
        }
    };
    let is_root = uid.is_root();

    // get the full path of the program to run
    let mut full_path = match get_full_path(&args.command[0]) {
        Some(p) => p,
        None => {
            eprintln!("Could not find {} in path", &args.command[0]);
            std::process::exit(1);
        }
    };

    // if not actually running as root, check the user can execute the command via sudo
    if !is_root {
        let mut targs: Vec<OsString> = vec![full_path.clone().into_os_string()];
        for arg in &args.command[1..] {
            targs.push(arg.into());
        }
        check_user_privileges(&targs, uid);
    }

    // enter the specified network namespace
    let adminns = match AdminNS::find(args.adminns.as_deref(), args.delay) {
        Ok(a) => a,
        Err(err) => {
            match args.adminns.as_deref() {
                Some(x) => eprintln!("Admin. NS '{}' not found ({})", x, err),
                None => eprintln!("No admin. NS found ({})", err),
            }
            std::process::exit(1)
        }
    };

    let netns_file = match File::open(&adminns.path) {
        Ok(f) => f,
        Err(_e) => {
            eprintln!("failed to open admin NS '{}'", adminns.name());
            error!("failed to open admin NS '{}'", adminns.name());
            std::process::exit(1)
        }
    };
    if adminns.proxy {
        unsafe {
            env::set_var("http_proxy", &format!("http://{}:3128", NAMESERVER));
            env::set_var("https_proxy", &format!("http://{}:3128", NAMESERVER));
        }
    }

    if let Err(e) = nix::sched::setns(netns_file.as_fd(), CloneFlags::CLONE_NEWNET) {
        err_exit_expl("setns to network namespace failed", e);
    }

    // create a new mount namespace (copy of init's)
    if let Err(e) = unshare(CloneFlags::CLONE_NEWNS) {
        err_exit_expl("unshare(CLONE_NEWNS) failed", e);
    }

    // make mounts private so mounts we create don't propagate back
    let res =
        mount::<str, str, str, str>(None, "/", None, MsFlags::MS_REC | MsFlags::MS_PRIVATE, None);
    if let Err(e) = res {
        err_exit_expl("making mounts private failed", e);
    }

    // if /etc/resolv.conf does not exists in the new mount namespace (just in case), create an empty file so the bind mount can succeed.
    let target = Path::new("/etc/resolv.conf");
    if !target.exists() {
        //
        if let Err(e) = std::fs::File::create(&target) {
            err_exit_expl("failed creating /etc/resolv.conf (target did not exist)", e);
        }
    }

    // prepare the contents of /etc/resolv.conf as a TMP file
    let resolv_conf = match prepare_etc_resolv() {
        Ok(f) => f,
        Err(err) => {
            eprintln!("Could not prepare tmp resolv.conf file: {}", err);
            error!("Could not prepare tmp resolv.conf file: {}", err);
            std::process::exit(1);
        }
    };

    // bind mount the TMP resolv.conf file
    if let Err(e) = mount(
        Some(resolv_conf.path()),
        target,
        None::<&str>,
        MsFlags::MS_BIND | MsFlags::MS_REC,
        None::<&str>,
    ) {
        err_exit_expl("bind-mounting resolv.conf failed", e);
    }

    // remount /etc/resolv.conf read-only
    let remount_res = mount(
        Some(resolv_conf.path()),
        target,
        None::<&str>,
        MsFlags::MS_BIND | MsFlags::MS_REMOUNT | MsFlags::MS_RDONLY,
        None::<&str>,
    );
    if let Err(e) = remount_res {
        eprintln!("warning: remounting resolv.conf as RO failed: {:?}", e);
        warn!("remounting resolv.conf as RO failed: {:?}", e);
        // continue — caller asked only that we modify resolv.conf, not that it's readonly.
    }

    // actually run the command
    let cstrs: Vec<CString> = args
        .command
        .iter()
        .map(|s| CString::new(s.as_str()).expect("CString::new failed"))
        .collect();

    let prog = CString::new(full_path.as_mut_os_str().as_bytes().to_vec()).unwrap();
    info!(
        uid = uid.to_string(),
        program = full_path.to_str(),
        command = args.command.join(" "),
        "Running"
    );
    let Err(e) = execv(&prog, &cstrs);
    eprintln!("exec failed: {:?}", e);
    error!("exec failed: {:?}", e);
    std::process::exit(1);
}
