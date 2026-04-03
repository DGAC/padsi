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

#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>

#include "utils.h"
#include "utils.c"

void
print_PIDData_element_func(uint index, PIDData *pdata, void *arg) {
    printf("==> @%d PID %d, zone %s\n", index, pdata->pid, pdata->app_id);
}

void
print_NSData_element_func(uint index, NSData *nsdata, void *arg) {
    printf("==> @%d '%s' -> '%s'\n", index, nsdata->ns_key, nsdata->zone_prefix);
}

PIDData *
pid_data_search(PIDData *element, int* searched_pid_p) {
    if (element->pid==*searched_pid_p)
        return element;
    return NULL;
}

NSData *
ns_data_search(NSData *element, char *ns_key) {
    if (!strcmp(element->ns_key, ns_key))
        return element;
    return NULL;
}

void test_array() {
    PIDData pdata={
        .pid=1,
        .app_id=strdup("pasdi.myzone1")
    };
    Array *array=array_new(sizeof(PIDData), (FreeFunction) pid_data_free_c);
    printf("Empty array contents (len: %d):\n", array_len(array));
    array_foreach(array, (ArrayForeachFunction) print_PIDData_element_func, NULL);

    array_add(array, &pdata);
    printf("Array contents (len: %d):\n", array_len(array));
    array_foreach(array, (ArrayForeachFunction) print_PIDData_element_func, NULL);

    uint index;
    for (index=2; index<12; index++) {
        pdata.pid=index;
        pdata.app_id=strdup_printf("padsi.myzone%d", index);
        array_add(array, &pdata);
    }
    printf("Array contents (len: %d):\n", array_len(array));
    array_foreach(array, (ArrayForeachFunction) print_PIDData_element_func, NULL);

    for (index=2; index<8; index++) {
        array_del(array, 2);
        printf("Array contents (removed element @index 2) (len: %d):\n", array_len(array));
        array_foreach(array, (ArrayForeachFunction) print_PIDData_element_func, NULL);
    }

    if (array_len(array)!=5) {
        printf("Got %d entries, expected 5\n", array_len(array));
        exit(1);
    }

    /* search */
    int searched_pid=9;
    PIDData *entry=array_search(array, (ArraySearchFunction) pid_data_search, (void *)&searched_pid, NULL);
    if (entry)
        printf("Found entry with PID %d: %s\n", searched_pid, entry->app_id);
    else {
        printf("Entry with PID %d not found :-(\n", searched_pid);
        exit(1);
    }

    array_free(array);
}

void test_parsing() {
    Array *array=load_prefixes_file("prefixes");
    if (array) {
        printf("Array contents (len: %d):\n", array_len(array));
        array_foreach(array, (ArrayForeachFunction) print_NSData_element_func, NULL);

        if (array_len(array)!=2) {
            printf("Got %d entries, expected 2\n", array_len(array));
            exit(1);
        }

        NSData *nsdata;
        char *ns_key="mnt:[5673531841]net:[4026796220]";
        nsdata=array_search(array, (ArraySearchFunction) ns_data_search, ns_key, NULL);
        if (!nsdata) {
            printf("Entry with ns_key '%s' not found :-(\n", ns_key);
            exit(1);
        }

        array_free(array);
    }
    else
        printf("Failed to parse prefixes file");
}

void test_ppid() {
    pid_t pid=getpid();
    pid_t ppid=getppid();
    pid_t cppid=get_ppid(pid);
    printf("Process PID is %d, PPID is %d, read is %d\n", pid, ppid, cppid);
    if (ppid!=cppid) {
        printf("Failed to get PPID of current process: got %d, expected %d\n", ppid, cppid);
        exit(1);
    }
    while(cppid>=0) {
        printf("PID %d, PPID: %d\n", pid, cppid);
        pid=cppid;
        cppid=get_ppid(pid);
    }
}

int main() {
    test_array();
    test_parsing();
    test_ppid();

    printf("OK\n");
}