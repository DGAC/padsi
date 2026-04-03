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
use tokio::time::sleep;

use actix_web::{App, HttpServer, web};
use anyhow::Result;

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
use crate::linux::PlatformAgent;
#[cfg(target_os = "windows")]
use crate::windows::PlatformAgent;

const ADMIN_PORT: u16 = 1212;

fn system_setup() -> Result<PlatformAgent> {
    println!("Setting up system...");
    let agent = { PlatformAgent::new()? };
    if agent.config().usage == VMUsage::RUN {
        agent.mount_shared_dirs()?;
    }
    agent.run_boot_script()?;
    Ok(agent)
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    println!("Logging setup");
    env_logger::init();

    println!("System setup");
    let agent: PlatformAgent = system_setup().expect("Failed to setup system");
    let shared_agent: Arc<Mutex<PlatformAgent>>=Arc::new(Mutex::new(agent));

    println!("Listening on port {}", ADMIN_PORT);
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
