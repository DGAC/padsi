#!/usr/bin/python3

#
# Copyright (c) 2025-2026 DGAC/DSNA
#
# This file is part of PADSI.
#
# This software is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this software.  If not, see <http://www.gnu.org/licenses/>.
#

import json
import os
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

_debug=False

# when this script is run with some data as stdin, it will interpret the data as a json structure to check files are as expected
# otherwise it will behave as a normal Python test script

def check_item(item:dict, parent_dir:str|None=None):
    t=item.get("type")
    name=item.get("name")
    if name is not None and parent_dir is not None:
        name=os.path.join(parent_dir, name)
    match t:
        case "f":
            check_file(name, item.get("mode"), item.get("contents"))
        case "d":
            check_dir(name, item.get("mode"), item.get("files"))
        case _:
            raise Exception(f"CODEBUG: unknown type '{t}'")

def check_file(name:str|None, mode:str|None, contents:str|None):
    if name is None or not os.path.isabs(name) or mode not in ("ro", "rw") or contents is None:
        raise Exception(f"CODEBUG: invalid file item name={name}, mode={mode}, contents={contents}")
    if not os.path.exists(name):
        raise Exception(f"File {name} does not exist")
    if not os.path.isfile(name):
        raise Exception(f"File {name} is not a file")

    st=os.stat(name)
    if mode=="ro" and not (st.st_mode & stat.S_IRUSR and not st.st_mode & stat.S_IWUSR):
        raise Exception(f"Unexpected file '{name}' mode '{stat.filemode(st.st_mode)}', expected {mode}")
    if mode=="rw" and not (st.st_mode & stat.S_IRUSR and st.st_mode & stat.S_IWUSR):
        raise Exception(f"Unexpected file '{name}' mode '{stat.filemode(st.st_mode)}', expected {mode}")

    if contents.startswith("@"):
        # load specified file
        with open(contents[1:], "rt") as fd:
            contents=fd.read()
    with open(name, "rt") as fd:
        rcontents=fd.read()
    if contents!=rcontents:
        raise Exception(f"Unexpected file '{name}' contents")


def check_dir(name:str|None, mode:str|None, contents:list[dict]|None):
    if name is None or not os.path.isabs(name) or mode not in ("ro", "rw") or contents is None:
        raise Exception(f"CODEBUG: invalid dir item name={name}, mode={mode}, contents={contents}")
    if not os.path.exists(name):
        raise Exception(f"File {name} does not exist")
    if not os.path.isdir(name):
        raise Exception(f"File {name} is not a directory")

    st=os.stat(name)
    if mode=="ro" and not (st.st_mode & stat.S_IRUSR and not st.st_mode & stat.S_IWUSR):
        raise Exception(f"Unexpected file '{name}' mode '{stat.filemode(st.st_mode)}', expected {mode}")
    if mode=="rw" and not (st.st_mode & stat.S_IRUSR and st.st_mode & stat.S_IWUSR):
        raise Exception(f"Unexpected file '{name}' mode '{stat.filemode(st.st_mode)}', expected {mode}")

    for item in contents:
        check_item(item, name)

if select.select([sys.stdin, ], [], [], 0.0)[0]:
    # script is passed som data to stdin
    stdin=sys.stdin.read()
    expected=json.loads(stdin)
    for item in expected:
        check_item(item)
    sys.exit(0)


# normal python test script
import nsbubble

script_dir=os.path.realpath(os.path.dirname(__name__))
data_dir=os.path.join(script_dir, "nsbubble-test-data")
bwrap_args=["bwrap", "--ro-bind", "/usr", "/usr",
    "--dir", "/tmp", "--dir", "/var", "--symlink", "../tmp", "var/tmp",
    "--proc", "/proc",
    "--dev", "/dev",
    "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
    "--ro-bind", script_dir, script_dir,
    "--symlink", "usr/lib", "/lib",
    "--symlink", "usr/lib64", "/lib64",
    "--symlink", "usr/bin", "/bin",
    "--symlink", "usr/sbin", "/sbin",
    "--chdir", "/",
    "--unshare-all",
    "--dir", f"/run/user/{os.geteuid()}"
]

def setup_test_data(tmp_dir:str) -> str:
    # copy the contents of the test and chown directories to 755 and file to 644
    # to simulate files which can't be written to. We can't have such permissions in
    # the source code because git does not lile it
    test_data_dir=tmp_dir+"/_"
    shutil.copytree(data_dir, test_data_dir)
    for (dirpath, dirnames, filenames) in os.walk(test_data_dir):
        for item in dirnames:
            os.chmod(os.path.join(dirpath, item), 0o555)
        for item in filenames:
            os.chmod(os.path.join(dirpath, item), 0o444)
    return test_data_dir

