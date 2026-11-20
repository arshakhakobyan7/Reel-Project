"""``reel serve`` (the staff dashboard), ``reel user`` (staff accounts) (T-802) and
``reel cert`` (the HTTPS certificate, T-1008)."""

import argparse
import getpass
import ipaddress
import logging
import secrets
import sys

from sqlalchemy import func, select

from reel.auth.store import SecretStore, SecretStoreError, make_secret_store
from reel.config.loader import ConfigError, ConfigProvider, LoadedConfig, find_config_file
from reel.db import Database, DatabaseError, StaffUser, normalize_username, open_database
from reel.logging_setup import configure_from_config, register_secret
from reel.web import tls
from reel.web.security import MIN_SECRET_LENGTH, PasswordError, hash_password

log = logging.getLogger("reel.web")
SESSION_KEY = "web/session"


def session_secret(cfg: LoadedConfig, store: SecretStore | None = None) -> str:
    """``REEL_WEB_SESSION_SECRET`` if set; otherwise a random secret kept in the secret
    store (Windows Credential Manager), made on the first start."""
    value = cfg.secrets.get("REEL_WEB_SESSION_SECRET")
    if value:
        if len(value) < MIN_SECRET_LENGTH:
            raise ConfigError(
                f"REEL_WEB_SESSION_SECRET must have at least {MIN_SECRET_LENGTH} characters"
            )
    else:
        store = store or make_secret_store(cfg)
        value = store.get(SESSION_KEY)
        if not value or len(value) < MIN_SECRET_LENGTH:
            value = secrets.token_urlsafe(48)
            store.set(SESSION_KEY, value)
            log.info("a new session secret was made and kept in the %s store", store.name)
    register_secret(value)
    return value


def register(commands: argparse._SubParsersAction) -> None:
    serve = commands.add_parser(
        "serve", help="run Reel: dashboard, folder watcher, workers and staff notifications"
    )
    serve.add_argument("--host", help="address to listen on (default: web.host)")
    serve.add_argument("--port", type=int, help="port (default: web.port)")
    for part in ("web", "watcher", "worker", "notifier", "cleanup"):
        serve.add_argument(f"--no-{part}", action="store_true", help=f"do not run the {part}")
    serve.set_defaults(handler=run_serve)

    user = commands.add_parser("user", help="manage staff accounts of the dashboard")
    actions = user.add_subparsers(dest="user_command", metavar="<action>", required=True)
    add = actions.add_parser("add", help="add a staff account")
    add.add_argument("username")
    add.add_argument("--name", default="", help="display name")
    add.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    add.set_defaults(handler=run_user_add)
    password = actions.add_parser("password", help="set a new password (ends the sessions)")
    password.add_argument("username")
    password.add_argument("--password-stdin", action="store_true")
    password.set_defaults(handler=run_user_password)
    for name, active in (("disable", False), ("enable", True)):
        action = actions.add_parser(name, help=f"{name} a staff account")
        action.add_argument("username")
        action.set_defaults(handler=run_user_active, active=active)
    listing = actions.add_parser("list", help="list staff accounts")
    listing.set_defaults(handler=run_user_list)

    cert = commands.add_parser(
        "cert", help="make or renew the dashboard's HTTPS certificate for the local network"
    )
    cert.add_argument(
        "--name", action="append", default=[], help="another name of this computer (repeatable)"
    )
    cert.add_argument(
        "--ip", action="append", default=[], metavar="ADDRESS",
        help="another private IPv4 address of this computer (repeatable)",
    )  # fmt: skip
    cert.add_argument(
        "--new-ca", action="store_true",
        help="make a new CA too; every staff computer must trust it again",
    )  # fmt: skip
    cert.add_argument("--show", action="store_true", help="only show the current certificate")
    cert.set_defaults(handler=run_cert)


def _open(args: argparse.Namespace) -> tuple[LoadedConfig, Database]:
    cfg = ConfigProvider(find_config_file(args.config)).get()
    return cfg, open_database(cfg.paths.database_file)


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Password again: "):
        raise PasswordError("the two passwords are different")
    return first


