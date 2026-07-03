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

use std::time::{SystemTime, UNIX_EPOCH};
//use chrono::{DateTime, Utc};
//use anyhow::{anyhow, Result};

///
/// Get the current unix timestamp
/// If the `ts` argument is not None, then the difference between that instant
/// and now is returned.
///
pub fn now(ts: Option<u64>) -> u64 {
    match ts {
        Some(x) => x,
        None => {
            let now = SystemTime::now();
            now.duration_since(UNIX_EPOCH)
                .expect("Time went backwards")
                .as_secs()
        }
    }
}

///
/// Get the current unix timestamp as a String
/// If the `ts` argument is not None, then the difference between that instant
/// and now is returned.
///
pub fn now_string(ts: Option<u64>) -> String {
    let now = now(ts);
    format!("{now}")
}

// TODO: if necessary
// fn system_time_to_date_time(t: SystemTime) -> Result<DateTime<Utc>> {
//     let (sec, nsec) = match t.duration_since(UNIX_EPOCH) {
//         Ok(dur) => (dur.as_secs() as i64, dur.subsec_nanos()),
//         Err(e) => { // unlikely but should be handled
//             return Err(anyhow!(e.to_string()))
//         },
//     };
//     match DateTime::from_timestamp(sec, nsec) {
//         Some(d) => Ok(d),
//         None => Err(anyhow!("from_timestamp({}, {}) failed", sec, nsec))
//     }
// }
