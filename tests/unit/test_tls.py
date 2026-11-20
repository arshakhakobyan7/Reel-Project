"""HTTPS for the dashboard on the local network (T-1008, D-060).

The handshakes are real: Python's OpenSSL checks the chain, the names and the CA's name
constraints the same way a browser does.
"""

import dataclasses
import datetime as dt
import http.client
import ipaddress
import json
import logging
import socket
import ssl
import threading
import time
import urllib.request

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from reel.auth.store import MemorySecretStore
from reel.cli import main
from reel.config import load_config
from reel.config.loader import ConfigError
from reel.db import init_database, open_database
from reel.runtime import Parts, Runtime
from reel.web import tls

PASSWORD = "key-password-" + "k" * 20
NAMES = ["reel-pc", "reel-pc.local", "localhost"]
ADDRESSES = [ipaddress.IPv4Address("127.0.0.1"), ipaddress.IPv4Address("192.168.1.20")]
NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)
PERMITTED_VIOLATION = 47  # X509_V_ERR_PERMITTED_VIOLATION
WEB_ONLY = Parts(watcher=False, worker=False, notifier=False, cleanup=False)


def make(folder, names=NAMES, addresses=ADDRESSES, **kwargs):
    return tls.create(folder, PASSWORD, names, addresses, **kwargs)


def handshake(folder, server_hostname):
    """A TLS handshake on 127.0.0.1 with the made files; None, or the verification error."""
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(folder / tls.SERVER_CERT, folder / tls.SERVER_KEY, PASSWORD)
    client = ssl.create_default_context(cafile=folder / tls.CA_CERT)
    with socket.create_server(("127.0.0.1", 0)) as listener:

        def answer():
            connection, _ = listener.accept()
            try:
                with server.wrap_socket(connection, server_side=True) as wrapped:
                    wrapped.recv(1)
            except OSError:  # the client refused the certificate
                pass

        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        try:
            with (
                socket.create_connection(listener.getsockname(), timeout=5) as raw,
                client.wrap_socket(raw, server_hostname=server_hostname) as wrapped,
            ):
                wrapped.sendall(b"x")
            return None
        except ssl.SSLCertVerificationError as exc:
            return exc
        finally:
            thread.join(5)


def test_the_certificate_opens_by_every_name_and_address(tmp_path):
    info = make(tmp_path)
    assert info.new_ca and info.names == tuple(NAMES)
    assert info.addresses == ("127.0.0.1", "192.168.1.20")
    for host in [*NAMES, "127.0.0.1", "192.168.1.20"]:
        assert handshake(tmp_path, host) is None, host


def test_other_names_and_addresses_are_refused(tmp_path):
    make(tmp_path)
    assert handshake(tmp_path, "example.com") is not None
    assert handshake(tmp_path, "192.168.1.99") is not None


def test_the_ca_cannot_sign_any_other_site(tmp_path):
    make(tmp_path)
    ca = x509.load_pem_x509_certificate((tmp_path / tls.CA_CERT).read_bytes())
    ca_key = serialization.load_pem_private_key(
        (tmp_path / tls.CA_KEY).read_bytes(), PASSWORD.encode()
    )
    for names, addresses, host in (
        (["bank.example"], [], "bank.example"),
        (["reel-pc"], [ipaddress.IPv4Address("8.8.8.8")], "8.8.8.8"),
    ):
        key = ec.generate_private_key(ec.SECP256R1())
        forged = tls.make_server(ca, ca_key, key, names, addresses, dt.datetime.now(dt.UTC))
        (tmp_path / tls.SERVER_KEY).write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(PASSWORD.encode()),
            )
        )
        (tmp_path / tls.SERVER_CERT).write_bytes(
            forged.public_bytes(serialization.Encoding.PEM)
            + ca.public_bytes(serialization.Encoding.PEM)
        )
        error = handshake(tmp_path, host)
        assert error is not None and error.verify_code == PERMITTED_VIOLATION, host
        assert not tls.covers(ca, names, addresses)


def test_both_keys_are_encrypted(tmp_path):
    make(tmp_path)
    for name in (tls.CA_KEY, tls.SERVER_KEY):
        data = (tmp_path / name).read_bytes()
        assert data.startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----")
        with pytest.raises(TypeError):
            serialization.load_pem_private_key(data, None)
        with pytest.raises(ValueError):
            serialization.load_pem_private_key(data, b"wrong password")
    assert not list(tmp_path.glob("*.tmp"))


