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

from __future__ import annotations

import filecmp
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import syslog
import tempfile

from PIL import Image, ImageDraw

import padsi.config
import padsi.misc

_debug=False

#
# misc. adaptations
#
logging.getLogger("PIL").setLevel(logging.WARNING)

# some applications need a specific WMClass
startup_WM_class={
    "org.gnome.Terminal": "gnome-terminal-server"
}

# some applications don't set the AppIP correctly
app_id_corrections={
    "ca.desrt.dconf-editor": "dconf-editor",
    "org.gnome.dfeet": "d-feet",
    "org.gnome.eog": "eog",
    "org.gnome.Evince": "evince",
    "org.gnome.FileRoller": "file-roller",
    "org.gnome.Rhythmbox3": "rhythmbox",
    "org.gnome.seahorse.Application": "seahorse",
    "org.gnome.Totem": "totem"
}

#
# Desktop entry files manipulations
#

def _is_system_path(path) -> bool:
    return path.startswith("/usr/") or path.startswith("/var/")

class XDGResources:
    def __init__(self, padsi_root_path:str, xdg_data_dirs:list[str]|None=None):
        self._padsi_xdg_data_dir=padsi_root_path
        self._xdg_data_dirs=xdg_data_dirs
        self._entries_dirs:list[str]|None=None
        self._icons_dirs:list[str]|None=None
        self._cache:dict[str,dict]={}

    @property
    def entries_paths(self) -> list[str]:
        """List of all the Desktop entry directories (which may contain some desktop entries) found in the system paths, as full paths
        """
        if self._entries_dirs is None:
            self._entries_dirs=[]
            if self._xdg_data_dirs is None:
                xdg_data_dirs=os.environ.get("XDG_DATA_DIRS")
                dirs=xdg_data_dirs.split(":") if xdg_data_dirs is not None else []
            else:
                dirs=self._xdg_data_dirs

            for path in dirs:
                if _is_system_path(path) and os.path.isdir(path+"/applications"):
                    self._entries_dirs.append(os.path.join(path, "applications"))
        return self._entries_dirs

    @property
    def padsi_xdg_data_dir(self) -> str:
        """Directory under which all PADSI generated XDG resources are stored
        """
        return self._padsi_xdg_data_dir

    @property
    def icons_dirs(self) -> list[str]:
        if self._icons_dirs is None:
            # build the list of paths where the icon can be, refer to the https://specifications.freedesktop.org/icon-theme-spec/latest
            self._icons_dirs=[]
            if self._xdg_data_dirs is None:
                xdg_data_dirs=os.environ.get("XDG_DATA_DIRS")
                dirs=xdg_data_dirs.split(":") if xdg_data_dirs is not None else []
            else:
                dirs=self._xdg_data_dirs

            for path in dirs:
                if _is_system_path(path) and os.path.isdir(path+"/icons"):
                    self._icons_dirs.append(os.path.join(path, "icons"))

            path="/usr/share/pixmaps"
            if os.path.isdir(path):
                self._icons_dirs.append(path)
        return self._icons_dirs

    def find_icons_in_path(self, path:str, theme:str|None, icon_name:str) -> list[str]:
        if path not in self._cache:
            icons:dict[str,list[str]]={} # key=icon name, value=list of paths with that icon name
            for (dirpath, _dirnames, filenames) in os.walk(path):
                for fname in filenames:
                    if fname.endswith(".png") or fname.endswith(".svg"):
                        icname=fname[:-4]
                        if icname not in icons:
                            icons[icname]=[os.path.join(dirpath, fname)]
                        else:
                            icons[icname].append(os.path.join(dirpath, fname))
            self._cache[path]=icons

        res_theme=[]
        res_default=[]
        theme_s=f"/{theme}/" if theme is not None else None

        # try to get the icon corresponing to the theme
        files=self._cache[path].get(icon_name)
        if files is not None:
            for ipath in files:
                if theme_s is not None and theme_s in ipath:
                    res_theme.append(ipath)
                else:
                    res_default.append(ipath)
        return res_theme if len(res_theme)>0 else res_default

def clean_zone_name(zone_name:str) -> str:
    """Remove any non allowed character in the zone name"""
    zn=''.join(char for char in zone_name if char.isalnum())
    if not zn:
        raise Exception(f"Invalid zone name {zone_name}")
    return zn

