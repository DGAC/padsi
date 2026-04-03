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
//! Web redirector which display a "blocked page" notification and WPAD server
//!

use std::sync::Arc;
use std::net::SocketAddr;
use anyhow::{anyhow, Context, Result};
use std::net::Ipv4Addr;
use tokio::task::JoinSet;
use tokio::net::{TcpListener};
use hyper_util::rt::TokioIo;
type ServerBuilder = hyper::server::conn::http1::Builder;
use hyper::service::service_fn;
use hyper::{body, Method, Request, Response, StatusCode};
use hyper::body::{Bytes};
use tokio_rustls::LazyConfigAcceptor;
use rustls::pki_types::{CertificateDer};
use rustls::server::{Acceptor, ServerConfig};
use http_body_util::{combinators::BoxBody};
use padsi::pki::{PKCS12, usages::TlsServer};
use padsi::trace::{Instrument, Level, error, info, warn, trace, span};

use crate::config::GlobalConfig;
use crate::misc::{empty, full, static_file_response, parse_ipv4, ensure_ip_address_present, response_for_web_redir_resource};

const WPAD_PORT:u16=80;

pub async fn setup(conf: &Arc<GlobalConfig>, set: &mut JoinSet<()>) -> Result<()> {
    http_setup(conf, set).await?;
    https_setup(conf, set).await
}

async fn http_setup(conf: &Arc<GlobalConfig>, set: &mut JoinSet<()>) -> Result<()> {
    let mut ports:Vec<u16>=vec![];
    let mut ip:Ipv4Addr=Ipv4Addr::from([0, 0, 0, 0]);
    if conf.web_proxy.is_some() {
        ports.push(WPAD_PORT);
        ip=match parse_ipv4(conf.web_proxy.as_ref().unwrap().listening_ip()) {
            Ok(r) => r,
            Err(err) => return Err(anyhow!(err))
        };
    }

    if conf.web_redirector.is_some() {
        for port in &conf.web_redirector.as_ref().unwrap().http_ports {
            if ! ports.contains(port) {
                ports.push(*port);
            }
        }

        ip=match parse_ipv4(conf.web_redirector.as_ref().unwrap().listening_ip()) {
            Ok(r) => r,
            Err(err) => return Err(anyhow!(err))
        };
    }

    ensure_ip_address_present(ip).await?;
    for port in ports {
        let web_addr = SocketAddr::from((ip, port));
        let web_listener = TcpListener::bind(web_addr).await?;
        if port==WPAD_PORT {
            info!("HTTP Web redirector and WPAD listening on http://{}", web_addr);
        }
        else {
            info!("HTTP Web redirector listening on http://{}", web_addr);
        }
        let conf = Arc::clone(conf);

        set.spawn(async move {
            loop {
                match web_listener.accept().await {
                    Ok((stream, addr)) => {
                        let span=span!(Level::INFO, "connection", client_ip=addr.ip().to_string(), client_port=addr.port());
                        let io = TokioIo::new(stream);
                        let config = Arc::clone(&conf); // clone Arc for each connection
                        tokio::task::spawn(async move {
                            if let Err(err) = ServerBuilder::new()
                                .serve_connection(io, service_fn(|req: Request<body::Incoming>| {
                                    let config = Arc::clone(&config);
                                    handle_request(req, port, config).instrument(span.clone())
                                }))
                                .with_upgrades()
                                .instrument(span.clone())
                                .await {
                                    span.in_scope(|| {
                                        error!("Failed to serve connection: {:?}", err);
                                    })
                            }
                        });
                    },
                    Err(err) => {
                        error!("Failed to handle incoming connection: {}", err.to_string())
                    }
                }
            };
        });
    }
    Ok(())
}

async fn craft_p12(conf: &GlobalConfig, sni: Option<&str>) -> Result<PKCS12> {
    if sni.is_none() {
        warn!("No SNI specified in request");
        return Err(anyhow!("No SNI specified in request"));
    }
    let sni=sni.unwrap();
    let redir_conf=conf.web_redirector.as_ref().unwrap();
    let mut cache=redir_conf.cache.as_ref().context("CODEBUG: cache is None")?.as_ref().write().unwrap();
    if cache.get(sni).is_none() {
        let p12= match redir_conf.ca.as_ref().context("CODEBUG: ca is None")?.generate_key_and_certificate(
            &TlsServer::new(time::Duration::days(1)), sni, None::<Vec<String>>) {
            Ok(p12) => p12,
            Err(err) => {
                return Err(err);
            }
        };
        cache.add(p12);
    }
    let p12=cache.get(sni).unwrap();
    trace!(cn=p12.cert().attributes().cn, "Generated certificate");
    Ok(p12.clone())
}

