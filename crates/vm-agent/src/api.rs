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
use std::sync::Arc;
use std::sync::Mutex;
use actix_web::{Responder, HttpRequest, Result as WebResult, Error as WebError, error, get, post, web};
use actix_files::NamedFile;
use actix_multipart::form::{json::Json as MpJson, tempfile::TempFile, MultipartForm};
use serde::{Deserialize, Serialize};
use base64::prelude::*;
#[cfg(target_os = "linux")]
use users::os::unix::UserExt;
#[cfg(target_os = "linux")]
use users::get_user_by_name;
use tokio::fs as fs;
use std::os::unix::fs::chown;

use crate::agent::OsAgent;
use crate::config::AgentConfig;

#[cfg(target_os = "linux")]
use crate::linux::PlatformAgent;

#[post("/shutdown")]
async fn post_shutdown(data: web::Data<Arc<Mutex<PlatformAgent>>>) -> WebResult<impl Responder> {
    let agent_guard=data.get_ref().lock().unwrap();
    if let Err(err) = agent_guard.shutdown() {
        Err(error::ErrorInternalServerError(err))
    }
    else {
        Ok(web::Bytes::new())
    }
}

#[derive(Deserialize)]
struct TaskArgs {
    args: Vec<String>,
    with_status: bool
}

#[derive(Deserialize)]
struct FormatOptions {
    as_text: Option<bool>
}

#[derive(Serialize)]
struct TaskResult {
    code: Option<i32>,
    stdout: Option<String>,
    stderr: Option<String>
}

#[post("/task")]
async fn post_task(
    data: web::Data<Arc<Mutex<PlatformAgent>>>,
    query: web::Json<TaskArgs>
) -> WebResult<impl Responder> {
    let agent_guard=data.get_ref().lock().unwrap();
    match agent_guard.new_task(&query.args, query.with_status) {
        Ok(tid) => {
            Ok(web::Json(tid))
        },
        Err(err) => Err(error::ErrorInternalServerError(err))
    }
}

#[get("/task/{id}")]
async fn get_task(
    data: web::Data<Arc<Mutex<PlatformAgent>>>,
    query: web::Query<FormatOptions>,
    path: web::Path<u64>
) -> WebResult<impl Responder> {
    let id=path.into_inner();
    let mut agent_guard=data.get_ref().lock().unwrap();
    let res=match agent_guard.task_output(id) {
        Ok(Some(output)) => {
            let as_text=query.as_text.unwrap_or(true);
            if as_text {
                TaskResult{
                    code: Some(output.status.code().unwrap_or_else(|| 0)),
                    stdout: Some(String::from_utf8_lossy(&output.stdout[..]).into_owned()),
                    stderr: Some(String::from_utf8_lossy(&output.stderr[..]).into_owned())
                }
            }
            else {
                TaskResult{
                    code: Some(output.status.code().unwrap_or_else(|| 0)),
                    stdout: Some(BASE64_STANDARD.encode(output.stdout)),
                    stderr: Some(BASE64_STANDARD.encode(output.stderr))
                }
            }
        },
        Ok(None) => {
            TaskResult { code: None, stdout: None, stderr: None }
        },
        Err(err) => return Err(error::ErrorBadRequest(err))
    };
    Ok(web::Json(res))
}

#[get("/tasks")]
async fn get_tasks(data: web::Data<Arc<Mutex<PlatformAgent>>>) -> impl Responder {
    let agent_guard=data.get_ref().lock().unwrap();
    let tids=agent_guard.tasks();
    web::Json(tids)
}

#[derive(Serialize)]
enum StatusResp {
    UserSessionOpened(bool),
    Config(AgentConfig)
}

#[get("/status/{context}")]
async fn get_status(
    data: web::Data<Arc<Mutex<PlatformAgent>>>,
    path: web::Path<String>
) -> WebResult<impl Responder> {
    let context=path.into_inner();
    let agent_guard = data.get_ref().lock().unwrap();
    let resp = match context.as_str() {
        "config" => StatusResp::Config(agent_guard.config().clone()),
        "user-session-opened" => StatusResp::UserSessionOpened(agent_guard.user_session_opened()),
        _ => {
            return Err(error::ErrorBadRequest(format!("invalid '{}' context", context)));
        }
    };
    Ok(web::Json(resp))
}

#[get("/file/{filename:.*}")]
async fn get_file(
    data: web::Data<Arc<Mutex<PlatformAgent>>>,
    req: HttpRequest
) -> Result<NamedFile, WebError> {
    let mut path: std::path::PathBuf = req.match_info().query("filename").parse().unwrap();
    let agent_guard = data.get_ref().lock().unwrap();
    let user=get_user_by_name(&agent_guard.config().user_name);
    if user.is_none() {
        return Err(error::ErrorInternalServerError(format!("user '{}' does not exist", &agent_guard.config().user_name)))
    }
    let user=user.unwrap();
    if path.is_absolute() {
        if ! path.starts_with(user.home_dir()) {
            return Err(error::ErrorForbidden(format!("access to '{}' is not allowed", path.display())))
        }
    }
    else {
        let mut npath=PathBuf::from(user.home_dir());
        npath.push(&path);
        path=npath;
    }
    let file = NamedFile::open(path)?;
    Ok(file.use_last_modified(true))
}

#[derive(Debug, Deserialize)]
struct Metadata {
    name: Option<String>,
}

#[derive(Debug, MultipartForm)]
struct UploadForm {
    #[multipart(limit = "100MB")]
    file: TempFile,
    meta: MpJson<Metadata>,
}

#[post("/file")]
pub async fn post_file(
    data: web::Data<Arc<Mutex<PlatformAgent>>>,
    form: MultipartForm<UploadForm>
) -> WebResult<impl Responder> {
    // Uploaded to file: form.file.file_name, size form.file.size
    // Requested file name: form.meta.name
    let agent_guard = data.get_ref().lock().unwrap();
    let user=get_user_by_name(&agent_guard.config().user_name);
    if user.is_none() {
        return Err(error::ErrorInternalServerError(format!("user '{}' does not exist", &agent_guard.config().user_name)))
    }
    let user=user.unwrap();

    let file_path= match &form.meta.name {
        Some(x) => {
            let mut p=PathBuf::from(x);
            if p.is_absolute() {
                if ! p.starts_with(user.home_dir()) {
                    return Err(error::ErrorForbidden(format!("access to '{}' is not allowed", p.display())))
                }
            }
            else {
                let mut npath=PathBuf::from(user.home_dir());
                npath.push(&x);
                p=npath
            }
            p
        },
        None => PathBuf::from(user.home_dir())
    };

    if let Err(err) = fs::copy(form.file.file.path(), &file_path).await {
        return Err(error::ErrorInternalServerError(err.to_string()))
    }
    match chown(&file_path, Some(agent_guard.config().user_id), Some(agent_guard.config().group_id)) {
        Ok(_) => Ok(format!("Ok")),
        Err(err) => Err(error::ErrorInternalServerError(err.to_string())),
    }
}
