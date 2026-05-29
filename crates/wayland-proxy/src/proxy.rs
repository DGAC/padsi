use std::io;
use std::os::unix::io::{AsRawFd, RawFd};
use std::os::unix::net::UnixStream;
use std::path::Path;

use padsi::trace::warn;
use std::sync::{Arc, Mutex};

use crate::config::ProxyConfig;
use crate::filter::{ProxyState, proxy_client_to_server, proxy_server_to_client};

// Max bytes per Wayland message chunk and max ancillary FDs in one recvmsg call.
const BUF_SIZE: usize = 65536; // maximum message size in Wayland (from messages' headers structure)
const MAX_FDS: usize = 28; // Wayland protocol limit

/// Connect to the real compositor and start bidirectional forwarding.
pub fn run_proxy<P>(
    sconfig: Arc<Mutex<ProxyConfig>>,
    client: UnixStream,
    upstream_path: P,
) -> io::Result<()>
where
    P: AsRef<Path>,
{
    let server = UnixStream::connect(upstream_path)?;

    client.set_nonblocking(false)?;
    server.set_nonblocking(false)?;

    let client_fd = client.as_raw_fd();
    let server_fd = server.as_raw_fd();

    // one thread to forward messages per direction, each thread owns one direction exclusively (as RawFD)
    // Safety: we dup the fds so both threads can hold their read/write ends.
    let client_r = dup_fd(client_fd)?;
    let client_w = dup_fd(client_fd)?;
    let server_r = dup_fd(server_fd)?;
    let server_w = dup_fd(server_fd)?;

    // Drop originals so they're not leaked
    drop(client);
    drop(server);

    let proxy_state = ProxyState::new(sconfig.lock().unwrap().enforce());
    let s_proxy_state = Arc::new(Mutex::new(proxy_state));

    let c_c2s = sconfig.clone();
    let fw_c2s = s_proxy_state.clone();
    let c2s = std::thread::Builder::new()
        .name("client2server".into())
        .spawn(move || {
            if let Err(e) = forward(
                c_c2s,
                fw_c2s,
                ForwardMode::ClientToServer,
                client_r,
                server_w,
            ) {
                warn!("[c2s] {e}");
            }
            close_fd(client_r);
            close_fd(server_w);
        })?;

    let c_s2c = sconfig.clone();
    let fw_s2c = s_proxy_state.clone();
    let s2c = std::thread::Builder::new()
        .name("server2client".into())
        .spawn(move || {
            if let Err(e) = forward(
                c_s2c,
                fw_s2c,
                ForwardMode::ServerToClient,
                server_r,
                client_w,
            ) {
                warn!("[s2c] {e}");
            }
            close_fd(server_r);
            close_fd(client_w);
        })?;

    let _ = c2s.join();
    let _ = s2c.join();

    Ok(())
}

enum ForwardMode {
    ClientToServer,
    ServerToClient,
}

/// Forward Wayland data (bytes + file descriptors) from `src` to `dst`
/// using `recvmsg` / `sendmsg` so that ancillary FDs are preserved.
fn forward(
    sconfig: Arc<Mutex<ProxyConfig>>,
    fwcontext: Arc<Mutex<ProxyState>>,
    mode: ForwardMode,
    src: RawFd,
    dst: RawFd,
) -> io::Result<()> {
    let mut data_buf = vec![0u8; BUF_SIZE];
    // Ancillary buffer sized for MAX_FDS file descriptors
    let cmsg_space =
        unsafe { libc::CMSG_SPACE((MAX_FDS * std::mem::size_of::<RawFd>()) as u32) } as usize;
    let mut cmsg_buf = vec![0u8; cmsg_space];

    loop {
        // receive
        let (mut ndata, received_fds) = recvmsg(src, &mut data_buf, &mut cmsg_buf)?;
        if ndata == 0 && received_fds.is_empty() {
            // Peer closed the connection
            return Ok(());
        }

        // adapt
        ndata = match mode {
            ForwardMode::ClientToServer => {
                proxy_client_to_server(&sconfig, &fwcontext, &mut data_buf, ndata)
            }
            ForwardMode::ServerToClient => {
                proxy_server_to_client(&sconfig, &fwcontext, &mut data_buf, ndata)
            }
        };

        // send received data
        if let Err(err) = sendmsg(dst, &data_buf[..ndata], &received_fds) {
            warn!("failed to send data: {}", err.to_string());
            for fd in received_fds {
                close_fd(fd);
            }
            return Ok(());
        }

        // Close the received FDs — they've been sent over and the kernel
        // duplicated them into the destination process's fd table.
        for fd in received_fds {
            close_fd(fd);
        }
    }
}