async fn https_setup(conf: &Arc<GlobalConfig>, set: &mut JoinSet<()>) -> Result<()> {
    let mut ports:Vec<u16>=vec![];
    let mut ip:Ipv4Addr=Ipv4Addr::from([0, 0, 0, 0]);
    if conf.web_redirector.is_some() {
        for port in &conf.web_redirector.as_ref().unwrap().https_ports {
            if ! ports.contains(port) {
                ports.push(*port);
            }
        }
        ip=match parse_ipv4(conf.web_redirector.as_ref().unwrap().listening_ip()) {
            Ok(r) => r,
            Err(err) => return Err(anyhow!(err))
        };
    }

    for port in ports {
        let web_addr = SocketAddr::from((ip, port));
        let web_listener = TcpListener::bind(web_addr).await?;
        info!("HTTPS Web redirector listening on https://{}", web_addr);
        let conf = Arc::clone(conf);

        set.spawn(async move {
            loop {
                match web_listener.accept().await {
                    Ok((stream, addr)) => {
                        let span=span!(Level::INFO, "connection", service_ip=?ip, service_port=port, client_ip=addr.ip().to_string(), client_port=addr.port());

                        let acceptor = LazyConfigAcceptor::new(Acceptor::default(), stream);
                        let start=acceptor.await.unwrap();
                        let client_hello=start.client_hello();

                        span.in_scope(|| {
                            trace!("SNI: {:?}", client_hello.server_name())
                        });
                        let p12=match craft_p12(conf.as_ref(), client_hello.server_name()).instrument(span.clone()).await {
                            Ok(p12) => p12,
                            Err(err) => {
                                span.in_scope(|| {
                                    error!("Failed to generate private key and certificate: {:?}", err)
                                });
                                continue;
                            }
                        };
                        let cev:Vec<CertificateDer<'static>>=vec![p12.cert().clone().cert_der];
                        let key=p12.priv_key().privkey_der().unwrap().clone_key();
                        let config = ServerConfig::builder()
                            .with_no_client_auth()
                            .with_single_cert(cev, key)
                            .unwrap();

                        match start.into_stream(Arc::new(config)).await {
                            Ok(accepted_stream) => {
                                let io = TokioIo::new(accepted_stream);
                                let config = Arc::clone(&conf); // Clone Arc for each connection
                                let span=span.clone();
                                tokio::task::spawn(async move {
                                    if let Err(err) = ServerBuilder::new()
                                        .serve_connection(io, service_fn(|req: Request<body::Incoming>| {
                                        let config = Arc::clone(&config);
                                        handle_request(req, port, config).instrument(span.clone())
                                    }))
                                        .with_upgrades()
                                        .await
                                    {
                                        span.in_scope(|| {
                                            warn!("Failed to serve connection: {:?}", err)
                                        })
                                    }
                                })
                            },
                            Err(err) => {
                                span.in_scope(|| {
                                    warn!("Failed to accept TLS connection: {:?}", err)
                                });
                                continue;
                            }
                        };
                    },
                    Err(err) => {
                        warn!("Error: {}", err.to_string());
                    }
                }
            };
        });
    }
    Ok(())
}


async fn handle_request(
    req: Request<hyper::body::Incoming>,
    port: u16,
    conf: Arc<GlobalConfig>
) -> Result<Response<BoxBody<Bytes, hyper::Error>>, hyper::Error> {
    let span=span!(Level::INFO, "handle_request", http_method=?req.method(), uri=?req.uri(), http_version=?req.version());
    let conf=conf.as_ref();

    // WPAD requested?
    if conf.web_proxy.is_some() && port==WPAD_PORT &&
        (req.method(), req.uri().path()) == (&Method::GET, "/wpad.dat") {
        span.in_scope(|| {
            trace!("WPAD requested")
        });
        match conf.web_proxy.as_ref().unwrap().wpad_file.as_ref() {
            Some(wpad_file) => return static_file_response(wpad_file.path(),
                None, Some("application/x-ns-proxy-autoconfig")).await,
            None => {
                let mut resp = Response::new(full("wpad.dat file not yet computed"));
                *resp.status_mut() = StatusCode::INTERNAL_SERVER_ERROR;
                return Ok(resp)
            }
        }
    }

    // any other GET
    else if conf.web_redirector.is_some() && req.method() == &Method::GET {
        let redir_conf=conf.web_redirector.as_ref().unwrap();
        let found=redir_conf.http_ports.contains(&port) || redir_conf.https_ports.contains(&port);
        if found {
            if let Some(r) = response_for_web_redir_resource(&req, port, conf)
                .instrument(span.clone())
                .await {
                return r
            }
        }
    }

    // final fallback
    let mut resp = Response::new(empty());
    *resp.status_mut() = StatusCode::FORBIDDEN;
    Ok(resp)
}
