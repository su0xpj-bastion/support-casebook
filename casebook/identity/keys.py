from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@dataclass
class SigningCert:
    key_pem: bytes
    cert_pem: bytes
    not_after: dt.datetime
    kid: str

    @property
    def public_key(self):  # noqa: ANN201
        return x509.load_pem_x509_certificate(self.cert_pem).public_key()


def signing_cert(cn: str, not_before: dt.datetime, not_after: dt.datetime, kid: str) -> SigningCert:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(not_before).not_valid_after(not_after)
            .sign(key, hashes.SHA256()))
    return SigningCert(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()),
                       cert.public_bytes(serialization.Encoding.PEM), not_after, kid)


def utc(y: int, m: int, d: int, hh: int = 9, mm: int = 0) -> dt.datetime:
    return dt.datetime(y, m, d, hh, mm, tzinfo=dt.timezone.utc)
