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

use std::path::PathBuf;
use std::{env, process::exit};
use std::sync::Arc;
use std::io;
use padsi::pki::{CA, PKCS12};
use tokio::task::{JoinSet};

use anyhow::Result;
use padsi::trace::{LevelFilter, TraceConfig, error, info, tracing_setup_json};

mod misc;
mod config;
mod proxy;
mod web;
mod cache;
use config::GlobalConfig;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // init logging
    let mut log_dir=String::from("/var/log");
    if let Ok(v)=env::var("LOG_DIR") {
        log_dir=String::from(v)
    }
    println!("Logging to directory '{}'", log_dir);
    let trace_conf= TraceConfig::new(&log_dir, "web-infra")
        .with_stdout_output(false)
        .with_file_level(LevelFilter::INFO)
        .with_syslog_level(LevelFilter::WARN);
    let _t=tracing_setup_json(&trace_conf).expect("Failed to initialize logging");

    // parse command line arguments
    let args: Vec<String> = env::args().collect();
    let (pkcs12_file, pkcs12_pass)=match args.len() {
        2 => {(None, None)},
        3 => {
            // a PKCS#12 file is provided
            let mut password = String::new();
            io::stdin()
                .read_line(&mut password)
                .expect("Failed to read password");
            password=password.trim().into();
            (Some(args[2].clone()), Some(password))
        },
        _ => {
            // if a PKCS#12 file is provided, then the password is expected
            // to be passed via stdin
            println!("Usage: {} <config.json file> [<PKCS#12 file>]", args[0]);
            exit(1);
        }
    };

    // load config file
    let conf_file:PathBuf=args[1].clone().into();
    let mut conf=match GlobalConfig::from_json(conf_file) {
        Ok(c) => c,
        Err(err) => {
            error!("Error while loading configuration file '{}': {}", args[1], err);
            exit(1);
        }
    };

    // load CA is web redirector is enabled
    if conf.web_redirector.is_some() && let Some(pkcs12_file)=pkcs12_file {
        let ca=CA::from_pkcs12(PKCS12::from_file(&pkcs12_file, &pkcs12_pass.unwrap()).unwrap()).unwrap();
        info!(pem=ca.cert_pem(), "loaded CA cert");
        conf.web_redirector.as_mut().unwrap().ca=Some(ca);
    }

    // generate WPAD file
    if let Some(web_proxy_conf)=conf.web_proxy.as_mut() {
        web_proxy_conf.generate_wpad_file()?
    }

    // put conf in a Arc
    let conf=Arc::new(conf);

    // start all the tasks
    let mut handles_set = JoinSet::<()>::new();
    if conf.web_proxy.is_some() {
        if let Err(err)= crate::proxy::setup(&conf, &mut handles_set).await {
            error!("Failed to start web proxy: {}", err);
            panic!("Failed to start web proxy: {}", err)
        }
    }
    if conf.web_proxy.is_some() || conf.web_redirector.is_some() {
        if let Err(err)= crate::web::setup(&conf, &mut handles_set).await {
            error!("Failed to start WPAD/web redirector: {}", err);
            if ! env::var("NO_PRIV").is_ok() {
                panic!("Failed to start WPAD/web redirector: {}", err)
            }
        }
    }

    // Let the services run!
    handles_set.join_all().await;
    Ok(())
}
