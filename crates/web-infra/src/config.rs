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

use crate::cache::{CertificatesCache, SizedCache};
use anyhow::{Result, anyhow};
use padsi::trace::info;
use padsi::{
    net::{EndPoint, endpoint::Zone},
    pki::CA,
};
use serde::{Deserialize, Serialize};
use std::{
    env::current_exe,
    fs::File,
    io::{BufReader, Write},
    path::{Path, PathBuf},
    sync::{Arc, RwLock},
};
use tempfile::NamedTempFile;

///
/// Local Web proxy configuration
/// Web proxy on specified port and WPAD server on port 80
///
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct WebProxyConfig {
    /// Port on which the local Web proxy is listening
    pub listening_port: u16,

    /// IP address to listen on (None means 0.0.0.0), must be the IP address of a local interface
    pub listening_ip: Option<String>,

    /// Access to the remote proxy: `<ip>:<port>` if enabled, None otherwise
    pub targets: Vec<Target>,

    /// cache how which host can be accessed
    #[serde(skip)]
    pub targets_cache: Option<Arc<RwLock<SizedCache<String, Option<String>>>>>,

    /// Path to the WPAD file once it has been generated
    #[serde(skip)]
    pub wpad_file: Arc<Option<NamedTempFile>>,
}

impl WebProxyConfig {
    pub fn generate_wpad_file(&mut self) -> Result<()> {
        // generate proxy PAC data:
        // direct access is listed as DIRECT, everything else is routet through the proxy
        let mut pad = String::from("function FindProxyForURL(url, host) {\n");
        for target in self.targets.iter() {
            if target.is_direct() {
                for rule in target.rules.iter() {
                    for zone in rule.endpoint.zones() {
                        match zone {
                            Zone::All => pad.push_str(&format!("    return \"DIRECT\";\n")),
                            Zone::Name(dname) => pad.push_str(&format!(
                                "    if (host==\"{}\") return \"DIRECT\";\n",
                                dname.without_trailing_dot()
                            )),
                            Zone::Network(n) => pad.push_str(&format!(
                                "    if (isInNet(host, \"{}\", \"{}\")) return \"DIRECT\";\n",
                                n.ip().to_string(),
                                n.mask().to_string()
                            )),
                            Zone::Pattern(p) => pad.push_str(&format!(
                                "    if (shExpMatch(url, \"{}\")) return \"DIRECT\";\n",
                                p.without_trailing_dot()
                            )),
                        }
                    }
                }
            }
        }
        pad.push_str("\n    // default, don't use a proxy\n");
        pad.push_str(&format!(
            "    return \"{}:{}\";\n",
            self.listening_ip(),
            self.listening_port
        ));
        pad.push_str("}\n");

        // store in file
        let mut wpadfile = NamedTempFile::new()?;
        wpadfile.write_all(pad.as_bytes())?;
        self.wpad_file = Arc::new(Some(wpadfile));

        // targets cache
        self.targets_cache = Some(Arc::new(RwLock::new(SizedCache::new(100))));
        Ok(())
    }

    pub fn listening_ip(&self) -> &str {
        match &self.listening_ip {
            Some(ip) => ip,
            None => "0.0.0.0",
        }
    }

    pub fn port(&self) -> u16 {
        self.listening_port
    }

