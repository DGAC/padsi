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
//! Web proxy features
//!

use std::sync::Arc;
use std::net::{Ipv4Addr, SocketAddr};
use anyhow::{anyhow, Result};
use tokio::task::JoinSet;
use tokio::net::{TcpListener, TcpStream};
use tokio::io::{AsyncWriteExt, AsyncReadExt};
use hyper_util::rt::TokioIo;
type ServerBuilder = hyper::server::conn::http1::Builder;
type ClientBuilder = hyper::client::conn::http1::Builder;
use hyper::service::service_fn;
use hyper::{body, Method, Request, Response, StatusCode};
use hyper::body::{Bytes};
use hyper::header::{
    CONNECTION,
    PROXY_AUTHENTICATE,
    PROXY_AUTHORIZATION,
    TE,
    TRAILER,
    TRANSFER_ENCODING,
    UPGRADE,
};
use hyper::upgrade::Upgraded;
use http_body_util::{combinators::BoxBody, BodyExt};
use padsi::trace::{info, warn, error, debug, trace, span, Level, Instrument};

use crate::config::GlobalConfig;
use crate::misc::{empty, full, parse_ipv4, ensure_ip_address_present, response_for_web_redir_resource, get_host_port, get_endpoint_from_request};

pub async fn setup(conf: &Arc<GlobalConfig>, set: &mut JoinSet<()>) -> Result<()> {
    if conf.web_proxy.is_none() {
        error!("CODEBUG: conf.web_proxy should not be none");
        return Err(anyhow!("CODEBUG: conf.web_proxy should not be none"));
    }
    let port=conf.web_proxy.as_ref().unwrap().port();
    let ip=match parse_ipv4(conf.web_proxy.as_ref().unwrap().listening_ip()) {
        Ok(r) => r,
        Err(err) => return Err(anyhow!(err))
    };
    let proxy_addr = SocketAddr::from((ip, port));
    ensure_ip_address_present(ip).await?;
    let proxy_listener = TcpListener::bind(proxy_addr).await?;
    info!("Proxy listening on http://{}", proxy_addr);
    let conf = Arc::clone(conf);

    set.spawn(async move {
        loop {
            match proxy_listener.accept().await {
                Ok((stream, addr)) => {
                    let span=span!(Level::INFO, "connection", service_ip=?ip, service_port=port,
                        client_ip=addr.ip().to_string(), client_port=addr.port());
                    let io = TokioIo::new(stream);

                    let config = Arc::clone(&conf); // Clone Arc for each connection
                    tokio::task::spawn(async move {
                        if let Err(err) = ServerBuilder::new()
                            .preserve_header_case(true)
                            .title_case_headers(true)
                            .serve_connection(io, service_fn(|req: Request<body::Incoming>| {
                                let config = Arc::clone(&config);
                                handle_request(req, &ip, port, config).instrument(span.clone())
                            }))
                            .with_upgrades()
                            .await
                        {
                            span.in_scope(|| {
                                warn!("Failed to serve connection to proxy: {:?}", err)
                            })
                        }
                    });
                },
                Err(err) => {
                    error!("Can't accept connections on {}: {}", proxy_addr, err);
                    break
                }
            }
        };
    });
    Ok(())
}

