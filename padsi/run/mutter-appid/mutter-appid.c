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

/*
 * This program is intended to be "run" using LD_PRELOAD
 */

#ifndef _GNU_SOURCE
    #define _GNU_SOURCE
#endif

#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <stdint.h>
#include <dlfcn.h>
#include <stdlib.h>
#include <syslog.h>
#include <errno.h>
#include <unistd.h>

#include "utils.h"
#include "utils.c"

#define DEBUG
#undef DEBUG

#define ZONES_PREFIXES_FILE "/run/padsi/zones-infos/prefixes.txt"

/* symbols actually used from Mutter's library */
typedef void MetaWindow;
pid_t (*_real_meta_window_get_pid)(MetaWindow *)=NULL;
const char *(*_real_meta_window_get_wm_class) (MetaWindow *)=NULL;
const char *(*_real_meta_window_get_wm_class_instance) (MetaWindow *)=NULL;
const char *(*_real_meta_window_get_gtk_application_id) (MetaWindow *)=NULL;
const char *(*_real_meta_window_get_sandboxed_app_id) (MetaWindow *)=NULL;
#define NB_OVERRIDE_FUNCS 4

/**
 * init function
 */
void __attribute__((constructor)) _padsi_mutter_appid_init(void) {
    /* load symbols */
    _real_meta_window_get_pid=(pid_t (*)(MetaWindow *)) dlsym(RTLD_NEXT, "meta_window_get_pid");
    _real_meta_window_get_wm_class=(const char *(*) (MetaWindow *)) dlsym(RTLD_NEXT, "meta_window_get_wm_class");
    _real_meta_window_get_wm_class_instance=(const char *(*) (MetaWindow *)) dlsym(RTLD_NEXT, "meta_window_get_wm_class_instance");
    _real_meta_window_get_gtk_application_id=(const char *(*) (MetaWindow *)) dlsym(RTLD_NEXT, "meta_window_get_gtk_application_id");
    _real_meta_window_get_sandboxed_app_id=(const char *(*) (MetaWindow *)) dlsym(RTLD_NEXT, "meta_window_get_sandboxed_app_id");
}

/**
 * Get the PADSI zone prefix from the mount+net namespaces (concatenated as ns_key)
 */
static NSData *
ns_data_search(NSData *element, char *ns_key) {
    if (!strcmp(element->ns_key, ns_key))
        return element;
    return NULL;
}
static const char *
_get_zone_prefix_from_namespaces_key(const char *ns_key) {
    Array *prefixes_array=load_prefixes_file(ZONES_PREFIXES_FILE);
    NSData *ns_data=array_search(prefixes_array, (ArraySearchFunction) ns_data_search, (void *) ns_key, NULL);
#ifdef DEBUG
    syslog(LOG_DEBUG, "_get_zone_prefix_from_namespaces_key(%s) ==> %s", ns_key, ns_data ? ns_data->zone_prefix : NULL);
#endif
    return ns_data ? ns_data->zone_prefix : NULL;
}

/**
 * Compute a masked app ID for the window based on the associated PID
 */
