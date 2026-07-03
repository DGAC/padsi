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

//!
//! Misc. functions
//!
use anyhow::{Result, anyhow};
use rand::RngExt;
use std::{
    io::Write,
    process::{Command, Output, Stdio},
};

const UPPERCASE: &str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
const LOWERCASE: &str = "abcdefghijklmnopqrstuvwxyz";
const NUMBERS: &str = "0123456789";
#[allow(dead_code)]
const SYMBOLS: &str = ")(*&^%$#@!~";

pub fn generate_password(len: u8) -> String {
    let mut charset = String::from(UPPERCASE);
    charset.push_str(LOWERCASE);
    charset.push_str(NUMBERS);
    //charset.push_str(SYMBOLS);
    let char_vec: Vec<char> = charset.chars().collect();

    let mut rng = rand::rng();

    let password: String = (0..len)
        .map(|_| {
            let idx = rng.random_range(0..char_vec.len());
            char_vec[idx] as char
        })
        .collect();
    password
}

pub fn run_process(mut command: Command, stdin_str: Option<String>) -> Result<Output> {
    let output = match stdin_str {
        Some(stdin_data) => {
            let mut child = command
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()?;
            let stdin = child.stdin.take();
            if stdin.is_none() {
                return Err(anyhow!("Failed to open stdin of spawned process"));
            }
            std::thread::spawn(move || {
                stdin
                    .unwrap()
                    .write_all(stdin_data.as_bytes())
                    .expect("Failed to write to stdin");
            });

            child.wait_with_output()
        }
        None => command.output(),
    };
    match output {
        Ok(o) => {
            if o.status.success() {
                Ok(o)
            } else {
                Err(anyhow!(
                    "Returns status {} (err: {})",
                    o.status,
                    String::from_utf8_lossy(&o.stderr)
                ))
            }
        }
        Err(err) => Err(anyhow!(err)),
    }
}