def test_a_renewal_keeps_the_ca(tmp_path):
    first = make(tmp_path, now=NOW)
    old = x509.load_pem_x509_certificate((tmp_path / tls.SERVER_CERT).read_bytes())
    second = make(tmp_path, addresses=[*ADDRESSES, ipaddress.IPv4Address("10.0.0.5")])
    new = x509.load_pem_x509_certificate((tmp_path / tls.SERVER_CERT).read_bytes())
    assert not second.new_ca and second.ca_thumbprint == first.ca_thumbprint
    assert new.serial_number != old.serial_number and "10.0.0.5" in second.addresses
    assert handshake(tmp_path, "10.0.0.5") is None


def test_a_new_ca_when_asked_when_the_name_changes_or_when_it_ends(tmp_path):
    first = make(tmp_path, now=NOW)
    asked = make(tmp_path, new_ca=True)
    assert asked.new_ca and asked.ca_thumbprint != first.ca_thumbprint
    renamed = make(tmp_path, names=["other-pc", "other-pc.local", "localhost"])
    assert renamed.new_ca and handshake(tmp_path, "other-pc") is None
    ends = make(tmp_path, names=["other-pc"], now=NOW)
    late = make(tmp_path, names=["other-pc"], now=NOW + dt.timedelta(days=tls.CA_DAYS - 10))
    assert late.new_ca and late.ca_thumbprint != ends.ca_thumbprint


def test_validity_and_purposes(tmp_path):
    info = make(tmp_path, now=NOW)
    assert info.expires == NOW + dt.timedelta(days=tls.SERVER_DAYS)
    assert info.ca_expires == NOW + dt.timedelta(days=tls.CA_DAYS)
    ca = x509.load_pem_x509_certificate((tmp_path / tls.CA_CERT).read_bytes())
    server = x509.load_pem_x509_certificate((tmp_path / tls.SERVER_CERT).read_bytes())
    basic = ca.extensions.get_extension_for_class(x509.BasicConstraints)
    assert basic.critical and basic.value.ca and basic.value.path_length == 0
    assert ca.extensions.get_extension_for_class(x509.NameConstraints).critical
    assert not server.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    purposes = server.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(purposes) == [x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]
    assert len(info.ca_thumbprint.split(":")) == 20 and len(info.ca_sha256.split(":")) == 32
    assert tls.read(tmp_path) == dataclasses.replace(info, new_ca=False)
    assert tls.read(tmp_path / "none") is None


def test_names_and_addresses_are_checked():
    assert tls.check_name(" Reel-PC. ") == "reel-pc"
    assert tls.check_name("reel.school.lan") == "reel.school.lan"
    for bad in ("bad name", "-reel", "reel_pc", "", "a" * 64):
        with pytest.raises(tls.TlsError):
            tls.check_name(bad)
    assert tls.check_address("192.168.0.7") == ipaddress.IPv4Address("192.168.0.7")
    for bad in ("8.8.8.8", "::1", "fe80::1", "169.254.1.1", "not an address"):
        with pytest.raises(tls.TlsError):
            tls.check_address(bad)


def test_this_computers_names_and_addresses(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "Reel-PC")
    found = [("192.168.1.20", 0), ("8.8.8.8", 0), ("127.0.0.1", 0)]
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 0, "", address) for address in found]
    )
    assert tls.local_names(["Reel.School.Lan"]) == [
        "reel-pc", "reel-pc.local", "localhost", "reel.school.lan",
    ]  # fmt: skip
    addresses = [str(a) for a in tls.local_addresses(["10.9.8.7"])]
    assert addresses[0] == "127.0.0.1" and addresses[-1] == "10.9.8.7"
    assert "192.168.1.20" in addresses and "8.8.8.8" not in addresses
    assert addresses.count("127.0.0.1") == 1


def test_the_key_password_lives_in_the_secret_store():
    store = MemorySecretStore()
    with pytest.raises(tls.TlsError, match="reel cert --new-ca"):
        tls.key_password(store, create=False)
    made = tls.key_password(store, create=True)
    assert len(made) >= 32 and store.get(tls.PASSWORD_KEY) == made
    assert tls.key_password(store, create=False) == made


def https_config(write_config, project):
    write_config('[web]\ntls = true\nbase_url = "https://reel-pc:8080"\n')
    return load_config(project / "config" / "reel.toml")


def test_tls_needs_an_https_address(project, write_config):
    write_config("[web]\ntls = true\n")
    with pytest.raises(ConfigError, match="https"):
        load_config(project / "config" / "reel.toml")


