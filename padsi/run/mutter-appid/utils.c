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

#include <fcntl.h>
#include <sys/stat.h>
#include <errno.h>
#include <unistd.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <syslog.h>
#include <time.h>

#define DEBUG
#undef DEBUG

/**
 * Format a string into a newly allocted buffer
 */
static char *
strdup_printf(const char *format, ...) {
    /* determine the number of characters of the final string */
    va_list args;
    va_start(args, format);
    int len;
    len=vsnprintf(NULL, 0, format, args);
    va_end(args);

    /* allocate and generate the final string */
    char *res=malloc(sizeof(char)*(len+1));
    if (!res)
        return NULL;

    va_start(args, format);
    vsprintf(res, format, args);
    va_end(args);

    return res;
}

/**
 * Arrays
 */
struct _Array {
  char        *elements; /* generic type, byte aligned */
  uint         used_nb_elements;
  uint         allocated_nb_elements;
  uint         element_size;
  FreeFunction element_contents_free_func;
  char         in_foreach; /* 1 if in a foreach func */
};

#define ARRAY_CHUNK_SIZE 5
static Array *
array_new(uint element_size, FreeFunction element_contents_free_func) {
    Array *array=malloc(sizeof(Array));
    array->element_size=element_size;
    array->used_nb_elements=0;
    array->allocated_nb_elements=ARRAY_CHUNK_SIZE;
    array->elements=malloc(sizeof(char)*array->element_size*array->allocated_nb_elements);
    array->element_contents_free_func=element_contents_free_func;
    array->in_foreach=0;
    return array;
}

static void *
array_get_element(Array *array, uint index) {
    if (index>=array->used_nb_elements)
        return NULL;

    return (void *) &(array->elements[array->element_size*index]);
}

static void
array_free(Array *array) {
    if (array->element_contents_free_func) {
        /* free each element's contents if necessary */
        uint i;
        for (i=0; i<array->used_nb_elements; i++)
            array->element_contents_free_func(array_get_element(array, i));
    }
    free(array->elements);
    free(array);
}

static uint
array_len(Array *array) {
    return array->used_nb_elements;
}

static void
array_add(Array *array, void *element) {
    array->used_nb_elements++;
    if (array->used_nb_elements>array->allocated_nb_elements) {
        /* need more space */
        array->allocated_nb_elements+=ARRAY_CHUNK_SIZE;
        array->elements=realloc(array->elements, sizeof(char)*array->element_size*array->allocated_nb_elements);
    }

    memcpy(array_get_element(array, array->used_nb_elements-1), element, array->element_size);
}

static void
array_del(Array *array, uint index) {
    if (array->in_foreach) {
        syslog(LOG_ERR, "CODEBUG: can't delete elements while performing a foreach function");
        return;
    }
    if (index>=array->used_nb_elements)
        return;
    if (array->element_contents_free_func)
        array->element_contents_free_func(array_get_element(array, index));

    array->used_nb_elements--;
    if (index==array->used_nb_elements)
        return; /* the last element was removed */

    char *src=array->elements+array->element_size*(index+1);
    char *dest=array->elements+array->element_size*index;
    size_t size=array->element_size*(array->used_nb_elements-index);
    memcpy(dest, src, size);

    /* reduce array size if possible */
    if (array->allocated_nb_elements-array->used_nb_elements>=ARRAY_CHUNK_SIZE) {
        array->allocated_nb_elements-=ARRAY_CHUNK_SIZE;
        array->elements=realloc(array->elements, sizeof(char)*array->element_size*array->allocated_nb_elements);
    }
}

static void
array_foreach(Array *array, ArrayForeachFunction foreach_func, void *foreach_arg) {
    uint i;
    char in_foreach=array->in_foreach;
    array->in_foreach=1;
    for (i=0; i<array->used_nb_elements; i++)
        foreach_func(i, array_get_element(array, i), foreach_arg);
    array->in_foreach=in_foreach;
}

static void *
array_search(Array *array, ArraySearchFunction search_func, void *search_arg, uint* out_index) {
    uint i;
    if (out_index)
        *out_index=0; /* safe value */

    for (i=0; i<array->used_nb_elements; i++) {
        void *res=search_func(array_get_element(array, i), search_arg);
        if (res) {
            if (out_index)
                *out_index=i;
            return res;
        }
    }
    return NULL;
}

/**
 * PID'a associated data
 */
static void
pid_data_free_c(__attribute__((unused)) PIDData *pdata) {
    // nothing to do here
}

/**
 * Namespaces' associated data
 */
static void
ns_data_free_c(NSData *ns_data) {
    if (ns_data) {
        free (ns_data->ns_key);
        free (ns_data->zone_prefix);
    }
}

/**
 * (Re)load the contents of the specified file into an Array of NSData
 * Returns a new Array of NSData or the existing one, no transfer of ownership.
 */
