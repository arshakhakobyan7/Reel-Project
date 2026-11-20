"""``reel auth``: connect Reel to Google once, see the connection, remove it (T-701)."""

import argparse
import sys
import webbrowser

from reel.auth.google import (
    SCOPES,
    SERVICE_SCOPES,
    AuthError,
    OAuthClient,
    authorize,
    connection,
    forget,
)
from reel.auth.google import make_google_tokens as _tokens
from reel.auth.store import SecretStoreError, make_secret_store
from reel.config import ConfigError, load_config
from reel.config.commands import find_config_file


def register(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("auth", help="connect Reel to Google (YouTube, Classroom)")
    actions = parser.add_subparsers(dest="auth_command", metavar="<action>", required=True)
    google = actions.add_parser("google", help="sign in once in the browser and keep the token")
    google.add_argument("--no-browser", action="store_true", help="only print the address to open")
    google.add_argument("--port", type=int, default=0, help="local port for the answer (0: any)")
    google.add_argument(
        "--only",
        choices=sorted(SERVICE_SCOPES),
        help="ask for this service's access only (an account without Classroom: --only youtube)",
    )
    google.set_defaults(handler=run_google)
    status = actions.add_parser("status", help="show whether Reel is connected (no secrets)")
    status.add_argument("--check", action="store_true", help="also get a fresh access token")
    status.set_defaults(handler=run_status)
    remove = actions.add_parser("forget", help="remove the stored Google token")
    remove.set_defaults(handler=run_forget)


def _config(args: argparse.Namespace):
    return load_config(find_config_file(args.config))


def run_google(args: argparse.Namespace) -> int:
    try:
        cfg = _config(args)
        path = cfg.secrets.get("GOOGLE_CLIENT_SECRETS_FILE")
        if not path:
            raise AuthError(
                "GOOGLE_CLIENT_SECRETS_FILE is not set (.env); see docs/setup/google.md",
                reason="not_configured",
            )
        client = OAuthClient.from_file(cfg.resolve(path))
        store = make_secret_store(cfg)

        def open_url(url: str) -> None:
            print("Open this address, sign in with the channel's Google account and allow access:")
            print(url, flush=True)
            if not args.no_browser:
                webbrowser.open(url)

        scopes = SERVICE_SCOPES[args.only] if args.only else SCOPES
        result = authorize(client, store, open_url=open_url, scopes=scopes, port=args.port)
    except (ConfigError, AuthError, SecretStoreError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Connected. Token kept in: {store.name} store. Scopes: {len(result['scopes'])}.")
    if result["missing_scopes"]:
        print("WARNING: not granted: " + ", ".join(result["missing_scopes"]), file=sys.stderr)
        return 1
    return 0


def run_status(args: argparse.Namespace) -> int:
    try:
        cfg = _config(args)
        store = make_secret_store(cfg)
        info = connection(store)
        if info is None:
            print(f"Not connected to Google ({store.name} store). Run 'reel auth google'.")
            return 1
        print(f"Connected since {info.get('connected_at')} ({store.name} store), "
              f"{len(info.get('scopes', []))} scopes.")  # fmt: skip
        if args.check:
            _tokens(cfg, store).token()
            print("Access token: OK (refreshed just now).")
    except (ConfigError, AuthError, SecretStoreError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


def run_forget(args: argparse.Namespace) -> int:
    try:
        store = make_secret_store(_config(args))
        removed = forget(store)
    except (ConfigError, SecretStoreError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("Google token removed." if removed else "No Google token was stored.")
    print("To revoke access completely: Google Account → Security → Third-party connections.")
    return 0