def run_user_add(args: argparse.Namespace) -> int:
    try:
        username = normalize_username(args.username)
        password_hash = hash_password(_read_password(args))
        cfg, db = _open(args)
        with db, db.transaction(immediate=True) as s:
            if s.scalar(select(StaffUser).where(StaffUser.username == username)):
                print(f"ERROR: the account {username!r} exists already", file=sys.stderr)
                return 1
            s.add(StaffUser(username=username, display_name=args.name.strip() or None,
                            password_hash=password_hash, is_active=True))  # fmt: skip
    except (ValueError, ConfigError, DatabaseError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Account {username!r} added.")
    return 0


def _change(args: argparse.Namespace, change) -> int:
    try:
        username = normalize_username(args.username)
        cfg, db = _open(args)
        with db, db.transaction(immediate=True) as s:
            user = s.scalar(select(StaffUser).where(StaffUser.username == username))
            if user is None:
                print(f"ERROR: there is no account {username!r}", file=sys.stderr)
                return 1
            change(user)
    except (ValueError, ConfigError, DatabaseError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


def run_user_password(args: argparse.Namespace) -> int:
    try:
        password_hash = hash_password(_read_password(args))
    except PasswordError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    def change(user: StaffUser) -> None:
        user.password_hash = password_hash

    code = _change(args, change)
    if code == 0:
        print("Password changed. The account's open sessions have ended.")
    return code


def run_user_active(args: argparse.Namespace) -> int:
    def change(user: StaffUser) -> None:
        user.is_active = args.active

    code = _change(args, change)
    if code == 0:
        print("Account enabled." if args.active else "Account disabled; its sessions have ended.")
    return code


def run_user_list(args: argparse.Namespace) -> int:
    try:
        cfg, db = _open(args)
        with db, db.transaction() as s:
            users = [
                (u.username, u.display_name or "", u.is_active, u.last_login_at)
                for u in s.scalars(select(StaffUser).order_by(StaffUser.username))
            ]
    except (ConfigError, DatabaseError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not users:
        print("No staff accounts. Add one with: reel user add <username>")
    for username, name, active, last in users:
        state = "active" if active else "disabled"
        seen = last.strftime("%Y-%m-%d %H:%M UTC") if last else "never"
        print(f"{username:<24} {state:<9} last login: {seen:<20} {name}")
    return 0


def run_cert(args: argparse.Namespace) -> int:
    try:
        cfg = ConfigProvider(find_config_file(args.config)).get()
        folder = tls.tls_folder(cfg)
        if args.show:
            info = tls.read(folder)
            if info is None:
                print(f"No certificate in {folder}. Make one with: reel cert")
                return 1
        else:
            names, addresses = tls.local_names(args.name), tls.local_addresses(args.ip)
            password = tls.key_password(make_secret_store(cfg), create=True)
            info = tls.create(folder, password, names, addresses, new_ca=args.new_ca)
    except (ConfigError, SecretStoreError, tls.TlsError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    web = cfg.settings.web
    print(f"Certificate: {folder / tls.SERVER_CERT} (valid until {info.expires:%Y-%m-%d})")
    print(f"Names:       {', '.join(info.names)}")
    print(f"Addresses:   {', '.join(info.addresses)}")
    state = "NEW: install it on every staff computer" if info.new_ca else "unchanged"
    print(f"CA:          {folder / tls.CA_CERT} ({state}; valid until {info.ca_expires:%Y-%m-%d})")
    print(f"CA thumbprint (SHA-1, as Windows shows it): {info.ca_thumbprint}")
    print(f"CA SHA-256: {info.ca_sha256}")
    if not web.tls:
        print()
        print("To use it, set in config/reel.toml under [web]:")
        print('  host = "0.0.0.0"')
        print("  tls = true")
        print(f'  base_url = "https://{info.names[0]}:{web.port}"')
        print("then restart Reel (see docs/setup/network.md).")
    return 0


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def run_serve(args: argparse.Namespace) -> int:
    """Reel in one process: dashboard, watcher, workers, staff notifier (T-901)."""
    import time

    from reel.queue.commands import stop_on_signals
    from reel.runtime import Parts, Runtime

    try:
        provider = ConfigProvider(find_config_file(args.config))
        cfg = provider.get()
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    configure_from_config(cfg, role="serve", console_level="DEBUG" if args.verbose else "INFO")
    parts = Parts(
        web=not args.no_web, watcher=not args.no_watcher, worker=not args.no_worker,
        notifier=not args.no_notifier, cleanup=not args.no_cleanup,
    )  # fmt: skip
    web = cfg.settings.web
    host, port = args.host or web.host, args.port or web.port
    try:
        db = open_database(cfg.paths.database_file)
        secret = session_secret(cfg) if parts.web else None
        options = tls.uvicorn_options(cfg) if parts.web and web.tls else {}
    except (ConfigError, DatabaseError, SecretStoreError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    with db:
        if parts.web:
            with db.transaction() as s:
                active = s.scalar(
                    select(func.count()).select_from(StaffUser).where(StaffUser.is_active.is_(True))
                )
            if not active:
                log.warning("there is no active staff account; add one with 'reel user add'")
            if not _loopback(host) and not web.base_url.startswith("https://"):
                log.warning(
                    "the dashboard listens on %s without HTTPS: put it behind an HTTPS proxy "
                    "and set web.base_url to its https:// address", host,
                )  # fmt: skip
        runtime = Runtime(
            db, provider.get, parts=parts, host=host, port=port, secret=secret, tls=options
        )
        stop = runtime.stopping
        with stop_on_signals(stop.set):
            runtime.start()
            if parts.web:
                scheme = "https" if options else "http"
                log.info("dashboard on %s://%s:%d; staff open %s", scheme, host, port, web.base_url)
            while not stop.is_set() and runtime.alive():
                time.sleep(0.25)  # short waits keep Ctrl+C responsive
            if not stop.is_set():
                log.error("a part of Reel stopped by itself (see the log above); stopping")
            runtime.stop()
    return 0
