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

import base64
import enum
import socket
import struct
import syslog
from typing import Any

_debug=False

class MessageType(int, enum.Enum):
    METHOD_CALL = 1
    METHOD_RETURN = 2
    ERROR = 3
    SIGNAL = 4

class MessageFlag(str, enum.Enum):
    NO_REPLY_EXPECTED = "NO_REPLY_EXPECTED"
    NO_AUTO_START = "NO_AUTO_START"
    ALLOW_INTERACTIVE_AUTHORIZATION = "ALLOW_INTERACTIVE_AUTHORIZATION"

class MessageHeaderType(str, enum.Enum):
    PATH = "PATH"
    INTERFACE = "INTERFACE"
    MEMBER = "MEMBER"
    ERROR_NAME = "ERROR_NAME"
    REPLY_SERIAL = "REPLY_SERIAL"
    DESTINATION = "DESTINATION"
    SENDER = "SENDER"
    SIGNATURE = "SIGNATURE"
    UNIX_FDS = "UNIX_FDS"

class MessageIncomplete(Exception):
    pass

def _unmarshall_string(data:bytes, endian_indicator:str) -> tuple[str,int]: # string and the total number of bytes "consummed"
    (msg_len,)=struct.unpack(endian_indicator+"I", data[0:4]) # get the string's length
    return(data[4:4+msg_len].decode(), msg_len+5) # 4 for the string's length + msg_len + 1 for the final "\0"

def _marshall_string(str, endian_indicator:str) -> tuple[bytes,int]:
    ls=struct.pack(endian_indicator+"I", len(str))
    data=ls+str.encode()+b"\x00"
    return (data, len(data))

def _unmarshall_signature(data:bytes, endian_indicator:str) -> tuple[str,int]: # signature and the total number of bytes "consummed"
    (msg_len,)=struct.unpack(endian_indicator+"B", data[0:1]) # get the string's length
    return(data[1:msg_len+1].decode(), msg_len+2) # 1 for the string's length + msg_len + 1 for the final "\0"

def _marshall_signature(str, endian_indicator:str) -> tuple[bytes,int]: # data and data's length
    ls=struct.pack(endian_indicator+"B", len(str))
    data=ls+str.encode()+b"\x00"
    return (data, len(data))

