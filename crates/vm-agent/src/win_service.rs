//
// Copyright (c) 2026 DGAC/DSNA
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

const SERVICE_NAME: &str = "padsi-agent";

use std::ffi::OsString;
use std::sync::Mutex;
use std::time::Duration;
use tokio::sync::oneshot;
use tokio_util::sync::CancellationToken;
use windows_service::{
    define_windows_service,
    service::{
        ServiceControl, ServiceControlAccept, ServiceExitCode, ServiceState, ServiceStatus,
        ServiceType,
    },
    service_control_handler::{self, ServiceControlHandlerResult},
    service_dispatcher,
};

// Macro to generate an extern "system" fn(u32, *mut *mut u16) – here
// named "ffi_service_main".
// (Win32 API requires this service main function)
// This is called on a new thread by the system after the
// service_dispatcher::start call.
define_windows_service!(ffi_service_main, app_service_main);

pub fn start() -> Result<(), windows_service::Error> {
    // Wraps call to StartServiceCtrlDispatcher. A service executable is
    // supposed to call this function with a service name and a pointer to
    // the service's main function (as a service table entry) –
    // ffi_service_main.
    // start() only returns once the service has been stopped, so this
    // blocks until app_service_main returns.
    // ffi_service_main will be called in a new background thread.
    service_dispatcher::start(SERVICE_NAME, ffi_service_main)
}

// Called by ffi_service_main with the arguments passed when starting the
// service, i.e. sc.exe start <service name> arg1 arg2 (converted to
// Vec<OsString>). These are not the CLI args, so we can ignore them. This
// is called in the background thread in which ffi_service_main was called.
pub fn app_service_main(_arguments: Vec<OsString>) {
    if let Err(_e) = run_service() {
        // We could write to Windows Event Log here.
    }
    // Once this function (and ffi_service_main) returns, the dispatcher
    // (service_dispatcher::start) unblocks
}

