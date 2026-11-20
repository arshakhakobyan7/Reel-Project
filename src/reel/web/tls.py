"""HTTPS for the dashboard on the local network (T-1008, D-060).

Reel makes its own small certificate authority (CA) and, with it, the certificate of this
computer. Each staff computer trusts the CA once (the file ``reel-ca.crt``); after that the
browser shows the dashboard as an ordinary secure site.

- The CA may only sign this computer's names and private network addresses (X.509 name
  constraints). Even a stolen CA key cannot be used to pose as any other site.
- Both private keys are encrypted with a random password that is kept in the secret store
  (Windows Credential Manager, D-047), never in a file.
- A renewal keeps the CA, so staff computers do not need to trust anything again. A new CA is
  made only when asked for, or when the CA cannot cover this computer's names any more.
"""

import datetime as dt
import hashlib
import ipaddress
import logging
import os
import re
import secrets
import socket
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from reel.auth.store import SecretStore, make_secret_store
from reel.config.loader import ConfigError, LoadedConfig
from reel.logging_setup import register_secret

log = logging.getLogger("reel.web")

FOLDER = "tls"  # inside the data folder (git-ignored)
CA_CERT, CA_KEY = "reel-ca.crt", "reel-ca.key"
SERVER_CERT, SERVER_KEY = "server.crt", "server.key"
PASSWORD_KEY = "web/tls-key"  # secret store entry
CA_DAYS = 3650
SERVER_DAYS = 397  # browsers refuse server certificates valid for more than 398 days
RENEW_DAYS = 30
PRIVATE = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
NETWORKS = tuple(ipaddress.IPv4Network(n) for n in PRIVATE)
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DNS = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})*$")
_PEM = serialization.Encoding.PEM

Address = ipaddress.IPv4Address


class TlsError(RuntimeError):
    """The certificate cannot be made or opened."""


@dataclass(frozen=True)
class CertInfo:
    folder: Path
    names: tuple[str, ...]
    addresses: tuple[str, ...]
    expires: dt.datetime
    ca_expires: dt.datetime
    ca_thumbprint: str  # SHA-1, as Windows shows it in the certificate's details
    ca_sha256: str
    new_ca: bool = False


def tls_folder(cfg: LoadedConfig) -> Path:
    return cfg.paths.data_dir / FOLDER


def check_name(name: str) -> str:
    value = name.strip().lower().rstrip(".")
    if not _DNS.fullmatch(value):
        raise TlsError(f"{name!r} is not a computer name (letters, digits, '-' and '.')")
    return value


def check_address(text: str) -> Address:
    try:
        value = ipaddress.ip_address(text.strip())
    except ValueError:
        raise TlsError(f"{text!r} is not an IPv4 address") from None
    if not isinstance(value, Address) or not any(value in n for n in NETWORKS):
        raise TlsError(f"{text!r} is not a private IPv4 address (10.x, 172.16-31.x, 192.168.x)")
    return value


def local_names(extra: Iterable[str] = ()) -> list[str]:
    """This computer's name, its ``.local`` form, ``localhost``, then the extra names."""
    host = check_name(socket.gethostname())
    names = [host, f"{host}.local", "localhost", *(check_name(n) for n in extra)]
    return list(dict.fromkeys(names))


def local_addresses(extra: Iterable[str] = ()) -> list[Address]:
    """127.0.0.1, this computer's private IPv4 addresses, then the extra addresses."""
    found: set[Address] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(ipaddress.IPv4Address(info[4][0]))
    except OSError:
        pass
    try:  # the address used to reach the network; a UDP "connect" sends nothing
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            found.add(ipaddress.IPv4Address(probe.getsockname()[0]))
    except OSError:
        pass
    usable = sorted(a for a in found if any(a in n for n in NETWORKS) and not a.is_loopback)
    loopback = ipaddress.IPv4Address("127.0.0.1")
    return list(dict.fromkeys([loopback, *usable, *(check_address(a) for a in extra)]))