async fn handle_request(mut req: Request<hyper::body::Incoming>, _ip: &Ipv4Addr, port:u16, conf:Arc<GlobalConfig>)
    -> Result<Response<BoxBody<Bytes, hyper::Error>>, hyper::Error> {
    let span=span!(Level::INFO, "handle_request", http_method=?req.method(), uri=?req.uri(), http_version=?req.version());

    let ep=match span.in_scope(|| get_endpoint_from_request(&req)) {
        Ok(ep) => ep,
        Err(err) => {
            warn!("Invalid request: '{:?}'", err.to_string());
            let mut resp = Response::new(empty());
            *resp.status_mut() = StatusCode::BAD_REQUEST;
            return Ok(resp)
        }
    };

    if Method::CONNECT == req.method() { // for HTTPS connections
        // Received an HTTP request like:
        // ```
        // CONNECT www.domain.com:443 HTTP/1.1
        // Host: www.domain.com:443
        // Proxy-Connection: Keep-Alive
        // ```
        //
        // When HTTP method is CONNECT, return an empty body
        // then eventually upgrade the connection and talk a new protocol.
        //
        // Note: only after client received an empty body with STATUS_OK can the
        // connection be upgraded, so we can't return a response inside
        // `on_upgrade` future.
        if let Some(addr) = get_host_port(req.uri()) {
            let proxy_conf=conf.web_proxy.as_ref().unwrap();
            match proxy_conf.get_hext_hop_addr(&ep) {
                Ok(next_hop) => {
                    tokio::task::spawn(async move {
                        match hyper::upgrade::on(req).await {
                            Ok(upgraded) => {
                                if let Err(err) = tunnel(upgraded, &next_hop, &addr).instrument(span.clone()).await {
                                    span.in_scope(|| {
                                        match next_hop {
                                            Some(_s) => warn!(up_proxy_blocked=ep.to_string(), "{}", err.to_string()),
                                            None => warn!(up_conn_blocked=ep.to_string(), "{}", err.to_string()),
                                        }
                                    })
                                };
                            }
                            Err(e) => {
                                span.in_scope(|| {
                                    warn!("upgrade error: {}", e)
                                })
                            }
                        }
                    });
                    Ok(Response::new(empty()))
                },
                Err(_err) => {
                    // do a web redirection if web redirection is enabled
                    if conf.web_redirector.is_some() {
                        let span=span!(Level::TRACE, "Access denied, using web redirection");
                        tokio::task::spawn(async move {
                            match hyper::upgrade::on(req).await {
                                Ok(upgraded) => {
                                    if let Err(err) = tunnel(upgraded, &None, &addr).instrument(span.clone()).await {
                                        span.in_scope(|| {
                                            warn!("{}", err.to_string())
                                        })
                                    };
                                },
                                Err(e) => span.in_scope(|| {
                                    warn!("upgrade error: {}", e)
                                })
                            }
                        });
                        Ok(Response::new(empty()))
                    }
                    else {
                        span.in_scope(|| {
                            trace!("Access denied, returning 403")
                        });
                        let mut resp = Response::new(empty());
                        *resp.status_mut() = StatusCode::FORBIDDEN;
                        Ok(resp)
                    }
                }
            }
        } else {
            span.in_scope(|| {
                warn!("Invalid request URI: '{:?}'", req.uri())
            });
            let mut resp = Response::new(empty());
            *resp.status_mut() = StatusCode::BAD_REQUEST;
            Ok(resp)
        }
    }
    else { // for HTTP connections, forward request
        let proxy_conf=conf.web_proxy.as_ref().unwrap();
        match proxy_conf.get_hext_hop_addr(&ep) {
            Ok(next_hop) => {
                // determine the actual address of the next hop or the destination server itself
                let addr=match &next_hop {
                    Some(p) => {
                        span.in_scope(|| {
                            trace!("Forwarding request to proxy {}", p)
                        });
                        p.clone()
                    }, // send request to specified proxy
                    None => {
                        // send request to the specified Web server
                        let host = match req.uri().host() {
                            Some(h) => h,
                            None => {
                                span.in_scope(|| {
                                    warn!("No host specified")
                                });
                                let mut resp = Response::new(full("No host specified"));
                                *resp.status_mut() = StatusCode::BAD_REQUEST;
                                return Ok(resp);
                            }
                        };
                        let port = req.uri().port_u16().unwrap_or(80);
                        span.in_scope(|| {
                            trace!("Sending request to web server {}:{}", host, port)
                        });
                        format!("{}:{}", host, port)
                    }
                };

                remove_hop_headers(req.headers_mut());

                // open the connection and "forward it"
                match TcpStream::connect(addr).instrument(span.clone()).await {
                    Ok(stream) => {
                        let io = TokioIo::new(stream);
                        let (mut sender, conn) = ClientBuilder::new()
                            .preserve_header_case(true)
                            .title_case_headers(true)
                            .handshake(io)
                            .await?;
                        debug!("starting HTTP connection");
                        let span2=span.clone();
                        tokio::task::spawn(async move {
                            if let Err(err) = conn.await {
                                span2.in_scope(|| {
                                    warn!(up_conn_blocked=ep.to_string(), "HTTP connection failed: {:?}", err)
                                })
                            }
                            else {
                                span2.in_scope(|| {
                                    debug!("HTTP connection Ok")
                                })
                            }
                        });
                        match next_hop {
                            Some(_) => {
                                debug!(uri=format!("{}", req.uri()), "Sending request to proxy");
                                let resp = sender.send_request(req).await?;
                                Ok(resp.map(|b| b.boxed()))
                            },
                            None => {
                                let path_query = req
                                    .uri()
                                    .path_and_query()
                                    .map(|pq| pq.as_str())
                                    .unwrap_or("/");
                                match path_query.parse() {
                                    Ok(new_uri) => {
                                        debug!(uri=format!("{}", new_uri), "Sending request directly to server");
                                        *req.uri_mut()=new_uri;
                                        let resp = sender.send_request(req).await?;
                                        Ok(resp.map(|b| b.boxed()))
                                    },
                                    Err(err) => {
                                        error!("Should not happen: got an invalid URI extracted from the request: {}", err.to_string());
                                        let mut resp = Response::new(empty());
                                        *resp.status_mut() = StatusCode::BAD_REQUEST;
                                        Ok(resp)
                                    }
                                }
                            }
                        }
                    },
                    Err(err) => {
                        span.in_scope(|| {
                            warn!(up_conn_blocked=ep.to_string(), "TCP connection failed: {}", err.to_string())
                        });
                        let mut resp = Response::new(empty());
                        *resp.status_mut() = StatusCode::REQUEST_TIMEOUT;
                        Ok(resp)
                    }
                }
            },
            Err(_err) => {
                if let Some(r) = response_for_web_redir_resource(&req, port, conf.as_ref())
                    .instrument(span.clone())
                    .await {
                    return r
                }
                let mut resp = Response::new(empty());
                *resp.status_mut() = StatusCode::FORBIDDEN;
                Ok(resp)
            }
        }
    }
}