static const char *
_compute_padsi_prefix(pid_t pid) {
    if (pid) {
        #define PARSE_BUF_SIZE 64
        char path_buf[PARSE_BUF_SIZE];
        char key_buf[PARSE_BUF_SIZE]; /* to store the 'namespaces key' to search for the corresponding PADSI zone prefix */
        int res;

        /* getting the mount namespace */
        res=snprintf(path_buf, PARSE_BUF_SIZE, "/proc/%d/ns/mnt", pid);
        if (res<0) {
            syslog(LOG_ERR, "Error while creating path to /proc mnt namespace: %s", strerror(errno));
            return NULL;
        }
        if (res>=PARSE_BUF_SIZE) {
            syslog(LOG_ERR, "CODEBUG: not enough space (%d) to create path to /proc mnt namespace", PARSE_BUF_SIZE);
            return NULL;
        }
        ssize_t index;
        index=readlink(path_buf, key_buf, PARSE_BUF_SIZE);
        if (index<0) {
            if (errno!=EACCES)
                syslog(LOG_ERR, "Error while readlink the mnt namespace '%s': %s (%d)", path_buf, strerror(errno), errno);
            return NULL;
        }
        if (index>=PARSE_BUF_SIZE) {
            syslog(LOG_ERR, "CODEBUG: not enough space (%d) to readlink the mnt namespace '%s'", PARSE_BUF_SIZE, path_buf);
            return NULL;
        }

        /* getting the net namespace */
        res=snprintf(path_buf, PARSE_BUF_SIZE, "/proc/%d/ns/net", pid);
        if (res<0) {
            syslog(LOG_ERR, "Error while creating path to /proc net namespace: %s", strerror(errno));
            return NULL;
        }
        if (res>=PARSE_BUF_SIZE) {
            syslog(LOG_ERR, "CODEBUG: not enough space (%d) to create path to /proc net namespace", PARSE_BUF_SIZE);
            return NULL;
        }
        ssize_t index2;
        index2=readlink(path_buf, key_buf+index, PARSE_BUF_SIZE-index);
        if (index2<0) {
            syslog(LOG_ERR, "Error while readlink the net namespace '%s': %s", path_buf, strerror(errno));
            return NULL;
        }
        if (index2>=PARSE_BUF_SIZE-index) {
            syslog(LOG_ERR, "CODEBUG: not enough space (%d) to readlink the net namespace '%s'", PARSE_BUF_SIZE, path_buf);
            return NULL;
        }
        key_buf[index+index2]=0;

        /* get the prefix */
        return _get_zone_prefix_from_namespaces_key(key_buf);
    }
    return NULL;
}

/**
 * Remove any entry for a PID which does not exist anymore
 */
static void
pid_foreach_cleanup(uint index, PIDData *element, Array *pids_to_remove) {
    struct stat statbuf;
    char *path=strdup_printf("/proc/%d", element->pid);
    if (stat(path, &statbuf)<0 && errno==ENOENT ) {
        /* process is not present anymore */
        array_add(pids_to_remove, &(index));
    }
    free(path);
}

static void
pids_cleanups(Array *zone_pids) {
    Array *pids_to_remove=array_new(sizeof(uint), NULL);
    array_foreach(zone_pids, (ArrayForeachFunction) pid_foreach_cleanup, pids_to_remove);
    if (array_len(pids_to_remove)>0) {
        uint i;
        for (i=0; i<array_len(pids_to_remove); i++) {
            uint *index;
            index=array_get_element(pids_to_remove, i);
            array_del(zone_pids, *index);
        }
    }
    array_free(pids_to_remove);
}

/**
 * Generate and keep ownership of an app ID taking into account the process's namespaces from the PID.
 * Returns: a string (which may be same as the passed @actual_app_id)
 */
