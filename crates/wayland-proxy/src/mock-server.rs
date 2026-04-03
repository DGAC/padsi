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

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::UnixListener;
use std::path::Path;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let proxy_service_path = "/tmp/mock-server.sock";

    // Remove any existing socket file
    if Path::new(proxy_service_path).exists() {
        std::fs::remove_file(proxy_service_path)?;
    }

    let listener = UnixListener::bind(proxy_service_path)?;
    println!("Listening on {proxy_service_path}");

    loop {
        let (mut stream, addr) = listener.accept().await?;
        println!("Client connected: {:?}", addr);

        tokio::spawn(async move {
            let mut buf = vec![0u8; 1024];
            loop {
                match stream.read(&mut buf).await {
                    Ok(n) if n>0 => {
                        println!("[RECV bytes] {:x?}", &buf[..n]);
                        if let Ok(mut text) = std::str::from_utf8(&buf[..n]) {
                            text=text.trim();
                            println!("[RECV UTF-8] {text}");
                            let reply=match text {
                                "" => String::from("???"),
                                _ => format!("mock server handled [{}]", text)
                            };
                            if let Err(e) = stream.write_all(reply.as_bytes()).await {
                                println!("Failed to reply UTF-8: {}", e)
                            }
                        }
                        else {
                            if let Err(e) = stream.write_all(&buf[..n]).await {
                                println!("Failed to echo received bytes: {}", e)
                            }
                        }
                    },
                    Ok(_) => {
                        println!("Client disconnected");
                        return;
                    }
                    Err(e) => {
                        eprintln!("Read error: {e}");
                        return;
                    }
                };
            }
        });
    }
}
