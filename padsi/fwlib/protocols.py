#!/usr/bin/python3

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


#
# This file contains dictionary variables which are extracted from the /etc/protocols file
#

def generate_protocols():
    """Update the protocols variables below from the /etc/protocols file
    """
    import os
    idtop={}
    ptoid={}
    with open("/etc/protocols", "r") as fd:
        for line in fd.readlines():
            if line[0]!="#":
                try:
                    (name, id, *_)=line.split()
                    id=int(id)
                    idtop[id]=name
                    ptoid[name]=id
                except Exception:
                    pass

    with open(__file__, "r") as fd:
        data=[]
        for line in fd.readlines():
            if line.startswith("# PROTOCOLS BELOW"):
                break
            data.append(line)

        data.append("# PROTOCOLS BELOW\n")
        data.append("protocol_ids={\n")
        for (key, value) in idtop.items():
            data.append(f'\t{key}: "{value}",\n')
        data.append("}\n\n")

        data.append("protocol_names={\n")
        for (key, value) in ptoid.items():
            data.append(f'\t"{key}": {value},\n')
        data.append("}\n")

        with open(__file__, "w") as fd:
            fd.write("".join(data))
        print(f"File '{os.path.basename(__file__)}' has been updated (though there may not actually be any change)")

if __name__=="__main__":
    generate_protocols()

# PROTOCOLS BELOW
protocol_ids={
	0: "hopopt",
	1: "icmp",
	2: "igmp",
	3: "ggp",
	4: "ipencap",
	5: "st",
	6: "tcp",
	8: "egp",
	9: "igp",
	12: "pup",
	17: "udp",
	20: "hmp",
	22: "xns-idp",
	27: "rdp",
	29: "iso-tp4",
	33: "dccp",
	36: "xtp",
	37: "ddp",
	38: "idpr-cmtp",
	41: "ipv6",
	43: "ipv6-route",
	44: "ipv6-frag",
	45: "idrp",
	46: "rsvp",
	47: "gre",
	50: "esp",
	51: "ah",
	57: "skip",
	58: "ipv6-icmp",
	59: "ipv6-nonxt",
	60: "ipv6-opts",
	73: "rspf",
	81: "vmtp",
	88: "eigrp",
	89: "ospf",
	93: "ax.25",
	94: "ipip",
	97: "etherip",
	98: "encap",
	103: "pim",
	108: "ipcomp",
	112: "vrrp",
	115: "l2tp",
	124: "isis",
	132: "sctp",
	133: "fc",
	135: "mobility-header",
	136: "udplite",
	137: "mpls-in-ip",
	138: "manet",
	139: "hip",
	140: "shim6",
	141: "wesp",
	142: "rohc",
	143: "ethernet",
	262: "mptcp",
}

protocol_names={
	"ip": 0,
	"hopopt": 0,
	"icmp": 1,
	"igmp": 2,
	"ggp": 3,
	"ipencap": 4,
	"st": 5,
	"tcp": 6,
	"egp": 8,
	"igp": 9,
	"pup": 12,
	"udp": 17,
	"hmp": 20,
	"xns-idp": 22,
	"rdp": 27,
	"iso-tp4": 29,
	"dccp": 33,
	"xtp": 36,
	"ddp": 37,
	"idpr-cmtp": 38,
	"ipv6": 41,
	"ipv6-route": 43,
	"ipv6-frag": 44,
	"idrp": 45,
	"rsvp": 46,
	"gre": 47,
	"esp": 50,
	"ah": 51,
	"skip": 57,
	"ipv6-icmp": 58,
	"ipv6-nonxt": 59,
	"ipv6-opts": 60,
	"rspf": 73,
	"vmtp": 81,
	"eigrp": 88,
	"ospf": 89,
	"ax.25": 93,
	"ipip": 94,
	"etherip": 97,
	"encap": 98,
	"pim": 103,
	"ipcomp": 108,
	"vrrp": 112,
	"l2tp": 115,
	"isis": 124,
	"sctp": 132,
	"fc": 133,
	"mobility-header": 135,
	"udplite": 136,
	"mpls-in-ip": 137,
	"manet": 138,
	"hip": 139,
	"shim6": 140,
	"wesp": 141,
	"rohc": 142,
	"ethernet": 143,
	"mptcp": 262,
}
