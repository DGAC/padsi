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

use clap::Parser;
use std::env;
use std::os::unix::fs::FileTypeExt;
use std::sync::Arc;

use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper_util::rt::TokioIo;
use tokio::net::UnixListener;
use tokio::sync::Mutex;

use padsi::trace::{LevelFilter, TraceConfig, error, info, tracing_setup_json};

mod api;
mod capabilities;
mod config;
mod process;
mod reap;

use crate::api::handle_request;
use crate::config::Config;
use crate::reap::chld_reaper_setup;

#[derive(Parser)]
#[command(author, version, about, long_about = None)]
struct Args {
    #[arg(help = "Run directory")]
    rundir: String,
    #[arg(long = "cap-add", help = "CSV list of capabilities to add")]
    caps: Option<String>,
}

#[tokio::main]
async fn main() {
    // init logging
    let mut log_dir = String::from("/var/log");
    if let Ok(v) = env::var("LOG_DIR") {
        log_dir = String::from(v)
    }
    println!("Logging to directory '{}'", log_dir);
    let trace_conf = TraceConfig::new(&log_dir, "init")
        .with_stdout_output(false)
        .with_file_level(LevelFilter::DEBUG)
        .with_syslog_level(LevelFilter::DEBUG);
    let _t = tracing_setup_json(&trace_conf).expect("Failed to initialize logging");

    let args = Args::parse();
    let socket_file = format!("{}/bubble.sock", args.rundir);
    let config = match Config::new(&socket_file, args.caps.as_deref()) {
        Ok(c) => c,
        Err(err) => {
            let msg = format!("Invalid arguments: {}", err.to_string());
            error!(msg);
            panic!("Invalid arguments")
        }
    };
    let config = Arc::new(Mutex::new(config));
    chld_reaper_setup(&config);

    if let Ok(meta) = std::fs::metadata(&socket_file) {
        if meta.file_type().is_socket() {
            std::fs::remove_file(&socket_file).expect("Failed to remove stale socket");
        }
    }

    let listener = UnixListener::bind(&socket_file).expect("Failed to bind Unix socket");
    println!("[init] listening on {socket_file}");
    info!("[init] listening on {socket_file}");

    loop {
        match listener.accept().await {
            Ok((stream, _addr)) => {
                let io = TokioIo::new(stream);
                let config = Arc::clone(&config);
                tokio::spawn(async move {
                    if let Err(e) = http1::Builder::new()
                        .serve_connection(
                            io,
                            service_fn(move |req| handle_request(req, Arc::clone(&config))),
                        )
                        .await
                    {
                        eprintln!("[init] connection error: {e}");
                    }
                });
            }
            Err(e) => eprintln!("[init] accept error: {e}"),
        }
    }
}