PIDData *
pid_data_search(PIDData *element, pid_t* searched_pid_p) {
    if (element->pid==*searched_pid_p)
        return element;
    return NULL;
}
static const char *
_customize_app_id(MetaWindow *window, int index, const char *actual_app_id) {
    /* static data held between calls */
    static Array *zones_pids_arrays[NB_OVERRIDE_FUNCS]={NULL, NULL, NULL, NULL};
    Array *zone_pids=zones_pids_arrays[index];
    static char *last_result=NULL;

    /* actual function implementation */
    if (! actual_app_id)
        return NULL;

    if (_real_meta_window_get_pid) {
        if (! zone_pids) {
            zone_pids=array_new(sizeof(PIDData), (FreeFunction) pid_data_free_c);
            zones_pids_arrays[index]=zone_pids;
        }

        pids_cleanups(zone_pids);

        pid_t pid=_real_meta_window_get_pid(window);
        if (pid>0) {
            const char *prefix=NULL;

            /* search if work has already been done */
            PIDData *edata=array_search(zone_pids, (ArraySearchFunction) pid_data_search, (void *)& pid, NULL);
            if (edata) {
#ifdef DEBUG
                syslog(LOG_DEBUG, "_customize_app_id(pid:%d) ==> already got prefix %s", pid, edata->zone_prefix);
#endif
                prefix=edata->zone_prefix;
            }
            else {
                /* compute prefix, possibly using the namespaces of the parent processes to
                * take into account sub-namespaces (like when Flatpak apps. are run)
                */
                pid_t tpid=pid; /* actual PID used to get the prefix */
                while(tpid>0 && !prefix) {
                    prefix=_compute_padsi_prefix(tpid);
                    if (!prefix)
                        tpid=get_ppid(tpid);
                }
                /*
                 * keep the prefix for next time we get to use the same PID
                 */
                if (prefix) {
                    PIDData pid_data={
                        .pid=pid,
                        .zone_prefix=prefix
                    };
                    array_add(zone_pids, &pid_data);
                }
#ifdef DEBUG
                syslog(LOG_DEBUG, "_customize_app_id(pid:%d) ==> computed prefix %s", pid, prefix);
#endif
            }

            if (prefix) {
                char *new_app_id=strdup_printf("%s.%s", prefix, actual_app_id);
                if (last_result)
                    free(last_result);
                last_result=new_app_id;
#ifdef DEBUG
                syslog(LOG_DEBUG, "_customize_app_id(pid:%d) ==> %s", pid, last_result);
#endif
                return last_result;
            }
        }
    }
    else
        syslog(LOG_WARNING, "The _real_meta_window_get_pid symbol was not found.");
    return actual_app_id;
}

const char *
meta_window_get_wm_class(MetaWindow *window)
{
    if (_real_meta_window_get_wm_class) {
        const char *actual_app_id=_real_meta_window_get_wm_class(window);
        const char *app_id=_customize_app_id(window, 0, actual_app_id);
#ifdef DEBUG
        syslog(LOG_DEBUG, "meta_window_get_wm_class() %s ==> %s", actual_app_id, app_id);
#endif
        return app_id;
    }
    syslog(LOG_ERR, "The meta_window_get_wm_class symbol was not found, yet the it has been called, expect trouble.");
    return NULL;
}

const char *
meta_window_get_wm_class_instance(MetaWindow *window) {
    if (_real_meta_window_get_wm_class_instance) {
        const char *actual_app_id=_real_meta_window_get_wm_class_instance(window);
        const char *app_id=_customize_app_id(window, 1, actual_app_id);
#ifdef DEBUG
        syslog(LOG_DEBUG, "meta_window_get_wm_class_instance() %s ==> %s", actual_app_id, app_id);
#endif
        return app_id;
    }
    syslog(LOG_ERR, "The meta_window_get_wm_class_instance symbol was not found, yet the it has been called, expect trouble.");
    return NULL;
}

const char *
meta_window_get_gtk_application_id (MetaWindow *window) {
    if (_real_meta_window_get_gtk_application_id) {
        const char *actual_app_id=_real_meta_window_get_gtk_application_id(window);
        const char *app_id=_customize_app_id(window, 2, actual_app_id);
#ifdef DEBUG
        syslog(LOG_DEBUG, "meta_window_get_gtk_application_id() %s ==> %s", actual_app_id, app_id);
#endif
        return app_id;
    }
    syslog(LOG_ERR, "The meta_window_get_gtk_application_id symbol was not found, yet the it has been called, expect trouble.");
    return NULL;
}

const char *
meta_window_get_sandboxed_app_id (MetaWindow *window) {
    if (_real_meta_window_get_sandboxed_app_id) {
        const char *actual_app_id=_real_meta_window_get_sandboxed_app_id(window);
        const char *app_id=_customize_app_id(window, 3, actual_app_id);
#ifdef DEBUG
        syslog(LOG_DEBUG, "meta_window_get_sandboxed_app_id() %s ==> %s", actual_app_id, app_id);
#endif
        return app_id;
    }
    syslog(LOG_ERR, "The meta_window_get_sandboxed_app_id symbol was not found, yet the it has been called, expect trouble.");
    return NULL;
}
