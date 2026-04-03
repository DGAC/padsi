/**
 * Copyright (c) 2025-2026 DGAC/DSNA
 *
 * This file is part of PADSI.
 *
 * This software is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This software is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this software.  If not, see <http://www.gnu.org/licenses/>.
 */
#define _GNU_SOURCE
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define SOCKET_PATH "/bubble/run/padsi-usbredir.sock"

static void send_fds(int sock, int *fds, int nfds) {
    struct msghdr msg = {0};
    char buf[CMSG_SPACE(nfds * sizeof(int))];
    memset(buf, 0, sizeof(buf));

    struct iovec io = { .iov_base = "X", .iov_len = 1 }; // at least 1 byte

    msg.msg_iov = &io;
    msg.msg_iovlen = 1;

    msg.msg_control = buf;
    msg.msg_controllen = sizeof(buf);

    struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
    cmsg->cmsg_level = SOL_SOCKET;
    cmsg->cmsg_type  = SCM_RIGHTS;
    cmsg->cmsg_len   = CMSG_LEN(nfds * sizeof(int));

    memcpy(CMSG_DATA(cmsg), fds, nfds * sizeof(int));

    msg.msg_controllen = cmsg->cmsg_len;

    if (sendmsg(sock, &msg, 0) == -1) {
        perror("sendmsg");
        exit(1);
    }
}

int main(void) {
    int sock = socket(AF_UNIX, SOCK_STREAM, 0);
    if (sock == -1) {
        perror("socket");
        return 1;
    }

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, SOCKET_PATH, sizeof(addr.sun_path) - 1);

    if (connect(sock, (struct sockaddr*)&addr, sizeof(addr)) == -1) {
        perror("connect");
        close(sock);
        return 1;
    }

    int fds[3] = { STDIN_FILENO, STDOUT_FILENO, STDERR_FILENO };
    send_fds(sock, fds, 3);

    char buf[256];
    ssize_t n = read(sock, buf, sizeof(buf) - 1);
    if (n > 0) {
        buf[n] = '\0';
        return atoi(buf);
    }

    close(sock);
    return 0;
}
