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

use std::path::{Path, PathBuf};
use http_body_util::{combinators::BoxBody, BodyExt, Empty, Full};
use anyhow::{anyhow, bail, Result};
use hyper::HeaderMap;
use std::net::Ipv4Addr;
use tokio::{fs::File, io::AsyncReadExt, io::AsyncWriteExt};
use tokio::time::{Duration, sleep};
use tokio::process::Command;
use tokio::net::UnixStream;
use hyper::header::{HeaderValue, CONTENT_TYPE};
use hyper::{body::Bytes, Response, StatusCode, Request, Method, Uri};
use padsi::trace::{error, warn, info};
use padsi::{net::EndPoint};
use padsi::notifications::WebRedirNotification;
use crate::config::GlobalConfig;

pub fn empty() -> BoxBody<Bytes, hyper::Error> {
    Empty::<Bytes>::new()
        .map_err(|never| match never {})
        .boxed()
}

pub fn full<T: Into<Bytes>>(chunk: T) -> BoxBody<Bytes, hyper::Error> {
    Full::new(chunk.into())
        .map_err(|never| match never {})
        .boxed()
}

pub async fn load_file_contents(filename:&PathBuf) -> Result<Vec<u8>> {
    let mut file =File::open(filename).await?;
    let mut contents = vec![];
    file.read_to_end(&mut contents).await?;
    Ok(contents)
}

pub fn parse_ipv4(addr: &str) -> Result<Ipv4Addr> {
    match addr.parse::<Ipv4Addr>() {
        Ok(i) => Ok(i),
        Err(_) => {
            error!("Invalid address '{}'", addr);
            bail!("Invalid address '{}'", addr)
        }
    }
}

pub async fn ensure_ip_address_present(ip:Ipv4Addr) -> Result<()> {
    // ignore this verification if ip is 0.0.0.0
    if ip==Ipv4Addr::new(0, 0, 0, 0) {
        return Ok(())
    }
    let mut cmde=Command::new("ip");
    cmde.arg("a");
    let ip_str=ip.to_string();
    let mut slept:u16=0;
    while slept<60000 {
        let res=String::from_utf8(cmde.output().await?.stdout)?;
        for line in res.lines() {
            let tline=line.trim();
            if tline.starts_with("inet ") {
                let parts=tline.split(" ").filter(|s| s.starts_with(&ip_str)).collect::<Vec<&str>>();
                if parts.len()==1 {
                    return Ok(())
                }
            }
        }
        sleep(Duration::from_millis(500)).await;
        slept+=500;
        info!("Waited a bit, try to see if there is a network inferface with the '{}' IP address", ip);
    }
    Err(anyhow!("Could not find any network interface with the '{}' IP address", ip))
}

pub async fn static_file_response(root_path: &Path, rel_filename: Option<&str>, content_type:Option<&str>) -> Result<Response<BoxBody<Bytes, hyper::Error>>, hyper::Error> {
    let mut path=root_path.to_path_buf();
    if let Some(fname)=rel_filename {
        path.push(fname);
    }

    match load_file_contents(&path).await {
        Ok(contents) => {
            let mut resp = Response::new(full(contents));
            *resp.status_mut() = StatusCode::OK;
            if let Some(ct)=content_type {
                resp.headers_mut().append(CONTENT_TYPE, HeaderValue::from_str(ct).unwrap());
            }
            Ok(resp)
        },
        Err(err) => {
            error!("Failed to load static file '{}': {}", path.display(), err);
            let mut resp = Response::new(empty());
            *resp.status_mut() = StatusCode::NOT_FOUND;
            Ok(resp)
        }
    }
}

///
///  Get the host:port of a request
///
pub fn get_host_port(uri: &Uri) -> Option<String> {
    match uri.authority() {
        Some(auth) => {
            match auth.port_u16() {
                Some (p) => Some(format!("{}:{}", auth.host(), p)),
                None => Some(String::from(auth.host()))
            }
        }
        None => None
    }
}

///
/// Get the host header
///
pub fn get_host_from_headers(req: &Request<hyper::body::Incoming>) -> Option<&str>{
    match req.headers().get("host") {
        Some(host) => {
            match host.to_str() {
                Ok(host) => Some(host),
                Err(_) => None
            }
        }
        None => None
    }
}

