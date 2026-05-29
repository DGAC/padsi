use padsi::trace::{LevelFilter, TraceConfig, debug, info, tracing_setup_json};
use std::env;
use std::os::unix::net::UnixListener;
use std::sync::{Arc, Mutex};

mod config;
mod constants;
mod filter;
mod message;
mod proxy;
mod proxybuffer;

#[cfg(test)]
mod tests;

use config::ProxyConfig;
use proxy::run_proxy;

/// Gets the launch arguments of the proxy
///
/// Gets the arguments specified at the proxy launch.
/// The order for the return tuple of these arguments are :
/// real_compo_path, compositor path, zone name, authorized zones (CSV), allow no zone, enforce
fn get_args() -> (String, String, String, String, bool, bool) {
    let args: Vec<String> = env::args().collect();
    let server_path = if args.len() > 1 {
        args[1].clone()
    } else {
        String::from("/tmp/mock-server.sock")
    };

    let proxy_service_path = if args.len() >= 3 {
        args[2].clone()
    } else {
        String::from("/tmp/proxy.sock")
    };

    let this_zone_arg: String = if args.len() >= 4 {
        args[3].clone()
    } else {
        format!("{}", std::process::id())
    };

    let authorized_zones_arg: String = if args.len() >= 5 {
        args[4].clone()
    } else {
        String::from("")
    };

    let allow_nozone = if args.len() == 6 {
        args[5] == "allow"
    } else {
        true
    };

    let enforce = if args.len() == 7 {
        args[6] == "enforce"
    } else {
        true
    };
    return (
        server_path,
        proxy_service_path,
        this_zone_arg,
        authorized_zones_arg,
        allow_nozone,
        enforce,
    );
}

fn main() {
    // init logging
    let mut log_dir = String::from("/var/log");
    if let Ok(v) = env::var("LOG_DIR") {
        log_dir = String::from(v)
    }
    println!("Logging to directory '{}'", log_dir);
    let trace_conf = TraceConfig::new(&log_dir, "wayland-proxy")
        .with_stdout_output(true)
        .with_stdout_level(LevelFilter::INFO)
        .with_file_level(LevelFilter::WARN)
        .with_syslog_level(LevelFilter::WARN);
    let _t = tracing_setup_json(&trace_conf).expect("Failed to initialize logging");

    // Initialize args
    let (upstream_path, proxy_path, this_zone_arg, authorized_zones_arg, allow_nozone, enforce) =
        get_args();
    let config = ProxyConfig::new(&this_zone_arg, &authorized_zones_arg, allow_nozone, enforce);

    info!(
        "Starting Wayland proxy for zone '{}', can paste data copied from zones '{}'",
        this_zone_arg, authorized_zones_arg
    );
    info!("Listening on {proxy_path}");
    info!("Actual server at {upstream_path}");
    info!("Config: {:?}", config);

    // shared config.
    let sconfig: Arc<Mutex<ProxyConfig>> = Arc::new(Mutex::new(config));

    // Remove any existing socket file
    let _ = std::fs::remove_file(&proxy_path);

    let listener = UnixListener::bind(&proxy_path).expect("Failed to bind proxy socket");

    for stream in listener.incoming() {
        match stream {
            Ok(client) => {
                let upstream_path = upstream_path.clone();
                let sconfig = sconfig.clone();
                std::thread::spawn(move || {
                    debug!("[proxy] New client connection");
                    if let Err(e) = run_proxy(sconfig, client, &upstream_path) {
                        debug!("[proxy] Connection ended: {e}");
                    }
                });
            }
            Err(e) => debug!("[proxy] Accept error: {e}"),
        }
    }
}
