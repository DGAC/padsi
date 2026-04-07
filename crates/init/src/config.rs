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

use anyhow::Result;
use caps::Capability;
use std::collections::HashMap;

use std::sync::Arc;
use tokio::sync::Mutex;

use crate::capabilities::parse_capability;
use crate::process::Process;

// global configuration
pub struct Config {
    pub procs: HashMap<i32, Process>,
    pub capabilities: Vec<Capability>,
    pub auto_stop: bool,
    pub env: HashMap<String, String>,
    pub socket_file: String,
}

impl Config {
    pub fn new(socket_file: &str, caps: Option<&str>) -> Result<Self> {
        let caps_v = match caps {
            Some(s) => s
                .split(',')
                .map(parse_capability)
                .collect::<Result<Vec<Capability>>>(),
            None => Ok(vec![]),
        }?;
        let mut cenv: HashMap<String, String> = HashMap::new();
        for (key, value) in std::env::vars() {
            cenv.insert(key, value);
        }
        Ok(Self {
            procs: HashMap::new(),
            capabilities: caps_v,
            auto_stop: false,
            env: cenv,
            socket_file: String::from(socket_file),
        })
    }
}

pub type SharedConfig = Arc<Mutex<Config>>;
