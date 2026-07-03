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
use caps::{CapSet, Capability};

/// Parse a capability name like "net_admin" or "CAP_NET_ADMIN" into a
/// `caps::Capability`.  Returns an error string on failure.
pub fn parse_capability(name: &str) -> Result<Capability> {
    // caps expects the canonical uppercase form: "CAP_NET_ADMIN"
    let upper = {
        let upper_name = name.to_uppercase();
        if upper_name.starts_with("CAP_") {
            upper_name
        } else {
            format!("CAP_{}", upper_name)
        }
    };
    upper
        .parse::<Capability>()
        .map_err(|_| anyhow!("unknown capability: '{name}'"))
}

/// Called inside the child process (after fork, before exec).
/// Drops all capabilities, then re-adds only the requested ones.
///
/// This runs in a single-threaded fork child so blocking / non-async is fine.
pub fn apply_capabilities(caps: &[Capability]) -> Result<()> {
    // 1. Clear every capability set (inherited, permitted, effective, ambient).
    caps::clear(None, CapSet::Inheritable)?;
    caps::clear(None, CapSet::Ambient)?;

    if caps.is_empty() {
        // Drop permitted + effective too — fully unprivileged child.
        caps::clear(None, CapSet::Permitted)?;
        caps::clear(None, CapSet::Effective)?;
        return Ok(());
    }

    // 2. Raise the requested capabilities in all relevant sets so that they
    //    survive exec() and are effective in the new image.
    for &cap in caps {
        // Permitted / Effective: active right now and after exec (for
        // binaries that do not call cap_set_proc themselves).
        caps::raise(None, CapSet::Permitted, cap)?;
        caps::raise(None, CapSet::Effective, cap)?;
        // Inheritable + Ambient: preserved across exec even for
        // non-privileged executables.
        caps::raise(None, CapSet::Inheritable, cap)?;
        caps::raise(None, CapSet::Ambient, cap)?;
    }

    Ok(())
}

pub fn capabilities_to_string(caps: &[Capability]) -> String {
    caps.iter()
        .map(Capability::to_string)
        .collect::<Vec<String>>()
        .join(",")
}
