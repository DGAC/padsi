#
# Copyright (c) 2025-2026 DGAC/DSNA
# Copyright (c) 2024 Vivien Malerba <vmalerba@gmail.com>
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

from __future__ import annotations

import os
import re
import subprocess
import syslog
import time
from dataclasses import dataclass

import psutil

import nsbubble


def _get_qemu_version() -> tuple[int,int]:
    """Determine QEMU's version
    Returns a (major, minor) version
    """
    p=subprocess.run(["qemu-system-x86_64", "--version"], capture_output=True, text=True)
    if p.returncode!=0:
        raise Exception(f"Could not determine QEMU's installed version: {p.stderr}")
    # p.stdout will be like:
    #   QEMU emulator version 7.2.15 (Debian 1:7.2+dfsg-7+deb12u12)
    #   Copyright (c) 2003-2022 Fabrice Bellard and the QEMU Project developers
    try:
        (line, *_)=p.stdout.split("\n")
        if "version" not in line:
            raise Exception()
        (_, version, *_)=line[line.index("version"):].split()
        (maj, min, *_)=version.split(".")
        return (int(maj), int(min))
    except Exception:
        raise Exception(f"Unhandled QEMU version {p.stdout}")

class QEMUImageFile:
    """Represents a QEMU image file in the QCOW2 format"""
    def __init__(self, filename:str):
        self._image_file=os.path.realpath(filename)
        self._size_bytes:int|None=None
        self._backing_image:str|None=None
        self._backing_object:QEMUImageFile|None=None
        self._analyse()

    @classmethod
    def create(cls, filename:str, size_mb:int) -> QEMUImageFile:
        """Create a new QEMU image file"""
        args=["qemu-img", "create", "-f", "qcow2", filename, f"{size_mb}M"]
        res=subprocess.run(args, capture_output=True)
        if res.returncode!=0:
            raise Exception(f"Could not create '{filename}': {res.stderr.decode()}")
        return QEMUImageFile(filename)

    def _analyse(self):
        args=["qemu-img", "info", "-U", self._image_file]
        res=subprocess.run(args, capture_output=True)
        if res.returncode!=0:
            raise Exception(f"Could not analyse '{self._image_file}': {res.stderr.decode()}")

        for line in res.stdout.decode().splitlines():
            if line.startswith("file format: ") and line[13:]!="qcow2":
                raise Exception(f"Unhandled file format {line[13:]}")
            elif line.startswith("virtual size: "):
                m=re.search(r"([0-9]*) bytes\)$", line)
                if m is None:
                    raise Exception(f"CODEBUG: unexpected virtual size line '{line}'")
                self._size_bytes=int(m.groups()[0])
            elif line.startswith("backing file: "):
                m=re.search(r"actual path: (.*)\)$", line)
                if m is not None:
                    # line ex.: backing file: file.img (actual path: /path/to/file.img)
                    self._backing_image=m.groups()[0]
                else:
                    # line ex.: backing file: /path/to/file.img
                    self._backing_image=line[14:]

    @property
    def image_file_name(self) -> str:
        return self._image_file

    @property
    def backing_image_file_name(self) -> str|None:
        return self._backing_image

    @property
    def image_directory_name(self) -> str:
        return os.path.dirname(self._image_file)

    @property
    def size_bytes(self) -> int|None:
        """The size in bytes of the virtual disk, not of the file itself"""
        return self._size_bytes

    @property
    def backing(self) -> QEMUImageFile|None:
        """The QEMUImageFile object representing the backing file"""
        if self._backing_object is None and self._backing_image:
            self._backing_object=QEMUImageFile(self._backing_image)
        return self._backing_object

    def rename_backing_file(self, new_backing_file:str) -> QEMUImageFile:
        """Update the QEMU image file to point to the new backing file
        Use this operation after having renamed the original backing file
        """
        args=["qemu-img", "rebase", "-u", "-F", "qcow2", "-b", new_backing_file, self._image_file]
        res=subprocess.run(args, capture_output=True)
        if res.returncode!=0:
            raise Exception(f"Failed to rename backing file: {res.stderr}")
        return QEMUImageFile(new_backing_file)

    def create_snapshot(self, filename:str, exist_ok=False) -> QEMUImageFile:
        """Create a snapshot in the speficied file.
        If the snapshot file already exists, and exist_of is False, an exception is raised, otherwise the existing
        snapshot file is removed first"""
        if os.path.exists(filename):
            if exist_ok:
                os.remove(filename)
            else:
                raise Exception(f"File '{filename}' already exists")

        args=["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", self._image_file, filename]
        res=subprocess.run(args, capture_output=True)
        if res.returncode!=0:
            raise Exception(f"Failed to create snapshot: {res.stderr}")
        return QEMUImageFile(filename)

    def commit(self):
        """If the QEMU image file has a backing store, commit the modifications which may have been made"""
        if self.backing is None:
            raise Exception("Image file does not have any backing image")
        args=["qemu-img", "commit", "-d", self._image_file]
        res=subprocess.run(args, capture_output=True)
        if res.returncode!=0:
            raise Exception(f"Failed to commit: {res.stderr}")
        os.remove(self._image_file)

    def get_backing_files_names(self) -> list[str]:
        """Get the list of all the backing files
        in the following order:
        - position 0: the base QEMU image file
        - position 1: the QEMU image file which backend is the file at position 0
        ...
        - position N: the QEMU image file which is the backend of the current object
        """
        res=[]
        obj=self.backing
        while obj is not None:
            res.append(obj.image_file_name)
            obj=obj.backing
        res.reverse()
        return res

    def get_backing_directories(self) -> list[str]:
        """Get the list of directories containing backing images of the current object,
        in no particular order
        """
        dirs=[]
        obj=self.backing
        while obj is not None:
            dir=os.path.dirname(obj.image_file_name)
            if dir not in dirs:
                dirs.append(dir)
            obj=obj.backing
        return dirs

    def shrink(self, tmp_dir:str|None=None) -> float|None:
        """Shrink the VM image file.
        This may take some time
        Returns: the <new file size>/<old file size> as a percentage or None if the current file size is 0
        """
        if self._size_bytes==0 or self._size_bytes is None:
            return None
        csize=self._size_bytes

        if tmp_dir is None:
            args=["virt-sparsify"]
        else:
            args=["virt-sparsify", "--tmp", tmp_dir]

        smallerfile=f"{self._image_file}.shrinked"
        args+=["--compress", self._image_file, smallerfile]
        prog=subprocess.run(args, capture_output=True)
        if prog.returncode!=0:
            try:
                os.remove(smallerfile)
            except Exception:
                pass
            raise Exception(f"Could shrink '{self._image_file}': {prog.stderr.decode()}")
        os.rename(smallerfile, self._image_file)
        self._analyse()
        return self._size_bytes*100/csize

