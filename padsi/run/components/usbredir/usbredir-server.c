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
#include <sys/wait.h>
#include <syslog.h>

static int recv_fds(int sock, int *fds, int nfds) {
    struct msghdr msg = {0};
    char m_buffer[1];
    struct iovec io = { .iov_base = m_buffer, .iov_len = sizeof(m_buffer) };

    char cmsgbuf[CMSG_SPACE(nfds * sizeof(int))];
    memset(cmsgbuf, 0, sizeof(cmsgbuf));

    msg.msg_iov = &io;
    msg.msg_iovlen = 1;
    msg.msg_control = cmsgbuf;
    msg.msg_controllen = sizeof(cmsgbuf);

    ssize_t n = recvmsg(sock, &msg, 0);
    if (n <= 0) {
        return -1;
    }

    struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
    if (cmsg == NULL ||
        cmsg->cmsg_level != SOL_SOCKET ||
        cmsg->cmsg_type != SCM_RIGHTS) {
        fprintf(stderr, "Did not receive file descriptors\n");
        return -1;
    }

    memcpy(fds, CMSG_DATA(cmsg), nfds * sizeof(int));
    return 0;
}

int main(int argc, char **argv) {
    int server_fd, client_fd;
    struct sockaddr_un addr;

    if (argc!=2) {
        syslog(LOG_ERR, "Usage: %s <socket path>", argv[0]);
        return 1;
    }
    const char *socket_path=argv[1];
    unlink(socket_path);

    server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (server_fd == -1) {
        perror("socket");
        exit(1);
    }

    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, socket_path, sizeof(addr.sun_path) - 1);

    if (bind(server_fd, (struct sockaddr*)&addr, sizeof(addr)) == -1) {
        perror("bind");
        close(server_fd);
        exit(1);
    }

    if (listen(server_fd, 1) == -1) {
        perror("listen");
        close(server_fd);
        exit(1);
    }

    printf("Server listening on %s\n", socket_path);

    while (1) {
        client_fd = accept(server_fd, NULL, NULL);
        if (client_fd == -1) {
            perror("accept");
            continue;
        }

        int fds[3];
        if (recv_fds(client_fd, fds, 3) == -1) {
            perror("recv_fds");
            close(client_fd);
            continue;
        }

        printf("Received fds: %d %d %d\n", fds[0], fds[1], fds[2]);

        pid_t pid = fork();
        if (pid == -1) {
            perror("fork");
            close(client_fd);
            continue;
        }

        if (pid == 0) {
            // Child process: wire received FDs to stdin/stdout/stderr
            if (dup2(fds[0], STDIN_FILENO) == -1) {
                perror("dup2 stdin");
            }
            if (dup2(fds[1], STDOUT_FILENO) == -1) perror("dup2 stdout");
            if (dup2(fds[2], STDERR_FILENO) == -1) perror("dup2 stderr");

            // Close extra FDs
            close(client_fd);
            close(server_fd);
            for (int i = 0; i < 3; i++)
                if (fds[i] > 2) close(fds[i]);

            execlp("/usr/libexec/spice-client-glib-usb-acl-helper", "/usr/libexec/spice-client-glib-usb-acl-helper", NULL);
            perror("execlp");
            _exit(1);
        } else {
            // Parent process
            close(client_fd);
            for (int i = 0; i < 3; i++)
                close(fds[i]);

            int status;
            waitpid(pid, &status, 0);
            printf("Child exited with status %d\n", status);
        }
    }

    close(server_fd);
    unlink(socket_path);
    return 0;
}
