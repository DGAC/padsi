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

use std::sync::Arc;
use std::sync::Mutex;
use std::time::Duration;
use std::env;
use tokio::time::sleep;

use actix_web::{App, HttpServer, web};
use anyhow::Result;

use padsi::trace::{LevelFilter, TraceConfig, info, error, tracing_setup_json};

mod agent;
mod api;
mod config;
mod task;
#[cfg(target_os = "windows")]
mod windows;
#[cfg(target_os = "linux")]
mod linux;

use crate::agent::OsAgent;
use crate::config::VMUsage;

#[cfg(target_os = "linux")]
use crate::linux::{PlatformAgent, log_dir};
#[cfg(target_os = "windows")]
use crate::windows::{PlatformAgent, log_dir};

const ADMIN_PORT: u16 = 12;

fn system_setup() -> Result<PlatformAgent> {
    let mut agent = { PlatformAgent::new()? };
    if agent.config().usage == VMUsage::RUN {
        agent.mount_shared_dirs()?;
    }
    agent.run_boot_script()?;
    Ok(agent)
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    // init logging
    let log_dir=match env::var("LOG_DIR") {
        Ok(v) => String::from(v),
        Err(_) => log_dir()
    };

    println!("Logging to directory '{}'", log_dir);
    let trace_conf= TraceConfig::new(&log_dir, "padsi-vm-agent")
        .with_stdout_output(true)
        .with_file_level(LevelFilter::INFO)
        .with_syslog_level(LevelFilter::WARN);
    trace_conf.check_dir().expect(&format!("Could not create directory {}", trace_conf.directory()));
    let _t=tracing_setup_json(&trace_conf).expect("Failed to initialize logging");

    info!("System setup");
    let agent: PlatformAgent = match system_setup() {
        Ok(a) => a,
        Err(err) => {
            error!("Failed to setup system: {}", err.to_string());
            panic!("Failed to setup system")
        }
    };
    let shared_agent: Arc<Mutex<PlatformAgent>>=Arc::new(Mutex::new(agent));

    info!("Listening on port {}", ADMIN_PORT);
    tokio::spawn(reap_tasks(shared_agent.clone()));
    HttpServer::new(move || {
        App::new()
            .app_data(web::Data::new(shared_agent.clone()))
            .service(api::post_shutdown)
            .service(api::post_task)
            .service(api::get_task)
            .service(api::get_tasks)
            .service(api::get_status)
            .service(api::get_file)
            .service(api::post_file)
    })
    .bind(("0.0.0.0", ADMIN_PORT))?
    .run().await
}

async fn reap_tasks(pf_agent: Arc<Mutex<PlatformAgent>>) {
    loop {
        sleep(Duration::from_secs(2)).await;
        let agent_guard=pf_agent.lock().unwrap();
        agent_guard.reap_tasks();
    }
}