def test_firefox(mounts:dict, test_data_dir:str):
    with tempfile.TemporaryDirectory() as t_run_dir:
        with tempfile.TemporaryDirectory() as t_ovl_dir:
            if _debug:
                print(f"{t_run_dir=}")
                print(f"{t_ovl_dir=}")
            bound_dirs=[]
            mpset=nsbubble.MountPointSet.from_specifications(mounts, bound_dirs, t_run_dir)
            bargs:list[str]=[]
            for group in mpset.groups:
                bargs+=group.get_bwrap_args(t_run_dir, t_ovl_dir)
            if _debug:
                print(f"bargs={' '.join(bargs)}")

            if _debug:
                dargs=bwrap_args+bargs+["/bin/tree", "/etc/firefox"]
                proc=subprocess.run(dargs, capture_output=True, text=True)
                print(f"{proc.stdout}")

            test_data_args=["--ro-bind", test_data_dir, test_data_dir]
            args=bwrap_args+test_data_args+bargs+[os.path.realpath(__file__)]

            # test files appear as expected
            expected=[
                {
                    "type": "f",
                    "name": "/etc/firefox/firefox.js",
                    "mode": "ro",
                    "contents": f"@{test_data_dir}/etc/firefox/firefox.js"
                },
                {
                    "type": "d",
                    "name": "/etc/firefox/policies",
                    "mode": "rw",
                    "files": [
                        {
                            "type": "f",
                            "name": "policies.json",
                            "mode": "ro",
                            "contents": f"@{test_data_dir}/etc/padsi/mount-points/myzone/etc_firefox/policies.json"
                        }
                    ]
                }
            ]
            proc=subprocess.run(args, capture_output=True, text=True, input=json.dumps(expected))
            if proc.returncode==0:
                if _debug:
                    print(f"{proc.stdout}")
            else:
                raise Exception(f"FAILED on [{' '.join(args)}] ({proc.returncode}) stderr: {proc.stderr}")

            # test file locations
            # file exists, to be read only
            loc=mpset.file_source_path("/etc/firefox/firefox.js", False)
            exp=f"{test_data_dir}/etc/firefox/firefox.js"
            if loc!=exp:
                raise Exception(f"expected {exp}, got {loc}")
            loc=mpset.file_source_path("/etc/firefox/firefox.js", False)
            if loc!=exp:
                raise Exception(f"expected {exp}, got {loc}")

            # file does not exist, to be read only
            loc=mpset.file_source_path("/etc/firefox/non-existant", False)
            exp=None
            if loc!=exp:
                raise Exception(f"expected {exp}, got {loc}")
            loc=mpset.file_source_path("/etc/firefox/non-existant", False)
            if loc!=exp:
                raise Exception(f"expected {exp}, got {loc}")

            # overwrite an existing file
            loc=mpset.file_source_path("/etc/firefox/firefox.js", True)
            exp=os.path.join(t_run_dir, "etc/firefox/firefox.js")
            if loc!=exp:
                raise Exception(f"expected {exp}, got {loc}")

            # file exists, to be read only
            loc=mpset.file_source_path("/etc/firefox/policies/policies.json", False)
            exp=os.path.join(t_run_dir, "etc/firefox/policies/policies.json") # file has actually been copied to the run_dir
            if loc!=exp:
                raise Exception(f"expected {exp}, got {loc}")

            # file does not exist, to be read only
            loc=mpset.file_source_path("/etc/firefox/policies/non-existant", False)
            exp=None
            if loc!=exp:
                raise Exception(f"expected {exp}, got {loc}")

            # file exists, to be written to
            loc=mpset.file_source_path("/etc/firefox/policies/policies.json", True)
            exp=os.path.join(t_run_dir, "etc/firefox/policies/policies.json") # file has actually been copied to the run_dir
            if loc!=exp:
                raise Exception(f"expected {exp}, got {loc}")

            # file does not exist, to be written to
            loc=mpset.file_source_path("/etc/firefox/policies/non-existant", True)
            exp=os.path.join(t_run_dir, "etc/firefox/policies/non-existant")
            if loc!=exp:
                raise Exception(f"expected {exp}, got {loc}")


