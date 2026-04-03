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
#include <sys/types.h>
#include <sys/stat.h>
#include <linux/netlink.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <syslog.h>
#include <errno.h>
#include "common.h"

#define SOCK_PATH "host-netlink.sock"

static int send_fd(int sock, int fd_to_send) {
    struct msghdr msg = {0};
    struct iovec iov;
    char buf[1] = {0};
    char cbuf[CMSG_SPACE(sizeof(int))];

    iov.iov_base = buf;
    iov.iov_len = 1;
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    msg.msg_control = cbuf;
    msg.msg_controllen = sizeof(cbuf);

    struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
    cmsg->cmsg_level = SOL_SOCKET;
    cmsg->cmsg_type = SCM_RIGHTS;
    cmsg->cmsg_len = CMSG_LEN(sizeof(int));
    *((int *) CMSG_DATA(cmsg)) = fd_to_send;

    return sendmsg(sock, &msg, 0);
}

int main(int argc, const char**argv) {
    if (argc!=2) {
        syslog(LOG_ERR, "Usage: %s <user session directory>", argv[0]);
        return 1;
    }
    #define PATH_SIZE 512
    char socket_path[PATH_SIZE];
    if (snprintf(socket_path, PATH_SIZE, "%s/padsi-netlink.sock", argv[1])>=PATH_SIZE) {
        syslog(LOG_ERR, "Could not create socket_path: size > %d", PATH_SIZE);
        return 1;
    }

    /* remove any previous socket */
    unlink(socket_path);

    /* start listening on a Unix socket */
    int listen_fd, cl;
    struct sockaddr_un addr;
    listen_fd=socket(AF_UNIX, SOCK_STREAM, 0);
    if (listen_fd<0) {
        syslog(LOG_ERR, "Could not create Unix socket: %s", strerror(errno));
        return 1;
    }

    memset(&addr, 0, sizeof(addr));
    addr.sun_family=AF_UNIX;
    strncpy(addr.sun_path, socket_path, sizeof(addr.sun_path) - 1);
    if (bind(listen_fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        syslog(LOG_ERR, "Could not bind to Unix socket '%s': %s", socket_path, strerror(errno));
        return 1;
    }
    chmod(socket_path, 0660);
    if (listen(listen_fd, 5) < 0) {
        syslog(LOG_ERR, "Could not listen to Unix socket '%s': %s", socket_path, strerror(errno));
        return 1;
    }

    /* handle incoming connections */
    for (;;) {
        cl = accept(listen_fd, NULL, NULL);
        if (cl < 0) {
            syslog(LOG_ERR, "Could not accept client connection on Unix socket '%s': %s", socket_path, strerror(errno));
            continue;
        }

        /* get client's credentials */
        struct ucred cred;
        socklen_t cred_len = sizeof(cred);
        if (getsockopt(cl, SOL_SOCKET, SO_PEERCRED, &cred, &cred_len) == -1) {
            syslog(LOG_ERR, "Could not getsockopt(SO_PEERCRED) from client connection on Unix socket '%s': %s", socket_path, strerror(errno));
            close(cl);
            continue;
        }

        /* get the requested socket parameters */
        struct SocketRequestInfo req;
        ssize_t r = recv(cl, &req, sizeof(req), MSG_WAITALL);
        if (r != sizeof(req)) {
            syslog(LOG_ERR, "Invalid request size from client");
            close(cl);
            continue;
        }
        syslog(LOG_INFO, "Got a request for netlink socket (domain %d, type %d, protocol %d) from user %d:%d and PID %d",
               req.domain, req.type, req.protocol, cred.uid, cred.gid, cred.pid);
        if (req.domain!=AF_NETLINK || req.protocol!=NETLINK_KOBJECT_UEVENT) {
            syslog(LOG_ERR, "Invalid request domain %d, type %d, protocol %d", req.domain, req.type, req.protocol);
            close(cl);
            continue;
        }

        /* Open the socket on behalf of the client */
        int nl=socket(req.domain, req.type, req.protocol);
        if (nl<0) {
            syslog(LOG_ERR, "Could not open a netlunk socket (domain %d, type %d, protocol %d): %s", req.domain, req.type, req.protocol, strerror(errno));
            close(cl);
            continue;
        }

        /* send the FD to the client */
        if (send_fd(cl, nl) < 0) {
            syslog(LOG_ERR, "Could not pass FD %d back to Unix socket '%s': %s", nl, socket_path, strerror(errno));
        }

        close(nl);
        close(cl);
    }

    close(listen_fd);
    unlink(socket_path);
    return 0;
}