def _add_round_border(image:Image.Image, border_color=(232, 232, 232)) -> Image.Image:
    # compute border's params
    s=min(image.width, image.height)
    border_radius=10
    border_width=5
    if s is not None and s>0:
        border_radius=int(10*s/128)
        border_width=int(border_radius/2)
    border_radius=max(border_radius, 10)
    border_width=max(border_width, 5)

    image=image.convert("RGBA")
    # Create an out mask and an in mask
    mask=Image.new("L", image.size, 0)
    draw=ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, image.size[0], image.size[1]], radius=border_radius, fill=255)
    mask_in=Image.new("L", image.size, 0)
    draw=ImageDraw.Draw(mask_in)
    draw.rounded_rectangle(
        [
            border_width,
            border_width,
            image.size[0] - border_width,
            image.size[1] - border_width,
        ],
        radius=border_radius - border_width,
        fill=255,
    )

    border_image=Image.new("RGBA", image.size, color=border_color)
    new_image=Image.new("RGBA", image.size, color=0)

    new_image.paste(border_image, mask=mask)
    new_image.paste(image, mask=mask_in)
    return new_image

class DesktopEntry:
    def __init__(self, desktop_entry_file:str, xdg_res:XDGResources):
        if not desktop_entry_file.endswith(".desktop"):
            raise Exception(f"Desktop entry file name '{desktop_entry_file}' does not end with '.desktop'")
        if not isinstance(xdg_res, XDGResources):
            raise Exception(f"Invalid xdg_res argument '{xdg_res}'")
        self._filename=desktop_entry_file
        self._dir=os.path.dirname(self._filename)
        self._app_id=os.path.basename(desktop_entry_file[:-8])
        self._exec=None
        self._exec_path=None
        self._icon_name=None # value of the Icon property
        self._icon_file:str|None=None # full icon path
        self._icon_image:Image.Image|None=None
        self._contents:list[str]|None=None # contains the whole file, line by line
        self._main_contents_start_line:int|None=None # line number of the start of the [Desktop Entry] section
        self._main_contents_end_line:int|None=None # line number of the end of the [Desktop Entry] section
        self._nodisplay=None
        self._mimetype=None
        self._xdg_res=xdg_res

    @property
    def filename(self):
        """Full path to the desktop entry"""
        return self._filename

    @property
    def contents(self) -> list[str]:
        """Get the contents, line by line of the desktop entry"""
        if self._contents is None:
            index=0
            self._contents=[]
            with open(self._filename, "r") as fd:
                for line in fd.readlines():
                    line=line[:-1] # remove the final \n
                    sline=line.strip()
                    self._contents.append(line)
                    if sline=="[Desktop Entry]":
                        self._main_contents_start_line=index
                    elif sline and self._main_contents_start_line is not None and self._main_contents_end_line is None and \
                        sline[0]=="[" and sline[-1]=="]":
                        self._main_contents_end_line=index-1
                    index+=1
            if self._main_contents_start_line is None:
                raise Exception("Section [Desktop Entry] not found")
            if self._main_contents_end_line is None:
                self._main_contents_end_line=index
        return self._contents

    def _main_contents(self):
        """Contents in the [Desktop Entry] section as a generator
        """
        for line in self.contents[self._main_contents_start_line:self._main_contents_end_line+1]: # pyright: ignore
            yield line

    @property
    def app_id(self):
        """Application ID associated to the desktop entry"""
        return self._app_id

    @property
    def icon_name(self) -> str|None:
        """Name of the icon in the desktop entry, or None
        like "org.gnome.TextEditor"
        """
        if self._icon_name is None:
            for line in self._main_contents():
                if line.startswith("Icon="):
                    self._icon_name=line[5:].strip()
                    if "/" in self._icon_name: # we have a path, not an "icon ID"
                        self._icon_file=self._icon_name
                        self._icon_name=None
                    break
        return self._icon_name

    @property
    def icon_file(self) -> str|None:
        """Actual path to the icon file"""
        if self._icon_file is None:
            # get the current icon theme, like "Adwaita"
            cproc=subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "icon-theme"], capture_output=True)
            if cproc.returncode==0:
                icon_theme=cproc.stdout.decode().replace("'", "").strip()
            else:
                icon_theme="highcolor"

            # get the 'best' icon
            if self.icon_name is not None:
                for path in self._xdg_res.icons_dirs:
                    if self._icon_file is not None:
                        break
                    logging.debug(f"{self.icon_name} in {path} (icon theme {icon_theme})???")
                    icon_files=self._xdg_res.find_icons_in_path(path, icon_theme, self.icon_name)
                    if len(icon_files)>0:
                        for icon in icon_files:
                            if icon[-4:]==".svg":
                                return icon

                        for px in (512, 256, 192, 128, 96, 72, 64, 48, 42, 36, 32, 24, 22, 16, 8):
                            if self._icon_file is not None:
                                break
                            resol=f"/{px}x{px}/"
                            for icon in icon_files:
                                if resol in icon:
                                    self._icon_file=icon
                                    break

                        # single size icon
                        if self._icon_file is None:
                            self._icon_file=icon_files[0]

        return self._icon_file

    @property
    def no_display(self) -> bool|None:
        """The NoDisplay property of the desktop entry
        """
        if self._nodisplay is None:
            for line in self._main_contents():
                if line.startswith("NoDisplay="):
                    self._nodisplay=line[10:].strip().lower()=="true"
                    break
        return self._nodisplay

    @property
    def mimetype(self) -> str|None:
        """The MimeType property of the desktop entry
        """
        if self._mimetype is None:
            for line in self._main_contents():
                if line.startswith("MimeType="):
                    self._mimetype=line[9:]
                    break
        return self._mimetype

    @property
    def exec(self) -> str|None:
        """The program being executed AS IS from the Exec property
        (can be a full path or not)
        """
        if self._exec is None:
            for line in self._main_contents():
                if line.startswith("Exec="):
                    parts=line[5:].strip().split()
                    self._exec=parts[0]
                    if self._exec=="env" or self._exec.endswith("/env"):
                        print(f"TODO!: DE '{self.filename}' Exec={self._exec}", file=sys.stderr)
                        self._exec=None
        return self._exec

    @property
    def exec_path(self) -> str|None:
        """The program being executed as a full path (from the Exec property)
        Returns None if the full path can't be found
        """
        if self._exec_path is None:
            self._exec_path=self.exec
            if self._exec_path is not None and not os.path.isabs(self._exec_path):
                self._exec_path=shutil.which(self._exec_path)
        return self._exec_path

    def _create_outlined_icon(self, color:str) -> object|None:
        """Add an outline to the desktop entry's icon and returns a new NamedTemporaryFile, or None
        if the icon was not found
        """
        icon_file=self.icon_file
        if icon_file is None:
            logging.error(f"Could not create outlined icon for '{self.icon_name}' and color {color}: self.icon_file is None")
            return None

        if icon_file[-4:]==".svg":
            # convert to PNG first
            tmp=tempfile.NamedTemporaryFile("w", suffix=".png")
            import cairosvg
            cairosvg.svg2png(url=icon_file, write_to=tmp.name)
            icon_file=tmp.name

        if self._icon_image is None:
            self._icon_image=Image.open(icon_file)
        image_with_border=_add_round_border(self._icon_image, border_color=color)
        tmp=tempfile.NamedTemporaryFile("w", suffix=".png")
        image_with_border.save(tmp.name)
        return tmp

    def customize_for_zone(self, zone:padsi.config.Zone, de_install_dir:str, icons_install_dir:str, icon_color:str|None,
                           nodisplay:bool, user_de:bool) -> tuple[set[str],set[str]]:
        """Customize the desktop entry for the specified zone name and color

        If icon_color is not None, this function creates a new icon customized according to the specified color and
        alters the contents of the desktop entry to point to that new icon.

        If nodisplay is True, then the NoDisplay property is set to True

        Returns a tuple containing the sets of created/updated desktop entry files and icon files
        """
        touched_de_files=set()
        touched_icon_files=set()
        zone_name=clean_zone_name(zone.name)

        # icon preparations (don't re-create an icon which exists with the same name)
        new_icon=None
        icon_path=None
        if icon_color is not None:
            if self.icon_name is not None:
                # icon is from a name
                icon_name=f"padsi.{zone_name}.{self.icon_name}.png"
                icon_path=os.path.join(icons_install_dir, icon_name)
                if not os.path.exists(icon_path):
                    new_icon=self._create_outlined_icon(icon_color)
            elif self.icon_file is not None:
                # icon is from a path
                icon_name=f"padsi.{zone_name}.{os.path.basename(self.icon_file)}"
                icon_path=os.path.join(icons_install_dir, icon_name)
                if not os.path.exists(icon_path):
                    new_icon=self._create_outlined_icon(icon_color)

            if icon_path is not None:
                touched_icon_files.add(icon_path)

        if _debug:
            logging.debug(f"Icon: {self.icon_name}, icon_path:{icon_path}, new_icon:{new_icon}")

        # misc. preparations
        new_app_id=None
        if not nodisplay:
            repl=app_id_corrections.get(self._app_id)
            if repl:
                new_app_id=f"padsi.{zone_name}.{repl}"
        if new_app_id is None:
            new_app_id=f"padsi.{zone_name}.{self._app_id}"

        hc_wmclass=startup_WM_class.get(self._app_id)
        if hc_wmclass is not None:
            hc_wmclass=f"padsi.{zone_name}.{hc_wmclass}"

        # alter the contents
        new_contents:list[str]=[]
        nodisplay_set=False
        wmclass_set=False
        index=0
        for line in self.contents:
            if index>=self._main_contents_start_line and index<self._main_contents_end_line: # pyright: ignore
                # we are in the [Desktop Entry] section
                if line.startswith("Icon="):
                    if icon_color is not None:
                        if icon_path is not None:
                            new_contents.append(f"Icon={icon_path}")
                    else:
                        new_contents.append(line)
                    # otherwise, don't specify any icon in the desktop entry
                elif line.startswith("DBusActivatable="):
                    # we don't want to start the program using DBus in the zone, it does not work (yet?)
                    new_contents.append("DBusActivatable=false")
                elif line.startswith("Name="):
                    if icon_color is not None:
                        new_contents.append(f"Name={line[5:]} ({zone_name})")
                    else:
                        new_contents.append(line)
                elif (m:=re.match(r"Name\[[a-zA-Z0-9@_-]*]=", line)):
                    if icon_color is not None:
                        new_contents.append(f"{m.group(0)}{line[len(m.group(0)):]} ({zone_name})")
                    else:
                        new_contents.append(line)
                elif line.startswith("Exec="):
                    cmde=line[5:]
                    (path, *args)=shlex.split(cmde)
                    if path.endswith("padsi-cli"):
                        new_contents.append(line)
                    else:
                        # quick and dirty patch for chromium to force it to run using Wayland
                        # maybe use programs' policies to make this generic?
                        if "chromium" in path:
                            cmde=shlex.join([path, "--enable-features=UseOzonePlatform", "--ozone-platform=wayland"]+args)
                        new_contents.append(f"Exec=padsi-cli run {zone.name} {cmde}")
                elif line.startswith("NoDisplay="):
                    nodisplay_set=True
                    if nodisplay or (not user_de and icon_color is not None and self._app_id not in zone.apps):
                        new_contents.append("NoDisplay=true")
                    else:
                        new_contents.append(line)
                elif line.startswith("StartupWMClass="):
                    wmclass_set=True
                    if hc_wmclass is not None:
                        new_contents.append(f"StartupWMClass={hc_wmclass}")
                    else:
                        new_contents.append(f"StartupWMClass=padsi.{zone_name}.{line[15:]}")
                elif line.startswith("MimeType="):
                    pass
                elif line.startswith("Version="):
                    version=line[8:]
                    if version in ("1.0", "1.5"):
                        # ignore invalid Version attribute
                        new_contents.append(line)
                else:
                    new_contents.append(line)

                if index==self._main_contents_end_line-1: # pyright: ignore
                    # finished the main section of the file
                    if not nodisplay_set and (nodisplay or (not user_de and icon_color is not None and self._app_id not in zone.apps)):
                        new_contents.append("NoDisplay=true")
                    if not wmclass_set and hc_wmclass is not None:
                        new_contents.append(f"StartupWMClass={hc_wmclass}")
            else:
                # we are in another section
                if line.startswith("Exec="):
                    new_contents.append(f"Exec=padsi-cli run {zone.name} {line[5:]}")
                elif line.startswith("MimeType="):
                    pass
                else:
                    new_contents.append(line)
            index+=1

        # write resources
        if new_icon is not None:
            shutil.copyfile(new_icon.name, icon_path) # pyright: ignore

        with tempfile.TemporaryDirectory() as tmpdedir:
            tmpde=os.path.join(tmpdedir, f"{new_app_id}.desktop")
            with open(tmpde, "w") as fd:
                fd.write("\n".join(new_contents)+"\n")

            # generate new file
            res=subprocess.run(["desktop-file-install", "--dir", "/tmp", tmpde], capture_output=True, text=True)
            if res.returncode!=0:
                msg=f"Invalid desktop entry for {self._app_id} (zone {zone.name}): {res.stderr}"
                syslog.syslog(syslog.LOG_ERR, msg)
                raise Exception(msg)

            # reuse existing file if possible (needs to be made _after_ generation because some tags like "X-Desktop-File-Install-Version" may be added),
            # better to avoid giving too much work to the DE
            final_file=os.path.join(de_install_dir, f"{new_app_id}.desktop")
            tmp_file=os.path.join("/tmp", f"{new_app_id}.desktop")
            exists=False
            if os.path.exists(final_file):
                if filecmp.cmp(tmp_file, final_file):
                    exists=True

            if exists:
                os.remove(tmp_file)
            else:
                shutil.move(tmp_file, final_file)
            touched_de_files.add(final_file)

        return (touched_de_files, touched_icon_files)