///
/// Create an Endpoint from the request.
/// Will be TCP and contain exactly one host part and one port number
///
pub fn get_endpoint_from_request(req: &Request<hyper::body::Incoming>) -> Result<EndPoint> {
    let req_host=req.uri().host();
    let req_port=match req.uri().port_u16() {
        Some(p) => p,
        None => {
            match req.uri().scheme_str() {
                Some(sc) => {
                    match sc {
                        "http" => 80_u16,
                        "https" => 443_u16,
                        _ => {
                            warn!("Unhandled '{}' scheme", sc);
                            return Err(anyhow!("Unhandled '{}' scheme", sc))
                        }
                    }
                },
                None => {
                    warn!("No URI scheme or port number specified");
                    return Err(anyhow!("No URI scheme or port number specified"))
                }
            }
        }
    };

    match req_host {
        None => {
            warn!("No host specified");
            Err(anyhow!("No host specified"))
        },
        Some(req_host) => match EndPoint::new_from_req(req_host, req_port) {
            Ok (ep) => Ok(ep),
            Err (err) => Err(anyhow!("Invalid host '{}' or port '{}' ({})", req_host, req_port, err.to_string()))
        }
    }
}

///
/// Try to detect the web browser being used from HTTP headers
///
fn detect_browser(headers: &HeaderMap) -> Option<&'static str> {
    // refer to https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Sec-CH-UA
    if let Some(uah)=headers.get("Sec-CH-UA") {
        if let Ok(uah)=uah.to_str() {
            let parts=uah.split(",")
                .map(|part| part.trim())
                .map(|part| part.split_once(";"))
                .filter(|item| item.is_some())
                .map(|item| {
                    let (v, _)=item.unwrap();
                    v
                })
                .collect::<Vec<&str>>();
            for item in parts {
                match item {
                    "Google Chrome" => {return Some("chrome")}
                    "Chromium" => {return Some("chromium")}
                    "Microsoft Edge" => {return Some("edge")}
                    "Opera" => {return Some("opera")}
                    _ => {}
                }
            }
        }
    }
    if let Some(ua)=headers.get("user-agent") {
        if let Ok(ua)=ua.to_str() {
            if ua.contains("Firefox") {
                return Some("firefox")
            }
        }
    }
    None
}

///
/// Provides access to web redirection's resources in the /padsi-redi directory
///
pub async fn response_for_web_redir_resource(req: &Request<hyper::body::Incoming>, port:u16, conf: &GlobalConfig) ->
    Option<Result<Response<BoxBody<Bytes, hyper::Error>>, hyper::Error>> {
    if req.method() == Method::GET {
        let path=req.uri().path();
        // static resources are in /padsi-redir
        if let Some(npath) = path.strip_prefix("/padsi-redir/") {
            let mut content_type:Option<&str>=None;
            if npath.ends_with(".png") {
                content_type=Some("image/png")
            }
            else if npath.ends_with(".html") {
                content_type=Some("text/html; charset=utf-8");
            }
            return Some(static_file_response(&conf.webroot, Some(npath), content_type).await);
        }
        else {
            let resp=static_file_response(&conf.webroot, Some("notice.html"),
                Some("text/html; charset=utf-8")).await;

            // send a notification if browser can be detected and if request comes from a user and not the browser itself
            if req.headers().get("referer").is_some() && let Some(browser)=detect_browser(req.headers()) {
                if let Some(host)=get_host_from_headers(req) && req.uri().path().len()<100 {
                    let path=if req.uri().path().starts_with("/") {
                        String::from(req.uri().path())
                    } else {
                        format!("/{}", req.uri().path())
                    };
                    let url=match port {
                        80 | 8080 => format!("http://{}{}", host, path),
                        _ => format!("https://{}{}", host, path),
                    };
                    let notification=WebRedirNotification{
                        browser: String::from(browser),
                        url: url
                    };
                    tokio::spawn(async move {
                        let server_path="/bubble/run/padsi-notify.sock";
                        match UnixStream::connect(&server_path).await {
                            Ok(mut server_stream) => {
                                let data=serde_json::to_string(&notification).unwrap();
                                if let Err(e) = server_stream.write_all(data.as_bytes()).await {
                                    error!("Failed to forward message to server: {}", e);
                                }
                            },
                            Err(e) => {
                                error!("Failed to connect to notification server: {}", e);
                            }
                        }
                    });
                }
            }
            return Some(resp);
        }
    }
    None
}
