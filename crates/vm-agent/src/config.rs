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
use serde::{Deserialize, Deserializer, Serialize};
use std::collections::HashMap;
use std::fs::File;
use std::io::Read;
use std::path::{Path, PathBuf};

#[derive(Serialize, Deserialize, Debug, PartialEq, Clone)]
pub enum VMUsage {
    INSTALL,
    CUSTOMIZE,
    UPDATE,
    RUN,
}

fn string_to_u32<'de, D>(deserializer: D) -> Result<u32, D::Error>
where
    D: Deserializer<'de>,
{
    let s = String::deserialize(deserializer)?;
    s.parse::<u32>().map_err(serde::de::Error::custom)
}

#[derive(Serialize, Deserialize, Debug, Clone)]
#[allow(dead_code)]
pub struct AgentConfig {
    #[serde(rename = "PADSI_VM_CONFIG")]
    pub config: String,
    #[serde(rename = "PADSI_VM_NAME")]
    pub name: String,
    #[serde(rename = "PADSI_VM_NICKNAME")]
    pub nickname: String,
    #[serde(rename = "PADSI_VM_USAGE")]
    pub usage: VMUsage,
    #[serde(rename = "PADSI_USER_ID", deserialize_with = "string_to_u32")]
    pub user_id: u32,
    #[serde(rename = "PADSI_USER_NAME")]
    pub user_name: String,
    #[serde(rename = "PADSI_USER_FULLNAME")]
    pub user_fullname: String,
    #[serde(rename = "PADSI_USER_SHELL")]
    pub user_shell: String,
    #[serde(rename = "PADSI_GROUP_ID", deserialize_with = "string_to_u32")]
    pub group_id: u32,
    #[serde(rename = "PADSI_GROUP_NAME")]
    pub group_name: String,
    #[serde(rename = "PADSI_LANG")]
    pub lang: String,
    #[serde(skip_deserializing)]
    pub mountpoints: HashMap<String, String>,
}

impl AgentConfig {
    pub fn from_config_in_dir(config_dir: impl AsRef<Path>) -> Result<Self> {
        // load mountpoints.json file
        let mut path = PathBuf::from(config_dir.as_ref());
        path.push("etc");
        path.push("mountpoints.json");
        let mut file = File::open(path)?;
        let mut contents = String::new();
        file.read_to_string(&mut contents)?;
        let mpoints: HashMap<String, String> = serde_json::from_str(&contents)?;

        // load config.json file
        let mut path = PathBuf::from(config_dir.as_ref());
        path.push("etc");
        path.push("config.json");
        let mut file = File::open(path)?;
        let mut contents = String::new();
        file.read_to_string(&mut contents)?;

        let mut c: AgentConfig = serde_json::from_str(&contents)?;
        c.mountpoints = mpoints;
        Ok(c)
    }
}