class Message:
    """Represent a D-Bus message, and allow very minimal introspection
    """
    _header_type_mapping={
        1: MessageHeaderType.PATH,
        2: MessageHeaderType.INTERFACE,
        3: MessageHeaderType.MEMBER,
        4: MessageHeaderType.ERROR_NAME,
        5: MessageHeaderType.REPLY_SERIAL,
        6: MessageHeaderType.DESTINATION,
        7: MessageHeaderType.SENDER,
        8: MessageHeaderType.SIGNATURE,
        9: MessageHeaderType.UNIX_FDS,

        MessageHeaderType.PATH: 1,
        MessageHeaderType.INTERFACE: 2,
        MessageHeaderType.MEMBER: 3,
        MessageHeaderType.ERROR_NAME: 4,
        MessageHeaderType.REPLY_SERIAL: 5,
        MessageHeaderType.DESTINATION: 6,
        MessageHeaderType.SENDER: 7,
        MessageHeaderType.SIGNATURE: 8,
        MessageHeaderType.UNIX_FDS: 9
    }
    _field_signatures={
        1: "o",
        2: "s",
        3: "s",
        4: "s",
        5: "u",
        6: "s",
        7: "s",
        8: "g",
        9: "u"
    }
    def __init__(self, blob:bytes):
        self._header_fields:dict[MessageHeaderType,Any]={}
        self._blob:bytes
        self._parse_header(blob)
        self._body:Any=None
        self._passed_fd:list[int]=[]
        self._from_descr:str|None=None

    def __str__(self) -> str:
        descr=f"from {self._from_descr}" if self._from_descr is not None else ""
        if self.message_type==MessageType.METHOD_CALL:
            res=f"Message #{self.serial}[{descr} METHOD_CALL to {self.destination},{self.path},{self.interface},{self.member}()"
        elif self.message_type==MessageType.METHOD_RETURN or self.message_type==MessageType.ERROR:
            res=f"Message #{self.serial}[{descr} {'METHOD_RETURN' if self.message_type==MessageType.METHOD_RETURN else 'ERROR'} to {self.destination}, reply to #{self.reply_serial}"
        else:
            res=f"Message #{self.serial}[{descr} SIGNAL to {self.destination}"
        if self.passed_file_descriptors is not None:
            res+=f", FDs {self.passed_file_descriptors}]"
        else:
            res+="]"
        return res

    def serialize(self) -> dict:
        return {
            "type": self.message_type,
            "iface": self.interface,
            "member": self.member,
            "path": self.path,
            "dest": self.destination,
            "sender": self.sender,
            "serial": self.serial,
            "reply": self.reply_serial,
            "blob": base64.b64encode(self.blob) if self.blob is not None else None,
            "anc": base64.b64encode(self.ancillary_data) if self.ancillary_data is not None else None
        }

    @property
    def from_descr(self) -> str|None:
        return self._from_descr

    @from_descr.setter
    def from_descr(self, descr:str):
        self._from_descr=descr

    def _parse_header(self, blob:bytes):
        try:
            if len(blob)<12:
                raise MessageIncomplete()

            # handle endianness
            endian_byte=blob[0:1]
            if endian_byte==b'l':
                endian_indicator="<"
            elif endian_byte==b'B':
                endian_indicator=">"
            else:
                raise ValueError(f"Unknown endian specifier: {endian_byte}")
            self._endian_indicator=endian_indicator

            # fixed part of the header
            (msg_type, flags, version, body_length, serial)=struct.unpack(endian_indicator+"BBBII", blob[1:12])
            self._body_length=body_length
            try:
                self._msg_type=MessageType(msg_type)
            except Exception:
                raise Exception(f"Invalid message type {msg_type}")
            self._serial=serial
            self._flags:dict[MessageFlag,bool]={
                MessageFlag.NO_REPLY_EXPECTED: True if flags & 0x01 else False,
                MessageFlag.NO_AUTO_START: True if flags & 0x02 else False,
                MessageFlag.ALLOW_INTERACTIVE_AUTHORIZATION: True if flags & 0x03 else False
            }
            index=12

            # array of fields in the header
            (array_length,)=struct.unpack(endian_indicator+"I", blob[index:index+4])
            index+=4
            header_length=index+array_length
            header_length+=(8-header_length%8)%8 # 8 bytes alignment
            self._header_length=header_length

            msg_length=header_length+body_length
            if _debug:
                print(f"PARSING MESSAGE: msg_type:{msg_type}, serial:{serial}, header_length:{header_length}, body_length:{body_length}, array_length:{array_length}, msg_length:{msg_length}, blob length:{len(blob)}")
            if msg_length>len(blob):
                raise MessageIncomplete()

            # align index to 8 bytes
            index+=(8-index%8)%8

            # analyse header fields
            while index<header_length:
                (field_type,)=struct.unpack(endian_indicator+"B", blob[index:index+1])
                index+=1
                (field_signature, consummed)=_unmarshall_signature(blob[index:], endian_indicator)
                index+=consummed

                htype=Message._header_type_mapping[field_type]
                match field_signature:
                    case 'o' | 's':
                        (value, consummed)=_unmarshall_string(blob[index:], endian_indicator)
                        self._header_fields[htype]=value
                        index+=consummed
                        if _debug:
                            print(f"\tHEADER: {htype.value}: {value}")

                    case 'g':
                        (value, consummed)=_unmarshall_signature(blob[index:], endian_indicator)
                        self._header_fields[htype]=value
                        index+=consummed
                        if _debug:
                            print(f"\tHEADER: {htype.value}: {value}")

                    case 'u' | 'h':
                        (value,)=struct.unpack(endian_indicator+"I", blob[index:index+4])
                        self._header_fields[htype]=value
                        index+=4
                        if _debug:
                            print(f"\tHEADER: {htype.value}: {value}")

                    case _:
                        msg=f"Unhandled header field {htype} with signature {field_signature}"
                        syslog.syslog(syslog.LOG_ERR, msg)
                        raise Exception(msg)

                # align index to 8 bytes
                index+=(8-index%8)%8

            self._blob=blob[:msg_length]
            self._msg_length=msg_length
        except MessageIncomplete as e:
            raise e
        except struct.error:
            raise MessageIncomplete()
        except Exception as e:
            raise Exception(f"Could not parse D-Bus message [{blob}]: {str(e)}")

    def unmarshall_body(self, signature:str) -> Any:
        """VERY minimalist intrusion in the body of the message...
        """
        if self._body is None:
            match signature[0]:
                case "s" | "o":
                    (data, _)=_unmarshall_string(self._blob[self._header_length:], self._endian_indicator)
                case "g":
                    (data, _)=_unmarshall_signature(self._blob[self._header_length:], self._endian_indicator)
                case _:
                    raise Exception(f"Body unmarshalling for signature '{signature}' is not implemented")
            self._body=data
        return self._body

    @property
    def message_type(self) -> MessageType:
        """Get the type of D-Bus message"""
        return self._msg_type

    @property
    def reply_expected(self) -> bool:
        """Tell if a METHOD_CALL message expects a reply
        """
        return self._msg_type==MessageType.METHOD_CALL and not self._flags[MessageFlag.NO_REPLY_EXPECTED]

    @property
    def path(self) -> str:
        return self.get_header_field(MessageHeaderType.PATH)

    @property
    def destination(self) -> str|None:
        return self.get_header_field(MessageHeaderType.DESTINATION)

    @property
    def interface(self) -> str|None:
        return self.get_header_field(MessageHeaderType.INTERFACE)

    @property
    def member(self) -> str|None:
        return self.get_header_field(MessageHeaderType.MEMBER)

    @property
    def sender(self) -> str|None:
        return self.get_header_field(MessageHeaderType.SENDER)

    @property
    def signature(self) -> str|None:
        return self.get_header_field(MessageHeaderType.SIGNATURE)

    @property
    def serial(self) -> int:
        """Serial number of the message"""
        return self._serial

    @property
    def reply_serial(self) -> int|None:
        """For reply messages, get the serial number of the corresponding
        method call message
        """
        if self._msg_type==MessageType.METHOD_RETURN:
            return self.get_header_field(MessageHeaderType.REPLY_SERIAL)
        return None

    def get_header_field(self, field:MessageHeaderType) -> Any:
        """Get a specific header"""
        return self._header_fields.get(field)

    @property
    def length(self) -> int:
        """Total length of the message"""
        return self._msg_length

    @property
    def blob(self) -> bytes:
        """Actual wire representation of the message"""
        if self._blob is None:
            raise Exception("Message has not yet been parsed")
        return self._blob

    @property
    def passed_file_descriptors(self) -> list[int]|None:
        """List of file descriptors passed along with the message
        """
        if self.signature is None:
            return None

        count=self.signature.count("h")
        if len(self._passed_fd)!=count:
            raise Exception(f"Wrong number of file descriptor(s): expected {count} and actually have {len(self._passed_fd)}")
        return self._passed_fd if len(self._passed_fd)>0 else None

    @passed_file_descriptors.setter
    def passed_file_descriptors(self, fds:list[int]):
        if self.signature is None:
            raise Exception(f"Tried to specify {len(fds)} file descriptor(s) where message has no signature")
        count=self.signature.count("h")
        if len(fds)!=count:
            raise Exception(f"Tried to specify {len(fds)} file descriptor(s) where {count} are expected")
        if _debug:
            print(f"\tMSG #{self.serial} declared to pass FD {fds}")
        self._passed_fd=fds

    @property
    def ancillary_data(self) -> Any:
        """Get the ancillary data required to pass file descriptors if any via a Unix socket
        """
        if self.passed_file_descriptors is None:
            return None
        fds=struct.pack("i"*len(self.passed_file_descriptors), *self.passed_file_descriptors)
        return [(socket.SOL_SOCKET, socket.SCM_RIGHTS, fds)]

    @property
    def endian_indicator(self) -> str:
        return self._endian_indicator

    def change_header(self, changed_fields:dict[MessageHeaderType,Any]) -> Message:
        """Create a new message containing modified headers than the current one but the same body
        Note: no header field is added, only the present ones are altered
        """
        # determine how much more room the new blob will occupy
        extra_size=0
        for (htype, value) in self._header_fields.items():
            nvalue=changed_fields.get(htype)
            if nvalue is not None and isinstance(nvalue, str):
                extra_size+=max(0, len(nvalue)-len(value))

        # working buffer
        buffer=bytearray(b"\x00"*(self._header_length+extra_size+8)) # the +8 compensates for extra padding which may be necessary
        view=memoryview(buffer)
        view[0:12]=self._blob[0:12]
        array_len_index=12
        index=16

        # field's headers
        array_len_start=index
        array_len_end=index
        for (htype, value) in self._header_fields.items():
            value=changed_fields.get(htype, value)
            if value is None:
                raise Exception(f"Can't change header {htype} to None")
            if _debug:
                print(f"\tHEADER: {htype.value}: {value}")

            # field type as a byte
            field_type=Message._header_type_mapping[htype]
            view[index:index+1]=struct.pack(self.endian_indicator+"B", field_type)
            index+=1

            # variant's signature
            sig_str=Message._field_signatures[field_type]
            (data, length)=_marshall_signature(sig_str, self.endian_indicator)
            view[index:index+length]=data
            index+=length

            # field value
            match sig_str:
                case 'o' | 's':
                    (data, length)=_marshall_string(value, self.endian_indicator)
                case 'g':
                    (data, length)=_marshall_signature(value, self.endian_indicator)
                case 'u' | 'h':
                    data=struct.pack(self.endian_indicator+"I", value)
                    length=4
                case _:
                        msg=f"Unhandled header field {htype} with signature {sig_str}"
                        syslog.syslog(syslog.LOG_ERR, msg)
                        raise Exception(msg)

            view[index:index+length]=data
            index+=length
            array_len_end=index
            padlen=(8-index%8)%8
            if padlen>0:
                view[index:index+padlen]=b"\x00"*padlen
                index+=padlen

        # set header's fields array length
        view[array_len_index:array_len_index+4]=struct.pack(self.endian_indicator+"i", array_len_end-array_len_start)

        newmsg=Message(bytes(buffer[:index]+self._blob[self._header_length:self._header_length+self._body_length])) # TODO: pretty inefficient

        # "transfer" passed file descriptors
        if self.passed_file_descriptors is not None:
            newmsg.passed_file_descriptors=self.passed_file_descriptors
        if self._from_descr is not None:
            newmsg._from_descr="↔"+self._from_descr
        return newmsg

    #
    # Message specific features
    #
    def get_call_rule(self) -> dict[str,str]|None:
        """Parse an AddMatch or RemoveMatch call to retreive the match rules"""
        if self.signature=="s":
            data="---"
            try:
                data=self.unmarshall_body(self.signature)
                # data will be like "type='signal',sender='org.freedesktop.portal.Desktop',
                # interface='org.freedesktop.D-Bus.Properties',member='PropertiesChanged',path='/org/freedesktop/portal/desktop',
                # arg0='org.freedesktop.portal.ScreenCast'"
                res:dict[str,str]={}
                for kv in data.split(","):
                    (key,value)=kv.split("=")
                    if value[0]=="'" and value[-1]=="'":
                        value=value[1:-1]
                    res[key]=value
                return res
            except Exception:
                txt=f"Could not parse message's match rule '{data}'"
                print(f"WARNING: {txt}")
                syslog.syslog(syslog.LOG_WARNING, txt)
        raise Exception("Invalid non add or remove message")

if __name__=="__main__":
    import dbus_example_messages
    for data in dbus_example_messages.test_messages:
        print("==========================")
        msg=Message(data)
        msg2=Message(msg.blob)
        if msg.blob!=msg2.blob:
            raise Exception("Invalid blob parsing")
        msg2=msg.change_header({})
        if msg.blob!=msg2.blob:
            raise Exception("Invalid (un)changed headers encoding")

        if msg.destination is not None:
            for dest in (":1",  ":1.2.3.4.5.6.7.8.9.10.11.12.13.14.15"):
                print(f"==> Setting destination to {dest}")
                msg2=msg.change_header({MessageHeaderType.DESTINATION: dest})
                if msg2.destination!=dest:
                    raise Exception(f"Got {msg2.destination}, expected {dest}")

    exit(0)