//
// Low-level sendmsg / recvmsg wrappers
//
fn recvmsg(fd: RawFd, data: &mut [u8], cmsg_buf: &mut [u8]) -> io::Result<(usize, Vec<RawFd>)> {
    let mut iov = libc::iovec {
        iov_base: data.as_mut_ptr() as *mut libc::c_void,
        iov_len: data.len(),
    };

    let mut msghdr: libc::msghdr = unsafe { std::mem::zeroed() };
    msghdr.msg_iov = &mut iov;
    msghdr.msg_iovlen = 1;
    msghdr.msg_control = cmsg_buf.as_mut_ptr() as *mut libc::c_void;
    msghdr.msg_controllen = cmsg_buf.len() as _;

    let n = unsafe { libc::recvmsg(fd, &mut msghdr, libc::MSG_CMSG_CLOEXEC) };
    if n < 0 {
        return Err(io::Error::last_os_error());
    }

    let fds = extract_fds(&msghdr);
    Ok((n as usize, fds))
}

fn sendmsg(fd: RawFd, data: &[u8], fds: &[RawFd]) -> io::Result<()> {
    let mut iov = libc::iovec {
        iov_base: data.as_ptr() as *mut libc::c_void,
        iov_len: data.len(),
    };

    let cmsg_buf: Vec<u8>;
    let mut msghdr: libc::msghdr = unsafe { std::mem::zeroed() };
    msghdr.msg_iov = &mut iov;
    msghdr.msg_iovlen = 1;

    if !fds.is_empty() {
        let payload_len = fds.len() * std::mem::size_of::<RawFd>();
        let space = unsafe { libc::CMSG_SPACE(payload_len as u32) } as usize;
        cmsg_buf = vec![0u8; space];

        msghdr.msg_control = cmsg_buf.as_ptr() as *mut libc::c_void;
        msghdr.msg_controllen = space as _;

        // Fill in the cmsghdr
        let cmsg = unsafe { libc::CMSG_FIRSTHDR(&msghdr) };
        if !cmsg.is_null() {
            unsafe {
                (*cmsg).cmsg_level = libc::SOL_SOCKET;
                (*cmsg).cmsg_type = libc::SCM_RIGHTS;
                (*cmsg).cmsg_len = libc::CMSG_LEN(payload_len as u32) as _;
                std::ptr::copy_nonoverlapping(
                    fds.as_ptr() as *const u8,
                    libc::CMSG_DATA(cmsg),
                    payload_len,
                );
            }
        }
    }

    // sendmsg loop to handle partial sends (rare on Unix sockets but correct)
    let mut sent = 0usize;
    while sent < data.len().max(1) {
        let n = unsafe { libc::sendmsg(fd, &msghdr, libc::MSG_NOSIGNAL) };
        if n < 0 {
            return Err(io::Error::last_os_error());
        }
        sent += n as usize;
        // After the first send the FDs are transferred; subsequent sends are
        // plain data if the message was large (shouldn't happen for Wayland).
        if sent < data.len() {
            msghdr.msg_control = std::ptr::null_mut();
            msghdr.msg_controllen = 0;
            let remaining = data.len() - sent;
            let new_iov = libc::iovec {
                iov_base: data[sent..].as_ptr() as *mut libc::c_void,
                iov_len: remaining,
            };
            msghdr.msg_iov = &new_iov as *const _ as *mut _;
            msghdr.msg_iovlen = 1;
        } else {
            break;
        }
    }

    Ok(())
}

/// Extract SCM_RIGHTS file descriptors from a received msghdr.
fn extract_fds(msghdr: &libc::msghdr) -> Vec<RawFd> {
    let mut fds = Vec::new();
    let mut cmsg = unsafe { libc::CMSG_FIRSTHDR(msghdr) };
    while !cmsg.is_null() {
        let level = unsafe { (*cmsg).cmsg_level };
        let typ = unsafe { (*cmsg).cmsg_type };
        if level == libc::SOL_SOCKET && typ == libc::SCM_RIGHTS {
            let data = unsafe { libc::CMSG_DATA(cmsg) };
            let len = unsafe { (*cmsg).cmsg_len } as usize;
            let hdr_len = unsafe { libc::CMSG_LEN(0) } as usize;
            let payload = len.saturating_sub(hdr_len);
            let count = payload / std::mem::size_of::<RawFd>();
            for i in 0..count {
                let fd: RawFd = unsafe {
                    std::ptr::read_unaligned(
                        data.add(i * std::mem::size_of::<RawFd>()) as *const RawFd
                    )
                };
                fds.push(fd);
            }
        }
        cmsg = unsafe { libc::CMSG_NXTHDR(msghdr, cmsg) };
    }
    fds
}

//
// Helpers
//
fn dup_fd(fd: RawFd) -> io::Result<RawFd> {
    let new = unsafe { libc::dup(fd) };
    if new < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(new)
    }
}

fn close_fd(fd: RawFd) {
    unsafe { libc::close(fd) };
}
