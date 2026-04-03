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
//! Small module to log events to a file using the tracing crate
//!

use std::ffi::CString;
use std::io::IsTerminal;
use tracing;
use tracing_subscriber;
use anyhow::Result;
use tracing_subscriber::{Registry, prelude::*};

pub use tracing::{info, warn, error, debug, trace, span, Span, Level, Instrument};
pub use tracing_subscriber::filter::LevelFilter;

///
/// Trace configuration
///
pub struct TraceConfig<'a> {
    directory: &'a str,
    file_prefix: &'a str,
    program_id: &'a str,
    with_stdout_output: bool,
    file_level: tracing_subscriber::filter::LevelFilter,
    syslog_level: tracing_subscriber::filter::LevelFilter
}

impl <'a> TraceConfig<'a> {
    pub fn new(directory: &'a str, file_prefix:&'a str) -> Self {
        Self { directory , file_prefix, program_id:file_prefix, with_stdout_output: true,
            file_level: tracing_subscriber::filter::LevelFilter::INFO,
            syslog_level: tracing_subscriber::filter::LevelFilter::INFO
        }
    }

    /// Define a program ID different from the logs file prefix
    pub fn with_program_id(self, program_id: &'a str) -> Self {
        Self{program_id, ..self}
    }

    pub fn with_stdout_output(self, with_stdout_output:bool) -> Self {
        Self{with_stdout_output, ..self}
    }

    pub fn with_file_level(self, file_level: tracing_subscriber::filter::LevelFilter) -> Self {
        Self{file_level, ..self}
    }

    pub fn with_syslog_level(self, syslog_level: tracing_subscriber::filter::LevelFilter) -> Self {
        Self{syslog_level, ..self}
    }
}

///
/// Guard variable which must not be dropped until the end of the logging actions
pub struct TraceGuard {
    _guard: tracing_appender::non_blocking::WorkerGuard
}

///
/// Set up tracing for a whole program:
/// - prints events to stdout if stdout is a TTY and with_stdout_output is true
/// - records events as JSON lines in files named `<directory>/<file_prefix>....json`,
/// rotated every hour.
///
/// The returned object should be considered as static for the remainer of the program
/// otherwise JSON logs will not be recorded anymore
///
pub fn tracing_setup_json(config: &TraceConfig) -> Result<TraceGuard>{
    // stdout output if TTY and with_stdout_output
    let stdout_log = match config.with_stdout_output {
        true => match std::io::stdout().is_terminal() {
            true => Some(tracing_subscriber::fmt::layer()),
            false => None
        },
        false => None
    };

    // JSON lines file append
    let file_appender = tracing_appender::rolling::Builder::new()
        .filename_prefix(config.file_prefix)
        .filename_suffix("json")
        .rotation(tracing_appender::rolling::Rotation::DAILY)
        .max_log_files(14)
        .build(config.directory)?;
    let (non_blocking, guard) = tracing_appender::non_blocking(file_appender);

    let format = tracing_subscriber::fmt::format().json()
        .flatten_event(true)
        .with_current_span(false)
        .with_span_list(true)
        .with_target(false);

    let file_log = tracing_subscriber::fmt::layer()
        .json()
        .event_format(format)
        .with_writer(non_blocking)
        .with_filter(config.file_level);

    // syslog ouput
    let identity_s=CString::new(config.program_id)?;
    let b = Box::new(identity_s);
    let c=Box::leak(b);
    let identity=c.as_c_str();
    let (options, facility) = Default::default();
    let syslog = syslog_tracing::Syslog::new(identity, options, facility).unwrap();
    let syslog_log = tracing_subscriber::fmt::layer()
        .with_writer(syslog)
        .with_filter(config.syslog_level); // only get events tagged as syslog events

    // define subscriber
    let subscriber = Registry::default()
        .with(stdout_log)
        .with(file_log)
        .with(syslog_log);

    // set global subscriber
    tracing::subscriber::set_global_default(subscriber)?;
    Ok(TraceGuard{_guard: guard})
}
