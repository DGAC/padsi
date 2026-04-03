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

use anyhow::{Error, Result, anyhow};
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::{fmt::Display, hash::Hash, str::FromStr};

///
/// Domain Pattern
///
#[derive(Clone, Debug, Serialize)]
pub struct Pattern {
    pub(crate) pattern: String,
    #[serde(skip)]
    regex: Regex,
}

impl Pattern {
    pub fn new(name: &str) -> Result<Self> {
        match name.chars().nth_back(0) {
            Some('.') => {}
            _ => return Err(anyhow!("Must end with a '.'")),
        }
        if name.contains("**") && !name.starts_with("**") {
            return Err(anyhow!("'**' can only be used ath the start of a pattern"));
        }

        // compute regex
        let regex: Regex = if name.contains("*") {
            // tmp replace ** with § to avoid confusing the next modifications
            let q = name.replace("**", "§");

            // handle each "label" independantly
            let mut pat = String::from("^");
            let mut first = true;
            for p in q.split(".") {
                match first {
                    true => first = false,
                    false => pat.push_str(r"\."),
                }
                match p {
                    "*" => {
                        pat.push_str(r"[^\.\*]+");
                    }
                    _ => {
                        let r = p.replace("*", r"[^\.\*]*");
                        pat.push_str(&r);
                    }
                }
            }

            pat = pat.replace("§", ".*");
            pat.push('$');
            Regex::new(&pat)?
        } else {
            Regex::new(&name.replace(".", r"\."))?
        };
        Ok(Self {
            pattern: name.to_string(),
            regex,
        })
    }

    /// Test if the Name "contains" another Name (i.e. is a superset of it)
    pub fn contains(&self, what: &str) -> bool {
        self.name_contains(what)
    }

    fn name_contains(&self, other: &str) -> bool {
        if other.contains("*") {
            if self.pattern.contains("*") {
                if other.contains("**") {
                    if self.pattern.starts_with("**") {
                        self.name_contains(&other[1..])
                    } else {
                        false
                    }
                } else {
                    let rd = other.replace("*", "abc");
                    self.regex.is_match(&rd)
                }
            } else {
                false
            }
        } else if self.pattern.contains("*") {
            self.regex.is_match(other)
        } else {
            other == self.pattern
        }
    }

    pub fn without_trailing_dot(&self) -> &str {
        &self.pattern[..self.pattern.len() - 1]
    }
}

impl FromStr for Pattern {
    type Err = Error;

    fn from_str(s: &str) -> std::result::Result<Self, Self::Err> {
        Self::new(s)
    }
}

impl Display for Pattern {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.pattern)
    }
}

impl PartialEq for Pattern {
    fn eq(&self, other: &Self) -> bool {
        self.pattern == other.pattern
    }
}

impl Eq for Pattern {}

impl Hash for Pattern {
    fn hash<H: std::hash::Hasher>(&self, state: &mut H) {
        self.pattern.hash(state);
    }
}

impl<'de> Deserialize<'de> for Pattern {
    fn deserialize<D>(deserializer: D) -> std::result::Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let s = String::deserialize(deserializer)?;
        Self::from_str(&s).map_err(serde::de::Error::custom)
    }
}