def key_password(store: SecretStore, *, create: bool) -> str:
    value = store.get(PASSWORD_KEY)
    if not value:
        if not create:
            raise TlsError(
                f"the password of the certificate keys is not in the {store.name} store; "
                "make new keys with 'reel cert --new-ca'"
            )
        value = secrets.token_urlsafe(32)
        store.set(PASSWORD_KEY, value)
    register_secret(value)
    return value


def _key_pem(key: ec.EllipticCurvePrivateKey, password: str) -> bytes:
    return key.private_bytes(
        _PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(password.encode("utf-8")),
    )


def _open_key(path: Path, password: str) -> ec.EllipticCurvePrivateKey:
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password.encode("utf-8"))
    except ValueError, TypeError:
        raise TlsError(
            f"{path.name} cannot be opened with the password in the secret store; "
            "make new keys with 'reel cert --new-ca'"
        ) from None
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise TlsError(f"{path.name} is not a key made by Reel")
    return key


def _write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _usage(*, sign_certificates: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=not sign_certificates, content_commitment=False,
        key_encipherment=False, data_encipherment=False, key_agreement=False,
        key_cert_sign=sign_certificates, crl_sign=sign_certificates,
        encipher_only=False, decipher_only=False,
    )  # fmt: skip


def make_ca(key: ec.EllipticCurvePrivateKey, names: Sequence[str], now: dt.datetime):
    """A CA that may sign only ``names`` (and their sub-names) and private IPv4 addresses."""
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Reel"),
        x509.NameAttribute(NameOID.COMMON_NAME, f"Reel local CA ({names[0]})"),
    ])  # fmt: skip
    permitted = [*(x509.DNSName(n) for n in names), *(x509.IPAddress(n) for n in NETWORKS)]
    public = key.public_key()
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(public)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(hours=1))
        .not_valid_after(now + dt.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_usage(sign_certificates=True), critical=True)
        .add_extension(x509.NameConstraints(permitted, None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False)
        .sign(key, hashes.SHA256())
    )


def make_server(
    ca: x509.Certificate,
    ca_key: ec.EllipticCurvePrivateKey,
    key: ec.EllipticCurvePrivateKey,
    names: Sequence[str],
    addresses: Sequence[Address],
    now: dt.datetime,
) -> x509.Certificate:
    alternative = [*(x509.DNSName(n) for n in names), *(x509.IPAddress(a) for a in addresses)]
    end = min(now + dt.timedelta(days=SERVER_DAYS), ca.not_valid_after_utc)
    authority = ca.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(hours=1))
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_usage(sign_certificates=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName(alternative), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(authority),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )


def covers(ca: x509.Certificate, names: Sequence[str], addresses: Sequence[Address]) -> bool:
    """True when the CA's name constraints allow every name and address."""
    try:
        permitted = ca.extensions.get_extension_for_class(x509.NameConstraints).value
    except x509.ExtensionNotFound:
        return False
    subtrees = permitted.permitted_subtrees or []
    dns = [s.value for s in subtrees if isinstance(s, x509.DNSName)]
    networks = [s.value for s in subtrees if isinstance(s, x509.IPAddress)]
    names_ok = all(any(n == d or n.endswith("." + d) for d in dns) for n in names)
    return names_ok and all(any(a in net for net in networks) for a in addresses)


def _load_ca(folder: Path, password: str):
    cert_path, key_path = folder / CA_CERT, folder / CA_KEY
    if not cert_path.is_file() or not key_path.is_file():
        return None
    ca = x509.load_pem_x509_certificate(cert_path.read_bytes())
    key = _open_key(key_path, password)
    if key.public_key().public_numbers() != ca.public_key().public_numbers():
        raise TlsError(f"{CA_KEY} does not belong to {CA_CERT}; use 'reel cert --new-ca'")
    return ca, key


