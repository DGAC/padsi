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

use std::collections::HashMap;

use anyhow::Result;
use http_body_util::{BodyExt, Full};
use hyper::body::Bytes;
use hyper::{Method, Request, Response, StatusCode};
use nix::sys::signal::{kill, Signal};
use nix::unistd::Pid;
use serde::{Deserialize, Serialize, Deserializer};

use caps::{Capability};

use padsi::trace::error;
use crate::capabilities::parse_capability;
use crate::process::{ProcessSpec, ProcessState};
use crate::config::SharedConfig;

#[derive(Deserialize,Serialize)]
struct PidData {
    pid: u32,
}

#[derive(Serialize)]
struct ErrorResponse {
    error: String,
}

pub async fn handle_request(
    req: Request<hyper::body::Incoming>,
    config: SharedConfig,
) -> Result<Response<Full<Bytes>>, hyper::Error> {
    let path = req.uri().path();
    let segments: Vec<&str> = path.trim_start_matches('/').split('/').collect();

    match (req.method(), segments.as_slice()) {
        // GET /ping
        (&Method::GET, ["ping"]) => impl_get_ping(),
        // GET /env
        (&Method::GET, ["env"]) => impl_get_env(config).await,
        // POST /env
        (&Method::POST, ["env"]) => impl_post_env(req, config).await,
        // POST /procs
        (&Method::POST, ["procs"]) => impl_post_procs(req, config).await,
        // GET /procs
        (&Method::GET, ["procs"]) => impl_get_procs(req, config).await,
        // DELETE /procs
        (&Method::DELETE, ["procs"]) => impl_delete_procs(req, config).await,
        // PUT /procs
        (&Method::PUT, ["procs"]) => impl_put_procs(req, config).await,
        // GET /proc/<PID>
        (&Method::GET, ["proc", pid]) => proc_status_get(config, pid).await,
        // GET /property
        (&Method::GET, ["property"]) => impl_get_property(req, config).await,
        // PUT /property
        (&Method::PUT, ["property"]) => impl_put_property(req, config).await,
        // everything else
        _ => Ok(json_error(StatusCode::NOT_FOUND, "Not found")),
    }
}

fn json_response<T: Serialize>(status: StatusCode, body: &T) -> Response<Full<Bytes>> {
    let json = serde_json::to_vec_pretty(body).unwrap_or_default();
    Response::builder()
        .status(status)
        .header("Content-Type", "application/json")
        .body(Full::new(Bytes::from(json)))
        .unwrap()
}

fn json_error(status: StatusCode, msg: &str) -> Response<Full<Bytes>> {
    json_response(status, &ErrorResponse { error: msg.to_owned() })
}

fn json_none() -> Result<Response<Full<Bytes>>, hyper::Error> {
    let resp=Response::builder()
        .status(StatusCode::OK)
        .header("Content-Type", "application/json")
        .body(Full::new(Bytes::from("null")))
        .unwrap();
    Ok(resp)
}

fn impl_get_ping() -> Result<Response<Full<Bytes>>, hyper::Error> {
    json_none()
}

async fn impl_get_env(config: SharedConfig) -> Result<Response<Full<Bytes>>, hyper::Error> {
    let config = config.lock().await;
    Ok(json_response(StatusCode::OK, &config.env))
}

#[derive(Deserialize)]
struct EnvRequest {
    name: String,
    value: Option<String>
}

async fn impl_post_env(req: Request<hyper::body::Incoming>, config: SharedConfig) -> Result<Response<Full<Bytes>>, hyper::Error> {
    let body_bytes = req.collect().await?.to_bytes();
    let env_req: EnvRequest = match serde_json::from_slice(&body_bytes) {
        Ok(v) => v,
        Err(e) => return Ok(json_error(StatusCode::BAD_REQUEST, &e.to_string())),
    };

    let mut config = config.lock().await;
    match env_req.value {
        Some(v) => {
            config.env.insert(env_req.name, v);
        },
        None => {
            config.env.remove(&env_req.name);
        }
    }
    json_none()
}

#[derive(Deserialize)]
struct StartRequest {
    args: Vec<String>,
    #[serde(default)]
    ignore_status: bool,
    #[serde(default)]
    environ: Option<HashMap<String, String>>,
    #[serde(default, deserialize_with = "de_caps_list")]
    capabilities: Vec<Capability>,
    #[serde(default, alias="child-stdin")]
    stdin_file: Option<String>,
    #[serde(default, alias="child-stdout")]
    stdout_file: Option<String>,
    #[serde(default, alias="child-stderr")]
    stderr_file: Option<String>,
    #[serde(default)]
    restart: bool
}