// Main service logic for registration of SCM handler code (for
// handling stop events from SCM), to signal to SCM that we're
// starting/running/stopping, and eventually running the app within the
// tokio runtime.
fn run_service() -> Result<(), windows_service::Error> {
    // oneshot channel to be able to receive stop requests, should the SCM
    // control handler send one.
    // The transmitter part goes into the control handler closure, the
    // receiver goes into our own background tasks which handles the token
    // cancellation and setting of the stop pending state.
    let (shutdown_tx, shutdown_rx) = oneshot::channel();
    let shutdown_tx = Mutex::new(Some(shutdown_tx));

    // Register service control handler
    // This wraps RegisterServiceCtrlHandlerExW – so we tell SCM to call
    // the following closure whenever it needs to signal a control command
    // to us.
    // The closure runs in a separate thread.
    // Since register() gives us a status handle, this must be called
    // before we can set a status (start pending, etc.)
    // SERVICE_NAME is required here again, as multiple services could
    // potentially share the same binary (not in our case, but part of API).
    let status_handle =
        service_control_handler::register(
            SERVICE_NAME,
            move |control_event| match control_event {
                ServiceControl::Interrogate => {
                    // Mandatory so SCM can check if the service is still alive
                    // and responding
                    ServiceControlHandlerResult::NoError
                }
                ServiceControl::Stop => {
                    // SCM (or a user through SCM) has requested a stop of the
                    // service. Use oneshot transmitter to signal a shutdown to
                    // our app (in case it hasn't exited yet, i.e. receiver is
                    // still there).
                    // We use the channel here and not the cancellation token
                    // directly as we don't have the status handle yet and make
                    // it a little easier.
                    if let Some(tx) = shutdown_tx.lock().unwrap().take() {
                        let _ = tx.send(());
                    }
                    ServiceControlHandlerResult::NoError
                }

                // For any other control event we just tell SCM that we can't
                // handle it.
                _ => ServiceControlHandlerResult::NotImplemented,
            },
        )?;

    // Tell SCM that we've received the start request
    // This is technically not necessary since we don't have an
    // involved/long-lasting setup logic, so we could just go straight into
    // Running, and don't set StartPending with a wait_hint. I added it for
    // completeness.
    status_handle.set_service_status(ServiceStatus {
        // SERVICE_WIN32_OWN_PROCESS, i.e. we run our own non-shared
        // process
        service_type: ServiceType::OWN_PROCESS,

        // Starting up... (the new state of the service)
        current_state: ServiceState::StartPending,

        // Accept no control during startup
        controls_accepted: ServiceControlAccept::empty(),

        // No error
        exit_code: ServiceExitCode::Win32(0),

        // Progress checkpoint for SCM (only relevant if we would have a
        // multi-step initialization and want to report progress)
        checkpoint: 1,

        // How long SCM should wait before considering the service as hung
        wait_hint: Duration::from_secs(10),

        // Only used for shared processes
        process_id: None,
    })?;

    // Now tell SCM that we're ready
    // (ideally, you would have validated your initialization at this point,
    //  however, for a cross-platform app, you would likely need to
    //  restructure a bit if everything is normally done in run_app. I kept
    //  it simple here).
    status_handle.set_service_status(ServiceStatus {
        service_type: ServiceType::OWN_PROCESS,
        current_state: ServiceState::Running,

        // Accept STOP commands while running
        controls_accepted: ServiceControlAccept::STOP,

        exit_code: ServiceExitCode::Win32(0),

        // Must be 0 when service is running
        checkpoint: 0,

        // No wait hint when running, so just default/zero
        wait_hint: Duration::default(),
        process_id: None,
    })?;

    // Create tokio runtime _here_ as our regular main entry point in the
    // service case just calls the dispatcher and the dispatcher calls us
    // on a different thread.
    let runtime = match tokio::runtime::Runtime::new() {
        Ok(rt) => rt,
        Err(e) => {
            // Failed to create runtime
            status_handle.set_service_status(ServiceStatus {
                service_type: ServiceType::OWN_PROCESS,
                current_state: ServiceState::Stopped,
                controls_accepted: ServiceControlAccept::empty(),
                exit_code: ServiceExitCode::Win32(575), // app init failure
                checkpoint: 0,
                wait_hint: Duration::default(),
                process_id: None,
            })?;

            // hack: note that a custom error type might be more accurate
            // here as it's not a Windows API error...
            return Err(windows_service::Error::Winapi(e));
        }
    };

    let token = CancellationToken::new();

    runtime.block_on(async {
        let app_token = token.clone();

        // Spawn background task that waits for stop signal sent through
        // control handler, and then cancels the token to notify our app
        // tasks
        tokio::spawn(async move {
            let _ = shutdown_rx.await;
            token.cancel();

            // Report StopPending so SCM knows we're winding down and
            // doesn't force-kill us.
            // ServiceStatusHandle implements Copy, so we can use it here
            let _ = status_handle.set_service_status(ServiceStatus {
                service_type: ServiceType::OWN_PROCESS,
                current_state: ServiceState::StopPending,
                controls_accepted: ServiceControlAccept::empty(),
                exit_code: ServiceExitCode::Win32(0),
                checkpoint: 1,
                wait_hint: Duration::from_secs(10),
                process_id: None,
            });
        });

        // Run application until cancelled via token
        if let Err(e) = crate::run_app(app_token).await {
            // again: better to log to Windows Event Log or a file as
            // this error message goes to nowhere :-)
            eprintln!("Got error: {e}");
        }
    });

    // Tell SCM that we're done (we've passed the blocking runtime code
    // above, so we either crashed or received a shutdown)
    status_handle.set_service_status(ServiceStatus {
        service_type: ServiceType::OWN_PROCESS,
        current_state: ServiceState::Stopped,
        controls_accepted: ServiceControlAccept::empty(),
        exit_code: ServiceExitCode::Win32(0), // You may want to make return an error code
        // depending on run_app success
        checkpoint: 0,
        wait_hint: Duration::default(),
        process_id: None,
    })?;

    Ok(())
}