    /// Get the address as the target of a Web request.
    /// Returns:
    ///     - Ok(address to use as String) if the request has to be passed on to another proxy
    ///     - Ok(None) if the request has to be honored directly
    ///     - Err() if there was some sort of error (access blocked, no host specified, ...)
    pub fn get_hext_hop_addr(&self, req_ep: &EndPoint) -> Result<Option<String>> {
        let ep_str = req_ep.to_string();
        let mut cache = self.targets_cache.as_ref().unwrap().write().unwrap();
        if let Some(v) = cache.get(&ep_str) {
            info!(
                allowed_with = v,
                cached = true,
                allowed = req_ep.to_string(),
                "access allowed"
            );
            return Ok(v.clone());
        }

        // find the most precise matching rule, if any
        let mut matching_endpoint: Option<&Rule> = None;
        let mut matching_target: Option<&Target> = None;
        for target in self.targets.iter() {
            for rule in target.rules.iter() {
                if rule.endpoint.contains(&req_ep) {
                    //debug!("rule {} match!", rule.endpoint);
                    (matching_endpoint, matching_target) = match matching_endpoint {
                        Some(r) => {
                            let zone = &rule.endpoint.zones()[0];
                            let spec = format!("{}^tcp^{}", zone.to_string(), req_ep.ports()[0]);
                            let spec_ep = EndPoint::new(&spec).unwrap();
                            //debug!("spec_ep= {}", spec_ep);
                            if r.endpoint.contains(&spec_ep) {
                                // r is more generic => keep rule which the most specific
                                //debug!("rule {} contains {}", r.endpoint, rule.endpoint);
                                (Some(rule), Some(target))
                            } else {
                                //debug!("rule {} DOES NOT contains {}", r.endpoint, rule.endpoint);
                                (matching_endpoint, matching_target)
                            }
                        }
                        None => (Some(rule), Some(target)),
                    };
                    //debug!("matching_endpoint so far: {}", matching_endpoint.unwrap().endpoint);
                }
            }
        }

        // result
        match (matching_endpoint, matching_target) {
            (Some(rule), Some(target)) => match rule.action {
                RuleAction::Allow => {
                    info!(
                        allowed_with = target.remote_proxy,
                        allowed = req_ep.to_string(),
                        "access allowed"
                    );
                    cache.add(ep_str, target.remote_proxy.clone());
                    return Ok(target.remote_proxy.clone());
                }
                RuleAction::Deny => {
                    info!(
                        blocked_with = target.remote_proxy,
                        blocked = req_ep.to_string(),
                        "access denied"
                    );
                    return Err(anyhow!("Access denied by '{:?}'", target.remote_proxy));
                }
            },
            _ => {
                info!(
                    blocked = req_ep.to_string(),
                    "Access not allowed by any rule"
                );
                return Err(anyhow!("Access not allowed by any rule"));
            }
        }
    }
}

///
/// Filter action
///
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum RuleAction {
    Deny,
    Allow,
}

///
/// Filter rule
///
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Rule {
    action: RuleAction,
    endpoint: EndPoint,
}

///
/// Web Filter
///
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Target {
    pub remote_proxy: Option<String>,
    pub rules: Vec<Rule>,
}

impl Target {
    pub fn is_direct(&self) -> bool {
        self.remote_proxy.is_none()
    }
}

///
/// Web redirector configuration
///
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct WebRedirectorConfig {
    /// List of HTTP ports on which the web redirector listens, None if disabled
    pub http_ports: Vec<u16>,

    /// List of HTTPS ports on which the web redirector listens, None if disabled
    pub https_ports: Vec<u16>,

    /// IP address to listen on (None meaning 0.0.0.0), must be the IP address of a local interface
    pub listening_ip: Option<String>,

    /// Private key of the CA used to generate "fake" certificates for the web redirection
    #[serde(skip)]
    pub ca: Option<CA>, // to allow CA to be specified after the conf. is loaded

    /// Certificates cache
    #[serde(skip)]
    pub cache: Option<Arc<RwLock<CertificatesCache>>>,
}

impl WebRedirectorConfig {
    pub fn listening_ip(&self) -> &str {
        match &self.listening_ip {
            Some(ip) => ip,
            None => "0.0.0.0",
        }
    }
}

///
/// Global component configuration
///
#[derive(Clone, Deserialize, Serialize)]
pub struct GlobalConfig {
    /// Full path to html resources
    #[serde(skip)]
    pub webroot: PathBuf,
    pub web_proxy: Option<WebProxyConfig>,
    pub web_redirector: Option<WebRedirectorConfig>,
}

fn compute_webroot() -> PathBuf {
    let exepath = current_exe().unwrap_or_else(|_| "/etc".into());
    let mut webroot = PathBuf::from(exepath.parent().unwrap());
    webroot.push("webroot");
    webroot
}

impl GlobalConfig {
    /// Load a configuration from a JSON file
    /// Note: the CA part is left as None and must be set independently
    pub fn from_json<P: AsRef<Path>>(config_file: P) -> Result<Self> {
        let file = File::open(config_file)?;
        let reader = BufReader::new(file);
        let mut conf: GlobalConfig = serde_json::from_reader(reader)?;
        conf.webroot = compute_webroot();
        if let Some(ref mut redir_conf) = conf.web_redirector {
            redir_conf.cache = Some(Arc::new(RwLock::new(CertificatesCache::new(10))));
        }
        Ok(conf)
    }
}

impl Default for GlobalConfig {
    fn default() -> Self {
        GlobalConfig {
            webroot: compute_webroot(),
            web_proxy: None,
            web_redirector: None,
        }
    }
}

impl std::fmt::Display for GlobalConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Config[proxy: {:?}, redirector: {:?}]",
            self.web_proxy, self.web_redirector
        )
    }
}
