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

import datetime
import os
import secrets
import string
import tempfile

import cryptography.x509 as x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption, pkcs12)
from cryptography.x509.oid import NameOID


class RedirectCA:
    def __init__(self, ca_dir:str|None=None):
        """Certification Authority. If ca_dir is None, then a TMP directpry is used and everything is destroyed with the object
        The private key and self signed certificate are generated if they don't exist
        """
        if ca_dir is None:
            self._tmpdir=tempfile.TemporaryDirectory()
            self._ca_dir=self._tmpdir.name
        else:
            self._ca_dir=ca_dir
        self._ca_key_file=os.path.join(self._ca_dir, "root-ca.key")
        self._ca_cert_file=os.path.join(self._ca_dir, "root-ca.crt")
        self._certs_dir=os.path.join(self._ca_dir, "certs")

        self._ca_cert:x509.Certificate|None=None
        self._ca_key:rsa.RSAPrivateKey|None=None

        os.makedirs(self._certs_dir, exist_ok=True)
        os.chmod(self._ca_dir, 0o700)
        if os.path.exists(self._ca_key_file) and os.path.exists(self._ca_cert_file):
            self._load_root_ca()
        else:
            self._generate_root_ca()

    @property
    def ca_cert_file(self) -> str:
        return self._ca_cert_file

    def generate_ca_pkcs12(self, p12_file:str) -> str:
        """Generate a password and create a PKCS#12 file with the CA's private key and certificate
        Returns: the random password
        """
        alphabet=string.ascii_letters + string.digits
        password=''.join(secrets.choice(alphabet) for _ in range(16))
        p12_data=pkcs12.serialize_key_and_certificates(
            name=b"RedirectCA", key=self._ca_key, cert=self._ca_cert, cas=None,
            encryption_algorithm=BestAvailableEncryption(password.encode())
        )
        with open(p12_file, "wb") as f:
            f.write(p12_data)
        return password

    def _generate_root_ca(self):
        """Generate new credentials (CA's private key and certificate)"""
        key=rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject=issuer=x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "Web access denied CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PADSI"),
        ])
        cert=x509.CertificateBuilder()\
            .subject_name(subject)\
            .issuer_name(issuer)\
            .public_key(key.public_key())\
            .serial_number(x509.random_serial_number())\
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))\
            .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))\
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)\
            .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False, data_encipherment=False,
               key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True) \
            .sign(key, hashes.SHA256())

        with open(self._ca_cert_file, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(self._ca_key_file, "wb") as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            ))

        self._ca_cert=cert
        self._ca_key=key

    def _load_root_ca(self):
        """Load existing credentials (CA's private key and certificate)"""
        try:
            with open(self._ca_cert_file, "rb") as fd:
                data=fd.read()
                self._ca_cert=x509.load_pem_x509_certificate(data, default_backend())
            with open(self._ca_key_file, "rb") as fd:
                data=fd.read()
                self._ca_key=serialization.load_pem_private_key( # pyright: ignore
                    data, password=None, backend=default_backend()
                )
        except Exception as e:
            self._ca_cert=None
            self._ca_key=None
            raise e

    def generate_cert_for_domain(self, domain:str) -> tuple[str,str]:
        if self._ca_cert is None or self._ca_key is None:
            raise Exception("CODEBUG: CA is not yet operational")
        cert_file=os.path.join(self._certs_dir, f"{domain}.crt")
        key_file=os.path.join(self._certs_dir, f"{domain}.key")
        if os.path.exists(cert_file) and os.path.exists(key_file):
            return (cert_file, key_file)

        key=rsa.generate_private_key(public_exponent=65537, key_size=2048)

        subject=x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PADSI"),
            x509.NameAttribute(NameOID.COMMON_NAME, domain),
        ])

        cert=x509.CertificateBuilder()\
            .subject_name(subject)\
            .issuer_name(self._ca_cert.subject)\
            .public_key(key.public_key())\
            .serial_number(x509.random_serial_number())\
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))\
            .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))\
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(domain)]),
                critical=False
            )\
            .sign(self._ca_key, hashes.SHA256())

        with open(cert_file, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(key_file, "wb") as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            ))

        return (cert_file, key_file)

    def delete_private_key(self):
        os.remove(self._ca_key_file)
