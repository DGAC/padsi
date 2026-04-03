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

use std::process::{Command, Child, Output, Stdio};
use anyhow::{Result, anyhow};

pub struct Task {
    pub keep_status: bool,
    child: Option<Child>,
    output: Option<Output>
}

impl Task {
    pub fn new(args: &Vec<String>, keep_status:bool) -> Result<Self> {
        if args.len()==0 {
            return Err(anyhow!("invalid empty command arguments"))
        }
        let mut cmd=Command::new(&args[0]);
        cmd.stdout(Stdio::piped());
        if args.len()>1 {
            cmd.args(&args[1..]);
        }
        match cmd.spawn() {
            Ok(child) => Ok(Task{keep_status, child: Some(child), output: None}),
            Err(err) => {
                Err(anyhow!("failed to run {}: {}", args.join(" "), err.to_string()))
            }
        }
    }

    /// Get the result of the task's execution, if it has finished (returns None otherwise)
    pub fn result(&mut self) -> Option<&Output> {
        if let Some(child)=& mut self.child {
            match child.try_wait() {
                Ok(Some(_x)) => {
                    // process has terminated
                    let child=self.child.take().unwrap();
                    let output=child.wait_with_output();
                    self.output=Some(output.unwrap());
                },
                Ok(None) => return None, // process has not yet terminated
                Err(_err) => return None // some other kind of error
            }
        }

        self.output.as_ref()
    }
}
