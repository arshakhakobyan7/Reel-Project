"""Secrets on a Linux server (T-1018): the owner-only file store and AES-GCM upload state."""

import base64
import os
from types import SimpleNamespace

import pytest

from reel.auth.store import MemorySecretStore, PosixSecretStore, SecretStoreError
from reel.config.schema import AuthSettings
from reel.publish.google_api import (
    UPLOAD_SESSION_KEY,
    GoogleApiError,
    KeySessionCodec,
    WindowsSessionCodec,
    default_session_codec,
)

URL = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=secret-session-capability"
POSIX = pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions")


def test_the_key_codec_round_trips_without_plaintext_and_keeps_one_key():
    store = MemorySecretStore()
    sealed = KeySessionCodec(store).seal(URL)
    assert sealed.startswith("aesgcm:") and "secret-session" not in sealed
    key = store.get(UPLOAD_SESSION_KEY)
    assert key is not None and len(base64.b64decode(key)) == 32
    assert KeySessionCodec(store).unseal(sealed) == URL  # a new codec, the same key
    assert store.get(UPLOAD_SESSION_KEY) == key
    assert KeySessionCodec(store).seal(URL) != sealed  # a fresh nonce every time


@pytest.mark.parametrize(
    "protected",
    ["dpapi:abc", "aesgcm:not-base64!", "aesgcm:" + base64.b64encode(b"short").decode()],
)
def test_the_key_codec_refuses_what_it_did_not_seal(protected):
    with pytest.raises(GoogleApiError, match="restore"):
        KeySessionCodec(MemorySecretStore()).unseal(protected)


def test_another_key_or_a_changed_byte_cannot_be_opened():
    sealed = KeySessionCodec(MemorySecretStore()).seal(URL)
    with pytest.raises(GoogleApiError, match="restore"):
        KeySessionCodec(MemorySecretStore()).unseal(sealed)  # another server's key
    store = MemorySecretStore()
    good = KeySessionCodec(store).seal(URL)
    raw = bytearray(base64.b64decode(good[len("aesgcm:") :]))
    raw[-1] ^= 1
    with pytest.raises(GoogleApiError, match="restore"):
        KeySessionCodec(store).unseal("aesgcm:" + base64.b64encode(bytes(raw)).decode())


def test_a_broken_key_in_the_store_is_reported():
    store = MemorySecretStore()
    store.set(UPLOAD_SESSION_KEY, base64.b64encode(b"x" * 16).decode())
    with pytest.raises(GoogleApiError, match="not valid"):
        KeySessionCodec(store).seal(URL)


def test_the_default_codec_follows_the_platform():
    tokens = SimpleNamespace(store=MemorySecretStore())
    codec = default_session_codec(tokens)
    if os.name == "nt":
        assert isinstance(codec, WindowsSessionCodec)
    else:
        assert isinstance(codec, KeySessionCodec) and codec.store is tokens.store
    assert isinstance(default_session_codec(None), WindowsSessionCodec | KeySessionCodec)


def test_the_posix_store_is_a_setting():
    assert AuthSettings(store="posix").posix_file == "data/secrets/tokens.json"


@pytest.mark.skipif(os.name != "nt", reason="Windows")
def test_the_posix_store_is_refused_on_windows(tmp_path):
    with pytest.raises(SecretStoreError, match="Linux"):
        PosixSecretStore(tmp_path / "tokens.json")


@POSIX
def test_the_posix_store_keeps_values_owner_only(tmp_path):
    path = tmp_path / "secrets" / "tokens.json"
    store = PosixSecretStore(path)
    assert store.get("google") is None
    store.set("google", "refresh-token-value")
    store.set("other", "x")
    assert store.get("google") == "refresh-token-value"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert store.delete("other") is True and store.delete("other") is False
    assert not path.with_name(path.name + ".part").exists()


@POSIX
@pytest.mark.parametrize("target, mode", [("file", 0o644), ("folder", 0o755)])
def test_the_posix_store_refuses_a_file_others_can_read(tmp_path, target, mode):
    path = tmp_path / "secrets" / "tokens.json"
    store = PosixSecretStore(path)
    store.set("google", "refresh-token-value")
    os.chmod(path if target == "file" else path.parent, mode)
    with pytest.raises(SecretStoreError, match="chmod"):
        store.get("google")


@POSIX
def test_make_secret_store_builds_the_posix_store(tmp_path, project, write_config):
    from reel.auth.store import make_secret_store
    from reel.config import load_config

    write_config('[auth]\nstore = "posix"\nposix_file = "data/secrets/t.json"\n')
    store = make_secret_store(load_config(project / "config" / "reel.toml"))
    assert isinstance(store, PosixSecretStore)
    assert store.path == (project / "data" / "secrets" / "t.json").resolve()
