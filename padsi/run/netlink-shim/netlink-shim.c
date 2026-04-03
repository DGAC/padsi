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
#include <dlfcn.h>
#include <linux/netlink.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>
#include <syslog.h>
#include <stdio.h>
#include "common.h"

int (*_real_socket)(int, int, int)=NULL;
static const char *HOST_SOCK = "/bubble/run/padsi-netlink.sock";

/**
 * init function
 */
void __attribute__((constructor)) _padsi_netlink_init(void) {
    /* load symbols */
    _real_socket=(int (*)(int, int, int)) dlsym(RTLD_NEXT, "socket");
}

static int
get_fd_from_helper(int domain, int type, int protocol, int *out_fd) {
    int s=socket(AF_UNIX, SOCK_STREAM, 0);
    if (s<0)
        return -1;

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family=AF_UNIX;
    strncpy(addr.sun_path, HOST_SOCK, sizeof(addr.sun_path) - 1);

    if (connect(s, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        close(s);
        return -1;
    }

    /* Send the requested socket parameters */
    struct SocketRequestInfo req={ domain, type, protocol };
    if (write(s, &req, sizeof(req))!=sizeof(req)) {
        close(s);
        return -1;
    }

    /* Receive the netlink socket FD */
    struct msghdr msg={0};
    char m_buffer[1];
    struct iovec iov={
        .iov_base=m_buffer,
        .iov_len=sizeof(m_buffer)
    };
    char cmsgbuf[CMSG_SPACE(sizeof(int))];
    msg.msg_iov=&iov;
    msg.msg_iovlen=1;
    msg.msg_control=cmsgbuf;
    msg.msg_controllen=sizeof(cmsgbuf);

    ssize_t n=recvmsg(s, &msg, 0);
    if (n<=0) {
        close(s);
        return -1;
    }
    struct cmsghdr *cmsg=CMSG_FIRSTHDR(&msg);
    if (!cmsg || cmsg->cmsg_len!=CMSG_LEN(sizeof(int)) ||
        cmsg->cmsg_level!=SOL_SOCKET || cmsg->cmsg_type!=SCM_RIGHTS) {
        close(s);
        return -1;
    }

    int fd=*((int *) CMSG_DATA(cmsg));
    close(s);
    *out_fd=fd;
    return 0;
}

int socket(int domain, int type, int protocol) {
    if (domain==AF_NETLINK) {
        if (protocol==NETLINK_KOBJECT_UEVENT) {
            int fd;
            if (get_fd_from_helper(domain, type, protocol, &fd)==0) {
                return fd;
            } else {
                errno=ENOENT;
                return -1;
            }
        }
        //else
        //    syslog(LOG_DEBUG, "AF_NETLINK requested with protocol %d, no proxy used", protocol);
    }
    return _real_socket(domain, type, protocol);
}