static Array *
load_prefixes_file(const char *prefixes_file) {
    /* static data kept between calls */
    static Array *prefixes_array=NULL;
    static struct timespec *prefixes_ts=NULL;
    if (!prefixes_ts)
        prefixes_ts=calloc(1, sizeof(struct timespec));
    if (!prefixes_array)
        prefixes_array=array_new(sizeof(NSData), (FreeFunction) ns_data_free_c);

#ifdef DEBUG
    syslog(LOG_DEBUG, "load_prefixes_file(0) prefixes_ts=%ld", prefixes_ts->tv_sec);
#endif

    /* check if file needs to be re-loaded */
    struct stat statbuf;
    if (stat(prefixes_file, &statbuf)<0) {
        printf("Could not stat prefixes file '%s': %s\n", prefixes_file, strerror(errno));

        return prefixes_array;
    }

    if (prefixes_ts->tv_sec>=statbuf.st_mtim.tv_sec) {
#ifdef DEBUG
        syslog(LOG_DEBUG, "load_prefixes_file(1) no changes (%ld)", statbuf.st_mtim.tv_sec);
#endif
        return prefixes_array;
    }

    /* load the file's contents */
    int fd=open(prefixes_file, O_RDONLY);
    if (fd<0) {
        syslog(LOG_ERR, "Could not open prefixes file '%s': %s", prefixes_file, strerror(errno));
        return prefixes_array;
    }

    Array *array=array_new(sizeof(NSData), (FreeFunction) ns_data_free_c);
    #define FILE_READ_BUF_SIZE 1024
    char buffer[FILE_READ_BUF_SIZE+1];
    char *backlog=NULL;
    int eof_reached=0;
    while (! eof_reached) {
        /* reading some data chunk */
        int nbread=read(fd, buffer, FILE_READ_BUF_SIZE);
        if (nbread<0) {
            syslog(LOG_ERR, "Error reading prefixes file '%s': %s", prefixes_file, strerror(errno));
            array_free(array);
            array=NULL;
            goto out;
        }
        buffer[nbread]=0;
        if (nbread==0) {
            /* EOF reached */
            eof_reached=1;
        }
        else {
            if (backlog) {
                char *nbacklog=strdup_printf("%s%s", backlog, buffer);
                free(backlog);
                backlog=nbacklog;
            }
            else
                backlog=strdup(buffer);
        }
        if (!backlog)
            goto out;

        /* analysing received data */
        while (1) {
            char *ptr;
            for (ptr=backlog; *ptr && *ptr!='='; ptr++);
            if (*ptr=='=') {
                char *eqptr=ptr;
                for (ptr++; *ptr && *ptr!='\n'; ptr++);
                if (eof_reached || (!eof_reached && *ptr=='\n')) {
                    *eqptr=0;
                    char *nptr=*ptr ? ptr : NULL;
                    *ptr=0;
                    NSData ns_data={
                        .ns_key=strdup(backlog),
                        .zone_prefix=strdup(eqptr+1)
                    };
                    array_add(array, &ns_data);
#ifdef DEBUG
                    syslog(LOG_DEBUG, "load_prefixes_file(2) +++ %s = %s", ns_data.ns_key, ns_data.zone_prefix);
#endif

                    if (nptr) {
                        ptr++;
                        memmove(backlog, ptr, strlen(ptr)+1);
                    }
                    else
                        break;
                }
                else
                    break;
            }
            else
                break;
        }
    }

    out:
    free(backlog);
    close(fd);
    if (array) {
        memcpy(prefixes_ts, &statbuf.st_mtime, sizeof(struct timespec));
        array_free(prefixes_array);
        prefixes_array=array;
    }
#ifdef DEBUG
    syslog(LOG_DEBUG, "load_prefixes_file(3) prefixes_ts=%ld", prefixes_ts->tv_sec);
#endif
    return prefixes_array;
}


/**
 * Get the PID of a processus's parent
 * Returns:
 *    <0   on error,
 *    0    if processus is the init process
 *    PPID otherwise
 */
static pid_t
get_ppid(pid_t pid) {
    /* read /proc/<pid>/stat which is a single line formatted as "<pid> (<command>) <state> <ppid> [...]"
     * where <state> is a single char among "RSDZTtWXxKPI"
     * Refer to man 5 proc_pid_stat
     */
     #define PARSE_BUF_SIZE 64
    char path_buf[PARSE_BUF_SIZE];
    char buffer[PARSE_BUF_SIZE*4];
    int res;

    /* create path to /proc/.../stat */
    res=snprintf(path_buf, PARSE_BUF_SIZE, "/proc/%d/stat", pid);
    if (res<0) {
        syslog(LOG_ERR, "Error while creating path to /proc/.../stat: %s", strerror(errno));
        return -1;
    }
    if (res>=PARSE_BUF_SIZE) {
        syslog(LOG_ERR, "CODEBUG: not enough space (%d) to create path to /proc/.../stat", PARSE_BUF_SIZE);
        return -1;
    }

    /* read its contents */
    int fd=open(path_buf, O_RDONLY);
    if (fd<0) {
        syslog(LOG_ERR, "Could not open file '%s': %s", path_buf, strerror(errno));
        return -1;
    }

    int nbread=read(fd, buffer, PARSE_BUF_SIZE-1);
    if (nbread<0) {
        syslog(LOG_ERR, "Error reading file '%s': %s", path_buf, strerror(errno));
        return -1;
    }
    buffer[nbread]=0;
    if (nbread<5) {
        syslog(LOG_ERR, "Improper '%s' format: '%s'", path_buf, buffer);
        return -1;
    }

    /* parse read data */
    char *ptr;
    for (ptr=buffer+1; *(ptr+1) && *(ptr+2); ptr++) {
        if (ptr[-1]==' ' && ptr[1]==' ' && (*ptr=='R' || *ptr=='S' || *ptr=='D' || *ptr=='Z' || *ptr=='T' || *ptr=='t' ||
                                            *ptr=='X' || *ptr=='I')) {
            /* next item will be the PPID */
            char *start=ptr+2;
            for (ptr=start; *ptr && *ptr!=' '; ptr++);
            *ptr=0;
            return atoi(start);
        }
    }
    return -1;
}