def test_serve_options_check_the_files_the_key_and_the_date(project, write_config, caplog):
    cfg = https_config(write_config, project)
    store = MemorySecretStore()
    with pytest.raises(ConfigError, match="reel cert"):
        tls.uvicorn_options(cfg, store)
    folder = tls.tls_folder(cfg)
    tls.create(folder, tls.key_password(store, create=True), NAMES, ADDRESSES, now=NOW)
    options = tls.uvicorn_options(cfg, store, now=NOW)
    assert options == {
        "ssl_certfile": str(folder / tls.SERVER_CERT),
        "ssl_keyfile": str(folder / tls.SERVER_KEY),
        "ssl_keyfile_password": store.get(tls.PASSWORD_KEY),
    }
    with caplog.at_level(logging.WARNING, logger="reel.web"):
        tls.uvicorn_options(cfg, store, now=NOW + dt.timedelta(days=tls.SERVER_DAYS - 5))
        tls.uvicorn_options(cfg, store, now=NOW + dt.timedelta(days=tls.SERVER_DAYS + 1))
    assert [r.levelname for r in caplog.records] == ["WARNING", "ERROR"]
    store.set(tls.PASSWORD_KEY, "another-password-" + "x" * 20)
    with pytest.raises(ConfigError, match="cannot be opened"):
        tls.uvicorn_options(cfg, store)
    with pytest.raises(ConfigError, match="not in the memory store"):
        tls.uvicorn_options(cfg, MemorySecretStore())


def test_reel_cert(project, write_config, capsys):
    write_config('[auth]\nstore = "file"\n')
    config = str(project / "config" / "reel.toml")
    cfg = load_config(config)

    def run(*args):
        code = main(["--config", config, "cert", *args])
        return code, capsys.readouterr()

    code, out = run("--show")
    assert code == 1 and "reel cert" in out.out
    code, out = run("--name", "reel.school.lan", "--ip", "10.1.2.3")
    assert code == 0 and "NEW: install it on every staff computer" in out.out
    assert "reel.school.lan" in out.out and "10.1.2.3" in out.out
    assert "tls = true" in out.out
    assert f'base_url = "https://{socket.gethostname().lower()}:8080"' in out.out
    folder = tls.tls_folder(cfg)
    assert {p.name for p in folder.iterdir()} == {
        tls.CA_CERT, tls.CA_KEY, tls.SERVER_CERT, tls.SERVER_KEY,
    }  # fmt: skip
    thumbprint = tls.read(folder).ca_thumbprint
    code, again = run()
    assert code == 0 and "unchanged" in again.out and thumbprint in again.out
    code, shown = run("--show")
    assert code == 0 and thumbprint in shown.out
    code, bad = run("--ip", "8.8.8.8")
    assert code == 1 and "private" in bad.err
    secrets_file = cfg.resolve(cfg.settings.auth.dev_file)
    password = json.loads(secrets_file.read_text(encoding="utf-8"))
    for value in _strings(password):
        assert value not in out.out + again.out + shown.out + bad.err


def _strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, str) and len(value) >= 20:
        yield value


def test_the_dashboard_answers_over_https(project, write_config):
    cfg = https_config(write_config, project)
    store = MemorySecretStore()
    folder = tls.tls_folder(cfg)
    tls.create(folder, tls.key_password(store, create=True), NAMES, ADDRESSES)
    init_database(cfg.paths.database_file)
    db = open_database(cfg.paths.database_file)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with db:
        runtime = Runtime(
            db, lambda: cfg, parts=WEB_ONLY,
            host="127.0.0.1", port=port, secret="runtime-secret-" + "r" * 30,
            tls=tls.uvicorn_options(cfg, store),
        )  # fmt: skip
        runtime.start()
        try:
            deadline = time.monotonic() + 15
            while not runtime.web_started and time.monotonic() < deadline:
                time.sleep(0.05)
            context = ssl.create_default_context(cafile=folder / tls.CA_CERT)
            with urllib.request.urlopen(
                f"https://localhost:{port}/login", timeout=5, context=context
            ) as page:
                assert page.status == 200 and "Մուտք" in page.read().decode("utf-8")
                cookie = page.headers["Set-Cookie"]
                assert "Secure" in cookie and "HttpOnly" in cookie
            with pytest.raises((OSError, http.client.HTTPException)):
                urllib.request.urlopen(f"http://127.0.0.1:{port}/login", timeout=5)
        finally:
            runtime.stop(timeout=20)