class AppFolter:
    def __init__(self, zone_name:str, zone_friendly_name:str):
        self._name=f"padsi-{zone_name}"
        self._fname=zone_friendly_name

    def _parse_list(self, data:str) -> list[str]:
        """Parse something like "['Utilities', 'YaST']" to ["Utilities", "YaST"]
        """
        json_data=data.replace("'", '"')
        return json.loads(json_data)

    def _unparse_list(self, data:list[str]) -> str:
        return str(data)

    @property
    def name(self):
        return self._name

    def _get_current_appfolders(self) -> list[str]:
        cproc=subprocess.run(["gsettings", "get", "org.gnome.desktop.app-folders", "folder-children"], capture_output=True)
        if cproc.returncode!=0:
            raise Exception(f"Could not get list of AppFolders: {cproc.stderr.decode()}")
        return self._parse_list(cproc.stdout.decode())

    def remove(self):
        """Remove the AppFolder and everything in it"""
        appfolders=self._get_current_appfolders()
        if self.name in appfolders:
            appfolders.remove(self.name)
            cproc=subprocess.run(["gsettings", "set", "org.gnome.desktop.app-folders", "folder-children", self._unparse_list(appfolders)], capture_output=True)
            if cproc.returncode!=0:
                raise Exception(f"Could change the list of AppFolders to {appfolders}: {cproc.stderr.decode()}")

    def define(self, apps:list[DesktopEntry]):
        """Create if necessary the AppFolder, and set its contents
        """
        self.remove()

        # create AppFolder
        appfolders=self._get_current_appfolders()
        appfolders.append(self.name)
        cproc=subprocess.run(["gsettings", "set", "org.gnome.desktop.app-folders", "folder-children", self._unparse_list(appfolders)], capture_output=True)
        if cproc.returncode!=0:
            raise Exception(f"Could change the list of AppFolders to {appfolders}: {cproc.stderr.decode()}")

        # set AppFolder's name
        cproc=subprocess.run(["gsettings", "set", f"org.gnome.desktop.app-folders.folder:/org/gnome/desktop/app-folders/folders/{self.name}/",
                              "name", self._fname], capture_output=True)
        if cproc.returncode!=0:
            raise Exception(f"Could define AppFolder '{self.name}' name to '{self._fname}': {cproc.stderr.decode()}")

        # set AppFolder's contents
        appslist=[f"{de.app_id}.desktop" for de in apps]
        cproc=subprocess.run(["gsettings", "set", f"org.gnome.desktop.app-folders.folder:/org/gnome/desktop/app-folders/folders/{self.name}/",
                              "apps", self._unparse_list(appslist)], capture_output=True)
        if cproc.returncode!=0:
            raise Exception(f"Could define AppFolder '{self.name}' apps to '{appslist}': {cproc.stderr.decode()}")