@dataclass
class VirtioSharedDirectory():
    """Represent a directory shared using a virtiofs daemon (which is expected to already being running)
    In the VM, the shared directory will be mountable using "mount -t virtiofs <fsname> ..."
    """
    fsname: str
    socket_path: str

class NotEnoughMemory(Exception):
    pass

@dataclass
class VMSpecs():
    """Represent some VM specifications
    NB:
    - the directory in which both of these files are does not need to be writable
    """
    disk_size_mb:int=30000  # HDD size in Mb
    mem_mb:int=1024         # RAM size in Mb
    nb_cpu:int=2            # number of vCPUs
    net_type:str|None=None  # None for no networking
                            # "user" for user mode networking (slirp)
                            # "tap:<interface>" for TAP networking using the specified host's interface
    graphical_device:bool=True # True if the VM has some sort of UI component (to be used with a viewer)
    secure_boot:bool=True   # is SecureBoot enabled?

    @property
    def tap_iface(self) -> str|None:
        """Get the TAP network interface name, or None if not TAP interface is configured"""
        if self.net_type is not None and self.net_type.startswith("tap:"):
            return self.net_type[4:]

class BubbleVM(nsbubble.Bubble):
    """Creates a bubble in which a QEMU based virtual machine will be executed
    The VM image file is expected as an input, the VM installation itself is not handled here.
    """
    @staticmethod
    def get_ovmf_files(secure_boot:bool=True) -> tuple[str,str]: # CODE and VARS file
        if secure_boot:
            return ("/usr/share/OVMF/OVMF_CODE_4M.ms.fd", "/usr/share/OVMF/OVMF_VARS_4M.ms.fd")
        return ("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd")

    def __init__(self, image_file:str, vars_file:str, vm_spec:VMSpecs, features:nsbubble.Features, boot_iso:str|None=None, extra_isos:list[str]|None=None,
                 vfs_dirs:list[VirtioSharedDirectory]|None=None, run_dir:str|None=None):
        """Create the BubbleVM object.
        NB:
        - the image_file argument must be a path to a preferably QCow VM HDD file, write access is not needed
        - the vars_file argument must be a path an OVMF NVRAM file associated to the VM
        """
        self._vm_spec=vm_spec
        self._image=QEMUImageFile(image_file)
        self._vars_file=vars_file
        self._boot_iso=boot_iso
        self._extra_isos=extra_isos

        self._qemu_pid:int|None=None
        self._viewer_pid:int|None=None
        self._spice_sock="/tmp/spice.sock"
        self._monitor_sock="/tmp/monitor.sock"

        # VirtioFSD instances
        self._vfs_dirs:list[VirtioSharedDirectory]|None=vfs_dirs

        # bind files and directories for the VM
        image_dir=self._image.image_directory_name
        _mounts={
            "/dev/kvm": {
                "mount-point": "/dev/kvm",
                "read-only": False,
                "monitored": False
            }
        }

        # give access to backend images
        for path in self._image.get_backing_files_names():
            _mounts[path]={
                    "mount-point": path,
                    "read-only": True,
                    "monitored": False
                }

        _mounts[image_file]={
            "mount-point": image_file,
            "read-only": False,
            "monitored": False
        }
        _mounts[vars_file]={
            "mount-point": vars_file,
            "read-only": False,
            "monitored": False
        }

        # bind ISO files
        if self._boot_iso is not None:
            _mounts[self._boot_iso]={
                    "mount-point": self._boot_iso,
                    "read-only": True,
                    "monitored": False
                }

        if self._extra_isos is not None:
            for isofile in self._extra_isos:
                _mounts[isofile]={
                    "mount-point": isofile,
                    "read-only": True,
                    "monitored": False
                }

        # add last to give a higher priority
        if features.mounts is not None:
            _mounts.update(features.mounts)

        caps=features.capabilities.copy() if features.capabilities else None
        if vm_spec.tap_iface is not None:
            if caps is None:
                caps=[]
            caps+=["net_admin", "net_bind_service", "net_raw"]

        features.bind_dev=True
        features.mounts=_mounts
        features.bind_wayland=True
        features.capabilities=caps

        super().__init__(features=features, run_dir=run_dir)
        self._api:nsbubble.BubbleAPI|None=None

    @property
    def api(self):
        if self._api is None:
            self._api=nsbubble.BubbleAPI(self.run_dir)
        return self._api

    @property
    def image_file(self) -> str:
        return self._image.image_file_name

    @property
    def vars_file(self) -> str:
        return self._vars_file

    def start_qemu(self) -> int:
        """Start the VM if not yet running, and return the QEMU's PID"""
        if self._qemu_pid is not None:
            return self._qemu_pid

        memavail=int(psutil.virtual_memory().available/1024)
        if memavail<self._vm_spec.mem_mb:
            raise NotEnoughMemory(f"Not enough memory (required {self._vm_spec.mem_mb} but {memavail} is available)")

        # start QEMU, using SPICE listening in the self._spice_sock socket
        args=["qemu-system-x86_64", "-enable-kvm",
            "-m", f"{self._vm_spec.mem_mb}M",
            "-cpu", "host,migratable=on,hv-time=on,hv-relaxed=on,hv-vapic=on",
            "-device", "virtio-rng-pci,max-bytes=1024,period=1000",
            "-monitor", f"unix:{self._monitor_sock}-m,server,nowait",
            "-overcommit", "mem-lock=off",
            "-smp", f"{self._vm_spec.nb_cpu}",
            "-device", "intel-hda",
            "-device", "hda-duplex",
            "-device", "qemu-xhci,id=xhci",
            "-device", "usb-tablet,bus=xhci.0",
            "-global", "ICH9-LPC.disable_s3=1", # otherwise the system simetimes does not boot, refer to https://wiki.archlinux.org/title/QEMU/Troubleshooting

            # USB devices sharing
            "-device", "qemu-xhci,id=xhci1",
	        "-device", "qemu-xhci,id=xhci2",
            "-device", "qemu-xhci,id=xhci3",

            "-chardev", "spicevmc,name=usbredir,id=usbredirchardev1",
            "-device", "usb-redir,chardev=usbredirchardev1,id=usbredirdev1",
            "-chardev", "spicevmc,name=usbredir,id=usbredirchardev2",
            "-device", "usb-redir,chardev=usbredirchardev2,id=usbredirdev2",
            "-chardev", "spicevmc,name=usbredir,id=usbredirchardev3",
            "-device", "usb-redir,chardev=usbredirchardev3,id=usbredirdev3"
        ]

        # handle command line args depending on QEMU's version
        # refer to https://www.qemu.org/docs/master/about/removed-features.html
        (qemu_major, qemu_minor)=_get_qemu_version()
        if qemu_major<9:
            args+=[
                "-machine", "type=q35,accel=kvm",
                "-no-hpet"
            ]
        else:
            args+=[
                "-machine", "type=q35,accel=kvm,hpet=off"
            ]

        # boot
        if self._boot_iso is not None:
            args+=[
                "-boot", "once=d,menu=on,splash-time=0",
                "-drive", f"file={self._boot_iso},media=cdrom"
            ]
        else:
            args+=[
                "-boot", "order=c,menu=on,splash-time=0"
            ]

        (ovmf_code, _)=BubbleVM.get_ovmf_files(self._vm_spec.secure_boot)
        args+=[
            "-drive", f"file={self._image.image_file_name},if=virtio",
            "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={ovmf_code}",
            "-drive", f"if=pflash,format=raw,unit=1,file={self._vars_file}"
        ]

        # extra ISO if necessary
        if self._extra_isos is not None:
            for isofile in self._extra_isos:
                if not os.path.exists(isofile):
                    raise Exception(f"ISO file '{isofile}' not found")
                args+=[
                    "-drive", f"file={isofile},media=cdrom"
                ]

        try:
            # virtiofs shared directories, if any
            if self._vfs_dirs is not None:
                index=0
                for vobj in self._vfs_dirs:
                    args+=[
                        "-chardev", f"socket,id=virtiochar{index},path={vobj.socket_path}",
                        "-device", f"vhost-user-fs-pci,queue-size=1024,chardev=virtiochar{index},tag={vobj.fsname}",
                    ]
                    index+=1
                if index>0:
                    args+=["-object", f"memory-backend-file,id=mem,size={self._vm_spec.mem_mb}M,mem-path=/dev/shm,share=on", "-numa", "node,memdev=mem"]

            # network settings
            if self._vm_spec.net_type is not None and self._vm_spec.tap_iface is not None:
                if self._vm_spec.net_type=="user":
                    args+=["-netdev", "user,id=lx1"]
                elif self._vm_spec.net_type.startswith("tap:"):
                    iface=self._vm_spec.tap_iface
                    if re.match("^[a-z0-9]{1,15}$", iface) is not None:
                        args+=["-netdev", f"tap,id=lx1,ifname={iface},script=no,downscript=no"]
                    else:
                        raise Exception(f"Invalid network interface name '{iface}'")
                else:
                    raise Exception(f"Invalid network type specification '{self._vm_spec.net_type}'")
                args+=["-device", "virtio-net-pci,netdev=lx1"]
            else:
                args+=["-net", "none"]

            # UI settings
            if self._vm_spec.graphical_device:
                args+=[
                    # Spice VDAgent
                    "-spice", f"unix=on,addr={self._spice_sock},disable-ticketing=on,image-compression=off,seamless-migration=on",
                    "-device", "virtio-serial",
                    "-chardev", "spicevmc,id=vdagent,debug=0,name=vdagent",
                    "-device", "virtserialport,chardev=vdagent,name=com.redhat.spice.0",
                    "-vga", "qxl",
                    "-global", "qxl-vga.vram_size=262144",
                    "-global", "qxl-vga.ram_size=262144",
                ]
            else:
                args+=["-nographic"]

            # start QEMU
            self.api.wait_for_bubble_ready()
            qemulogdir=f"{self.run_dir}/qemu"
            os.makedirs(qemulogdir, exist_ok=True)
            caps=None
            if self._vm_spec.tap_iface is not None:
                caps="net_admin" # required to create the tapvm interface
            self._qemu_pid=self.api.start_process(args, ignore_status=False, child_stderr_file="/bubble/run/qemu/qemu.stderr",
                                                  child_stdout_file="/bubble/run/qemu/qemu.stdout", capabilities=caps)
            time.sleep(1)
            st=self.api.get_process_exit_status(self._qemu_pid)
            if st is not None:
                errfile=f"{qemulogdir}/qemu.stderr"
                if os.path.exists(errfile):
                    with open(errfile, "r") as fd:
                        raise Exception(f"Could not start the VM: {fd.read()}")
                raise Exception("Could not start the VM")
            return self._qemu_pid

        except Exception as e:
            # nothing for now
            raise e

    def get_vm_state(self) -> tuple[bool, bool]:
        """Tell if the VM and the viewer are running
        """
        if self._qemu_pid is None:
            qemu_st=False
        else:
            qemu_st=self.api.is_process_running(self._qemu_pid)
            if not qemu_st:
                self._qemu_pid=None
        if self._viewer_pid is None:
            viewer_st=False
        else:
            viewer_st=self.api.is_process_running(self._viewer_pid)
            if not viewer_st:
                self._viewer_pid=None
        return (qemu_st, viewer_st)

    def vm_stop(self):
        """Stop the VM if it is running"""
        if self._qemu_pid is None:
            return
        if True:
            # dont't bother to kill the VM
            self.api.stop_process(self._qemu_pid)
        else:
            # powerdown the system first
            syslog.syslog(syslog.LOG_ERR, "TODO: powerdown the system before killing QEmu")

    def vm_display(self, viewer:Viewer|None=None) -> int:
        """Display the VM's UI, and return the viewer's PID
        """
        if self._viewer_pid is not None:
            return self._viewer_pid

        if viewer is None:
            viewer=Viewer()

        self._viewer_pid=self.api.start_process(viewer.get_arguments(self._spice_sock), ignore_status=False, extra_env=viewer.env_variables) # pyright: ignore
        return self._viewer_pid

class Viewer:
    @property
    def needs_spice_socket(self) -> bool:
        # remote-viewer need the socket upfront
        return True

    @property
    def real_prog_name(self) -> str:
        return "/usr/bin/remote-viewer"

    @property
    def bubble_prog_name(self) -> str:
        return self.real_prog_name

    @property
    def env_variables(self) -> dict[str,str]|None:
        return None

    def get_arguments(self, spice_socket_file:str) -> list[str]:
        """Get the arguments to start a VM viewer, defaults to the remote-viewer program
        """
        return [self.bubble_prog_name, "--hotkeys=release-cursor=shift+f12", "-t", "Virtual Machine", f"spice+unix://{spice_socket_file}"]
