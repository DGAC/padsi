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
use std::path::PathBuf;
use std::process::{Command, Output};

use crate::config::AgentConfig;

pub trait OsAgent {
    /// Get the configuration of the VM agent
    fn config(&self) -> &AgentConfig;

    /// Get the path where PADSI's configuration files
    /// are located in the VM
    fn agent_dir(&self) -> &str;

    /// Get all the extensions for scripts or programs
    /// for the specific OS (without the '.', e.g. "bat" for Windows).
    /// The order is used as search order
    fn platform_extensions(&self) -> &Vec<&str>;

    /// Get a "runner" program, i.e. a program to use to actually
    /// depending on the script's extension, may run a script (maybe with some arguments hence the Vec)
    fn platform_runner(&self, ext: &str) -> Option<Vec<&str>>;

    /// Tell if the user session is opened
    fn user_session_opened(&self) -> bool;

    /// Mount all the configured filesystems
    fn mount_shared_dirs(&self) -> Result<()>;

    /// Start the system's shutdown
    fn shutdown(&self) -> Result<()>;

    /// Run a process as a new task
    /// if `with_status` is true, then the caller must get the status of the
    /// task using task_output()
    fn new_task(&self, args:&Vec<String>, with_status:bool) -> Result<u64>;

    /// Get a tack's status
    /// The task is destroyed after this function returned a non None value
    fn task_output(&mut self, id: u64) -> Result<Option<Output>>;

    /// Get the IDs of all the running tasks
    fn tasks(&self) -> Vec<u64>;

    /// Capture the output of tasks which are finished, in order to
    /// avoid zombi processes
    fn reap_tasks(&self);

    /// Run the on-boot.XXX script where XXX is one of several platform
    /// extensions
    fn run_boot_script(&self) -> Result<()> {
        for ext in self.platform_extensions() {
            let mut script = PathBuf::from(self.agent_dir());
            script.push("bin");
            if let Some(args) = self.platform_runner(ext) {
                for arg in args {
                    script.push(arg)
                }
            }
            script.push(format!("on-boot.{}", *ext));
            if script.try_exists().is_ok() {
                println!("running {:?}", script);
                let mut cmd = Command::new(&script);
                self.add_environment_variables(&mut cmd);
                match cmd.output() {
                    Ok(output) => {
                        if output.status.success() {
                            return Ok(());
                        }
                        return Err(anyhow!(
                            "failed to execute '{}': {}",
                            script.to_string_lossy(),
                            String::from_utf8_lossy(&output.stderr[..])
                        ));
                    }
                    Err(e) => {
                        return Err(anyhow!(
                            "failed to execute '{}': {}",
                            script.to_string_lossy(),
                            e.to_string()
                        ));
                    }
                }
            }
        }
        Ok(())
    }

    fn add_environment_variables(&self, command: &mut Command) {
        let mut dir = PathBuf::from(self.agent_dir());
        dir.push("etc");
        command.env("PADSI_ETC_DIR", dir.as_os_str());

        let mut dir = PathBuf::from(self.agent_dir());
        dir.push("lib");
        command.env("PADSI_LIB_DIR", dir.as_os_str());
        command.env("PYTHONPATH", dir.as_os_str());

        let mut dir = PathBuf::from(self.agent_dir());
        dir.push("user");
        command.env("PADSI_USER_DIR", dir.as_os_str());
    }
}