def test_chrome(mounts:dict, test_data_dir:str):
    with tempfile.TemporaryDirectory() as t_run_dir:
        with tempfile.TemporaryDirectory() as t_ovl_dir:
            bound_dirs=[]
            mpset=nsbubble.MountPointSet.from_specifications(mounts, bound_dirs, t_run_dir)
            bargs:list[str]=[]
            for group in mpset.groups:
                bargs+=group.get_bwrap_args(t_run_dir, t_ovl_dir)

            if _debug:
                dargs=bwrap_args+bargs+["/bin/tree", "/etc"]
                proc=subprocess.run(dargs, capture_output=True, text=True)
                print(f"{proc.stdout}")

            test_data_args=["--ro-bind", test_data_dir, test_data_dir]
            args=bwrap_args+test_data_args+bargs+[os.path.realpath(__file__)]
            #print(f"{' '.join(args)}")

            # test files appear as expected
            expected=[
                {
                    "type": "d",
                    "name": "/etc/chromium",
                    "mode": "rw",
                    "files": [
                        {
                            "type": "f",
                            "name": "master_preferences",
                            "mode": "ro",
                            "contents": f"@{test_data_dir}/etc/chromium/master_preferences"
                        },
                        {
                            "type": "d",
                            "name": "native-messaging-hosts",
                            "mode": "ro",
                            "files": [
                                {
                                    "type": "f",
                                    "name": "org.gnome.chrome_gnome_shell.json",
                                    "mode": "ro",
                                    "contents": f"@{test_data_dir}/etc/chromium/native-messaging-hosts/org.gnome.chrome_gnome_shell.json"
                                }
                            ]
                        }
                    ]
                },
                {
                    "type": "d",
                    "name": "/etc/chromium.d",
                    "mode": "ro",
                    "files": [
                        {
                            "type": "f",
                            "name": "README",
                            "mode": "ro",
                            "contents": f"@{test_data_dir}/etc/chromium.d/README"
                        }
                    ]
                }
            ]

            proc=subprocess.run(args, capture_output=True, text=True, input=json.dumps(expected))
            if proc.returncode==0:
                if _debug:
                    print(f"{proc.stdout}")
            else:
                raise Exception(f"FAILED on [{' '.join(args)}] ({proc.returncode}) stderr: {proc.stderr}")

            # test file locations
            # overwrite an existing file
            loc=mpset.file_source_path("/etc/chromium/README", True)
            exp=os.path.join(t_run_dir, "etc/chromium/README")
            if loc!=exp:
                raise Exception(f"expected {exp}, got {loc}")

            # overwrite an existing file, lower
            loc=mpset.file_source_path("/etc/chromium/native-messaging-hosts/org.gnome.chrome_gnome_shell.json", True)
            exp=os.path.join(t_run_dir, "etc/chromium/native-messaging-hosts/org.gnome.chrome_gnome_shell.json")
            if loc!=exp:
                raise Exception(f"expected {exp}, got {loc}")

            # read a non existant file
            loc=mpset.file_source_path("/etc/chromium.d/extra", False)
            exp=None
            if loc!=exp:
                raise Exception(f"expected {exp}, got {loc}")
            loc=mpset.file_source_path("/etc/chromium.d/extra", False)
            exp=None
            if loc!=exp:
                raise Exception(f"expected {exp}, got {loc}")

            # write a non existant file
            loc=mpset.file_source_path("/etc/chromium.d/extra", True)
            exp=os.path.join(t_run_dir, "etc/chromium.d/extra")
            if loc!=exp:
                raise Exception(f"expected {exp}, got {loc}")

            # create a file and read it
            loc=mpset.file_source_path("/etc/chromium/extra", True)
            assert(loc is not None)
            test_data="TEST!"
            with open(loc, "wt") as fd:
                fd.write(test_data)

            if _debug:
                dargs=bwrap_args+bargs+["/bin/tree", "/etc/chromium"]
                proc=subprocess.run(dargs, capture_output=True, text=True)
                print(f"{proc.stdout}")

            expected=[
                {
                    "type": "f",
                    "name": "/etc/chromium/extra",
                    "mode": "rw",
                    "contents": test_data
                }
            ]
            proc=subprocess.run(args, capture_output=True, text=True, input=json.dumps(expected))
            if proc.returncode==0:
                if _debug:
                    print(f"{proc.stdout}")
            else:
                raise Exception(f"FAILED on [{' '.join(args)}] ({proc.returncode}) stderr: {proc.stderr}")

class DirsTest(unittest.TestCase):
    def test_firefox(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_data_dir=setup_test_data(tmp_dir)
            mounts={
                f"{test_data_dir}/etc/padsi/mount-points/myzone/etc_firefox": {
                    "mount-point": "/etc/firefox/policies",
                    "read-only": False,
                    "monitored": False
                },
                f"{test_data_dir}/etc/firefox": {
                    "mount-point": "/etc/firefox",
                    "read-only": True,
                    "monitored": False
                },
            }
            test_firefox(mounts, test_data_dir)

    def test_firefox2(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_data_dir=setup_test_data(tmp_dir)
            mounts={
                f"{test_data_dir}/etc/firefox": {
                    "mount-point": "/etc/firefox",
                    "read-only": True,
                    "monitored": False
                },
                f"{test_data_dir}/etc/padsi/mount-points/myzone/etc_firefox": {
                    "mount-point": "/etc/firefox/policies",
                    "read-only": False,
                    "monitored": False
                },
            }
            test_firefox(mounts, test_data_dir)

    def test_chrome(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_data_dir=setup_test_data(tmp_dir)
            mounts={
                f"{test_data_dir}/etc/chromium": {
                    "mount-point": "/etc/chromium",
                    "read-only": False,
                    "monitored": False
                },
                f"{test_data_dir}/etc/chromium.d": {
                    "mount-point": "/etc/chromium.d",
                    "read-only": True,
                    "monitored": False
                }
            }
            test_chrome(mounts, test_data_dir)

    def test_chrome2(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_data_dir=setup_test_data(tmp_dir)
            mounts={
                f"{test_data_dir}/etc/chromium.d": {
                    "mount-point": "/etc/chromium.d",
                    "read-only": True,
                    "monitored": False
                },
                f"{test_data_dir}/etc/chromium": {
                    "mount-point": "/etc/chromium",
                    "read-only": False,
                    "monitored": False
                }
            }
            test_chrome(mounts, test_data_dir)

if __name__=='__main__':
    unittest.main()