fn remove_hop_headers(headers: &mut hyper::HeaderMap) {
    headers.remove(CONNECTION);
    headers.remove(PROXY_AUTHENTICATE);
    headers.remove(PROXY_AUTHORIZATION);
    headers.remove(TE);
    headers.remove(TRAILER);
    headers.remove(TRANSFER_ENCODING);
    headers.remove(UPGRADE);
}

// Create a TCP connection to host:port, build a tunnel between the connection and
// the upgraded connection
async fn tunnel(upgraded: Upgraded, remote_proxy:&Option<String>, addr: &str) -> Result<()> {
    let span=span!(Level::INFO, "tunnel");

    let mut remote_server=match remote_proxy {
        Some(proxy_addr) => {
            // Connect to remote proxy
            span.in_scope(|| {
                trace!("Tunnel via proxy {}", proxy_addr)
            });
            let mut proxy = TcpStream::connect(proxy_addr).await?;

            let connect_req = format!("CONNECT {addr} HTTP/1.1\r\nHost: {addr}\r\n\r\n");
            proxy.write_all(connect_req.as_bytes()).await?;

            let mut response = Vec::new();
            proxy.read_buf(&mut response).await?;
            let response_str = String::from_utf8_lossy(&response);
            if !response_str.contains(" 200 ") {
                span.in_scope(|| {
                    warn!("Proxy CONNECT failed: {response_str}")
                });
                return Err(anyhow!("Proxy CONNECT failed: {response_str}"));
            }
            proxy
        },
        None => {
            // Directly connect to the requested server (the local DNS service will resolv to self in case of web redirection)
            span.in_scope(|| {
                trace!("Direct to {}", addr);
            });
            let res=TcpStream::connect(addr).await?; // Connect to remote server
            res
        }
    };

    // tunnel data
    let mut upgraded = TokioIo::new(upgraded);
    let (from_client, from_server) =
        tokio::io::copy_bidirectional_with_sizes(&mut upgraded, &mut remote_server, 32768, 32768).await?;

    // done
    span.in_scope(|| {
        info!(sent_bytes=from_client, recv_bytes=from_server, "tunnel closed")
    });

    Ok(())
}
