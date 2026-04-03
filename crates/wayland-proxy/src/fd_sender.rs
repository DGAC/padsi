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

use std::os::unix::net::UnixStream;
use asyncfd::UnixFdStream;
use std::fs::File;
use std::os::unix::io::AsRawFd;
use tokio::io::AsyncWriteExt;
use std::{thread, time};

async fn send() {
    // Connect to the socket that needs testing
	let path=String::from("/tmp/proxy.sock");
    let stream=match UnixStream::connect(&path) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("Failed to connect to server: {}", e);
            return
        }
    };
    let mut stream_fd = UnixFdStream::new(stream, 4).unwrap();
    println!("Socket connected successfully");

    // WRITE File
    // Create a File and put text in it. This is used afterwards by the FD creation process
    let mut file_w = File::create("/tmp/proxy_file_test.txt").expect("Error failed to create file.");
    std::io::Write::write_all(& mut file_w, b"This is a text file for Max's proxy!").expect("Error while writing in file.");
    //let fd = file.as_raw_fd();
    println!("Write File created successfully");
    //println!("FD of File : {}", fd);

    // READ File
    let file = File::open("/tmp/proxy_file_test.txt").expect("Failed to create Read-Only file.");
    let fd  = file.as_raw_fd();
    println!("FD of read-only File created successfully.");
    println!("FD of File : {}", fd);

    // Pass this FD to the socket
    stream_fd.push_outgoing_fd(fd);
    stream_fd.write_all(b"passed FD\n").await.unwrap();
    println!("FD passed successfully");
    println!("END.");

    // Delay implementation
    let delay_duration = time::Duration::from_millis(3000);
    thread::sleep(delay_duration);
}

#[tokio::main]
async fn main(){
    send().await;
}