fn de_caps_list<'de, D>(deserializer: D) -> Result<Vec<Capability>, D::Error>
where D: Deserializer<'de> {
    // Helper is needed to allow Null values to be handled as []
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum Helper {
        CSV (String),
        VString (Vec<String>), // to be removed, useless
        Null,
    }
    match Helper::deserialize(deserializer)? {
        Helper::CSV(csv) => {
            match csv.split(",")
                .map(|s| parse_capability(s))
                .collect::<Result<Vec<Capability>, _>>() {
                    Ok(r)=> Ok(r),
                    Err(e) => Err(serde::de::Error::custom(e.to_string()))
                }
        },
        Helper::VString(caps) => {
            match caps
                .iter()
                .map(|s| parse_capability(s))
                .collect::<Result<Vec<Capability>, _>>() {
                    Ok(r)=> Ok(r),
                    Err(e) => Err(serde::de::Error::custom(e.to_string()))
                }
        },
        Helper::Null => {
            let empty:Vec<Capability>=vec![];
            Ok(empty)
        }
    }
}

async fn impl_post_procs(req: Request<hyper::body::Incoming>, config: SharedConfig) -> Result<Response<Full<Bytes>>, hyper::Error> {
    let body_bytes = req.collect().await?.to_bytes();
    let start_req: StartRequest = match serde_json::from_slice(&body_bytes) {
        Ok(v) => v,
        Err(e) => return Ok(json_error(StatusCode::BAD_REQUEST, &e.to_string())),
    };

    // check capabilities are allowed
    let mut config = config.lock().await;
    for cap in &start_req.capabilities {
        if !config.capabilities.contains(&cap) {
            let msg=format!("Capability {} denied", cap);
            error!(msg);
            return Ok(json_error(StatusCode::FORBIDDEN, &msg))
        }
    }

    let pspec=ProcessSpec::new(start_req.args, &config.env, start_req.environ.as_ref(),
        start_req.capabilities, start_req.stdin_file, start_req.stdout_file, start_req.stderr_file, start_req.restart);

    match pspec.start().await {
        Ok(proc) => {
            let pid=proc.pid;
            if ! start_req.ignore_status {
                config.procs.insert(proc.pid, proc);
            }
            Ok(json_response(StatusCode::OK, &PidData { pid }))
        },
        Err(err) => Ok(json_error(StatusCode::BAD_REQUEST, &err.to_string()))
    }
}

enum FilterState {
    Any,
    Running,
    Terminated
}

#[derive(Serialize)]
struct ProcessInfo {
    pid: u32,
    args: Vec<String>,
    state: ProcessState
}

async fn impl_get_procs(req: Request<hyper::body::Incoming>, config: SharedConfig) -> Result<Response<Full<Bytes>>, hyper::Error> {
    let mut filter_state=FilterState::Any;
    if let Some(query) = req.uri().query() {
        for (k, v) in form_urlencoded::parse(query.as_bytes()) {
            if k == "state" {
                let v=v.to_lowercase();
                filter_state=match v.as_str() {
                    "running" => FilterState::Running,
                    "terminated" | "killed" => FilterState::Terminated,
                    _ => return Ok(json_error(StatusCode::BAD_REQUEST, "invalid state argument"))
                };
            }
        }
    }
    let config = config.lock().await;
    let list: Vec<ProcessInfo>=config.procs
        .values()
        .filter(|p| {
            match filter_state {
                FilterState::Running => matches!(&p.state, ProcessState::Running),
                FilterState::Terminated => ! matches!(&p.state, ProcessState::Running),
                FilterState::Any => true,
            }
        })
        .map(|p| ProcessInfo { pid: p.pid, args: p.spec.args().clone(), state: p.state.clone() })
        .collect();
    Ok(json_response(StatusCode::OK, &list))
}

// send a signal to a process after making sure that it is managed
async fn send_signal(config: SharedConfig, pid: u32, s:Signal) -> Result<Response<Full<Bytes>>, hyper::Error> {
    let config = config.lock().await;
    let proc=match config.procs.get(&pid) {
        Some(p) => p,
        None => return Ok(json_error(StatusCode::BAD_REQUEST, "No such process")),
    };
    match kill(Pid::from_raw(proc.pid as i32), s) {
        Ok(_) => json_none(),
        Err(e) => Ok(json_error(StatusCode::INTERNAL_SERVER_ERROR, &e.to_string()))
    }
}

