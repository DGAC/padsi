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

/**
 * Generic utilities
 */
static char *strdup_printf(const char *format, ...);

typedef void(*FreeFunction)(void *);

/**
 * Arrays: each is a block of variable length of the same structure
 */
typedef struct _Array Array;

typedef void(*ArrayForeachFunction)(uint, void *, void *); /* arg1=index of each element arg2=each array element, arg3=foreach_arg */
typedef void*(*ArraySearchFunction)(void *, void *); /* arg1=each array element, arg2=seach_arg */
static Array         *array_new(uint element_size, FreeFunction element_contents_free_func);
static void           array_free(Array *array);
static uint           array_len(Array *array);
static void           array_add(Array *array, void *element);
static void          *array_get_element(Array *array, uint index);
static void           array_del(Array *array, uint index);
static void           array_foreach(Array *array, ArrayForeachFunction foreach_func, void *foreach_arg);
static void          *array_search(Array *array, ArraySearchFunction search_func, void *search_arg, uint* out_index);

/**
 * Data associated to a namespace
 */
typedef struct {
    char *ns_key; /* e.g. "mnt:[4026531841]net:[4026531840]"" */
    char *zone_prefix; /* e.g. "padsi.myzone" */
} NSData;

static void    ns_data_free_c(NSData *ns_data);
static Array  *load_prefixes_file(const char *prefixes_file);

/**
 * Data associated to a process
 */
typedef struct {
    pid_t  pid;
    char  *app_id;
} PIDData;
static void pid_data_free_c(PIDData *pdata);

static pid_t get_ppid(pid_t pid);