def create(
    folder: Path,
    password: str,
    names: Sequence[str],
    addresses: Sequence[Address],
    *,
    new_ca: bool = False,
    now: dt.datetime | None = None,
) -> CertInfo:
    """Make (or renew) the server certificate; make the CA too when needed."""
    now = now or dt.datetime.now(dt.UTC)
    folder.mkdir(parents=True, exist_ok=True)
    found = None if new_ca else _load_ca(folder, password)
    if found is not None:
        ca, ca_key = found
        if ca.not_valid_after_utc < now + dt.timedelta(days=RENEW_DAYS):
            log.warning("the CA expires on %s: a new CA is made", ca.not_valid_after_utc.date())
            found = None
        elif not covers(ca, names, addresses):
            log.warning("the CA does not cover %s: a new CA is made", ", ".join(names))
            found = None
    if found is None:
        ca_key = ec.generate_private_key(ec.SECP256R1())
        ca = make_ca(ca_key, names, now)
        _write(folder / CA_KEY, _key_pem(ca_key, password))
        _write(folder / CA_CERT, ca.public_bytes(_PEM))
    key = ec.generate_private_key(ec.SECP256R1())
    server = make_server(ca, ca_key, key, names, addresses, now)
    _write(folder / SERVER_KEY, _key_pem(key, password))
    _write(folder / SERVER_CERT, server.public_bytes(_PEM) + ca.public_bytes(_PEM))
    log.info("HTTPS certificate made for %s, valid until %s", ", ".join(names),
             server.not_valid_after_utc.date())  # fmt: skip
    return _info(folder, server, ca, new_ca=found is None)


def _info(folder: Path, server: x509.Certificate, ca: x509.Certificate, *, new_ca: bool):
    alternative = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    der = ca.public_bytes(serialization.Encoding.DER)
    return CertInfo(
        folder=folder,
        names=tuple(alternative.get_values_for_type(x509.DNSName)),
        addresses=tuple(str(a) for a in alternative.get_values_for_type(x509.IPAddress)),
        expires=server.not_valid_after_utc,
        ca_expires=ca.not_valid_after_utc,
        ca_thumbprint=_hex(hashlib.sha1(der).digest()),  # an id only, as Windows shows it
        ca_sha256=_hex(hashlib.sha256(der).digest()),
        new_ca=new_ca,
    )


def _hex(digest: bytes) -> str:
    return ":".join(f"{b:02X}" for b in digest)


def read(folder: Path) -> CertInfo | None:
    """The current certificate, or None when there is none."""
    server_path, ca_path = folder / SERVER_CERT, folder / CA_CERT
    if not server_path.is_file() or not ca_path.is_file():
        return None
    server = x509.load_pem_x509_certificate(server_path.read_bytes())
    ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
    return _info(folder, server, ca, new_ca=False)


def uvicorn_options(
    cfg: LoadedConfig, store: SecretStore | None = None, now: dt.datetime | None = None
) -> dict[str, str]:
    """The TLS options for uvicorn; checks the files and the key first."""
    folder = tls_folder(cfg)
    info = read(folder)
    if info is None or not (folder / SERVER_KEY).is_file():
        raise ConfigError(
            f"web.tls is on, but {folder} has no certificate; make it with 'reel cert'"
        )
    try:
        password = key_password(store or make_secret_store(cfg), create=False)
        _open_key(folder / SERVER_KEY, password)
    except TlsError as exc:
        raise ConfigError(str(exc)) from None
    now = now or dt.datetime.now(dt.UTC)
    if info.expires <= now:
        log.error("the HTTPS certificate expired on %s; renew it with 'reel cert'",
                  info.expires.date())  # fmt: skip
    elif info.expires - now < dt.timedelta(days=RENEW_DAYS):
        log.warning("the HTTPS certificate expires on %s; renew it with 'reel cert'",
                    info.expires.date())  # fmt: skip
    return {
        "ssl_certfile": str(folder / SERVER_CERT),
        "ssl_keyfile": str(folder / SERVER_KEY),
        "ssl_keyfile_password": password,
    }