async fn impl_delete_procs(req: Request<hyper::body::Incoming>, config: SharedConfig) -> Result<Response<Full<Bytes>>, hyper::Error> {
    let body_bytes = req.collect().await?.to_bytes();
    let stop_req: PidData = match serde_json::from_slice(&body_bytes) {
        Ok(v) => v,
        Err(e) => return Ok(json_error(StatusCode::BAD_REQUEST, &e.to_string())),
    };
    send_signal(config, stop_req.pid, Signal::SIGKILL).await
}

#[derive(Deserialize)]
struct DeleteRequest {
    pid: u32,
    state: String
}

async fn impl_put_procs(req: Request<hyper::body::Incoming>, config: SharedConfig) -> Result<Response<Full<Bytes>>, hyper::Error> {
    let body_bytes = req.collect().await?.to_bytes();
    let del_req: DeleteRequest = match serde_json::from_slice(&body_bytes) {
        Ok(v) => v,
        Err(e) => return Ok(json_error(StatusCode::BAD_REQUEST, &e.to_string())),
    };
    match del_req.state.as_str() {
        "suspend" => send_signal(config, del_req.pid, Signal::SIGSTOP).await,
        "resume" => send_signal(config, del_req.pid, Signal::SIGCONT).await,
        any => return Ok(json_error(StatusCode::BAD_REQUEST, &format!("invalid state '{}'", any))),
    }
}

async fn proc_status_get(config: SharedConfig, pid_str:&str) -> Result<Response<Full<Bytes>>, hyper::Error> {
    match pid_str.parse() {
        Ok(pid) => {
            let mut config = config.lock().await;
            match config.procs.get(&pid) {
                Some(p)=> {
                    match p.state {
                        ProcessState::Exited { code } => {
                            config.procs.remove(&pid);
                            Ok(json_response(StatusCode::OK, &code))
                        },
                        ProcessState::Killed { signal } => {
                            config.procs.remove(&pid);
                            Ok(json_response(StatusCode::OK, &(signal+128i32)))
                        },
                        _ => {
                            let r:Option<u32>=None;
                            Ok(json_response(StatusCode::OK, &r))
                        }
                    }
                },
                None=>Ok(json_error(StatusCode::BAD_REQUEST, "process not found"))
            }
        },
        Err(_) => Ok(json_error(StatusCode::BAD_REQUEST, "invalid pid argument"))
    }
}

#[derive(Serialize, Deserialize, Debug)]
#[serde(untagged)]
enum PropValue {
    Bool(bool),
    Other // future extension
}

#[derive(Deserialize)]
struct PropRequest {
    name: String,
    value: PropValue
}

async fn impl_get_property(req: Request<hyper::body::Incoming>, config: SharedConfig) -> Result<Response<Full<Bytes>>, hyper::Error> {
    let config = config.lock().await;
    if let Some(query) = req.uri().query() {
        for (k, v) in form_urlencoded::parse(query.as_bytes()) {
            if k == "name" {
                match v.as_ref() {
                    "auto-stop" => return Ok(json_response(StatusCode::OK, &PropValue::Bool(config.auto_stop))),
                    other => return Ok(json_error(StatusCode::BAD_REQUEST, &format!("unknown property '{}'", other)))
                };
            }
        }
    }
    Ok(json_error(StatusCode::BAD_REQUEST, "no property specified"))
}

async fn impl_put_property(req: Request<hyper::body::Incoming>, config: SharedConfig) -> Result<Response<Full<Bytes>>, hyper::Error> {
    let body_bytes = req.collect().await?.to_bytes();
    let prop_req: PropRequest = match serde_json::from_slice(&body_bytes) {
        Ok(v) => v,
        Err(e) => return Ok(json_error(StatusCode::BAD_REQUEST, &e.to_string())),
    };
    match prop_req.name.as_str() {
        "auto-stop" => {
            if let PropValue::Bool(v)=prop_req.value {
                let mut config = config.lock().await;
                config.auto_stop=v;
                return json_none()
            }
            return Ok(json_error(StatusCode::BAD_REQUEST, &format!("invalid value '{:?}' for the 'auto-stop' property", prop_req.value)))
        },
        other => return Ok(json_error(StatusCode::BAD_REQUEST, &format!("unknown property '{}'", other))),
    }
}
