#!/usr/bin/env bash
# Prepare an Ubuntu 24.04 VM for Reel (T-1018, D-070). Run it as root from the project folder
# that was copied to /opt/reel:
#
#   sudo bash /opt/reel/deploy/gcp/setup.sh <host name>     e.g. 34-90-12-7.sslip.io
#
# It installs free software only (D-009): FFmpeg and Caddy from Ubuntu, uv and Python 3.14,
# Reel in a virtual environment, the Linux build of the same Ollama release as on Windows, and
# the models. It writes the systemd service and the Caddy site, then starts both.
# Running it again only checks and repairs; it never replaces data/, .env or the token store.
set -euo pipefail

HOST="${1:?usage: setup.sh <host name, e.g. 34-90-12-7.sslip.io>}"
APP=/opt/reel
OLLAMA_VERSION=v0.40.16   # the build pinned in scripts/setup_ollama.py
WHISPER_MODEL=large-v3-turbo
LLM_MODEL=gemma3:4b

cd "$APP"
echo "== packages"
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q ffmpeg caddy curl ca-certificates zstd

echo "== the reel account (owns the project; no login shell)"
id reel >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin reel
mkdir -p "$APP/data" "$APP/logs" "$APP/models" "$APP/tools"
chown -R reel:reel "$APP"
chmod 750 "$APP"

echo "== uv and Python 3.14"
if ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh \
        | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi
as_reel() { sudo -u reel -H env HOME="$APP" UV_PYTHON_INSTALL_DIR="$APP/.python" \
    UV_CACHE_DIR="$APP/.cache/uv" "$@"; }
as_reel uv venv --quiet --allow-existing --python 3.14 "$APP/.venv"
as_reel uv pip install --quiet --python "$APP/.venv/bin/python" -e "$APP[dev]"

echo "== FFmpeg where the tests look for it (tools/ffmpeg/bin/*.exe -> the system build)"
as_reel mkdir -p "$APP/tools/ffmpeg/bin"
as_reel ln -sf /usr/bin/ffmpeg "$APP/tools/ffmpeg/bin/ffmpeg.exe"
as_reel ln -sf /usr/bin/ffprobe "$APP/tools/ffmpeg/bin/ffprobe.exe"

echo "== Ollama $OLLAMA_VERSION (Linux build, checked against the release's SHA-256)"
if [ ! -x "$APP/tools/ollama/bin/ollama" ]; then
    base="https://github.com/ollama/ollama/releases/download/$OLLAMA_VERSION"
    work=$(mktemp -d)
    curl -fsSL "$base/sha256sum.txt" -o "$work/sha256sum.txt"
    asset=""
    for name in ollama-linux-amd64.tar.zst ollama-linux-amd64.tgz; do
        if grep -qE "[ *](\./)?$name\$" "$work/sha256sum.txt"; then asset=$name; break; fi
    done
    [ -n "$asset" ] || { echo "no Linux amd64 asset in $OLLAMA_VERSION" >&2; exit 1; }
    curl -fL --progress-bar "$base/$asset" -o "$work/$asset"
    expected=$(grep -E "[ *](\./)?$asset\$" "$work/sha256sum.txt" | awk '{print $1}')
    actual=$(sha256sum "$work/$asset" | awk '{print $1}')
    [ "$expected" = "$actual" ] || { echo "SHA-256 mismatch for $asset" >&2; exit 1; }
    echo "SHA-256 ok: $asset"
    as_reel mkdir -p "$APP/tools/ollama"
    case "$asset" in
        *.tar.zst) tar --zstd -xf "$work/$asset" -C "$APP/tools/ollama" ;;
        *.tgz) tar -xzf "$work/$asset" -C "$APP/tools/ollama" ;;
    esac
    chown -R reel:reel "$APP/tools/ollama"
    rm -rf "$work"
fi

echo "== models: Whisper $WHISPER_MODEL (+ tiny for the tests), the person detector, $LLM_MODEL"
as_reel "$APP/.venv/bin/python" "$APP/scripts/setup_whisper.py" --model "$WHISPER_MODEL"
as_reel "$APP/.venv/bin/python" "$APP/scripts/setup_whisper.py" --model tiny
as_reel "$APP/.venv/bin/python" "$APP/scripts/setup_person_model.py"
# Reel appends to logs/ollama.log as the reel user whenever it starts Ollama. The ">>" below is
# opened by this root shell, so give the file to reel first: a root-owned log makes every LLM
# stage fail with PermissionError. This also repairs the log of an earlier install.
touch "$APP/logs/ollama.log"
chown reel:reel "$APP/logs/ollama.log"
chmod 600 "$APP/logs/ollama.log"
if ! as_reel env OLLAMA_MODELS="$APP/models/ollama" OLLAMA_HOST=127.0.0.1:11434 \
        "$APP/tools/ollama/bin/ollama" list 2>/dev/null | grep -q "^$LLM_MODEL"; then
    as_reel mkdir -p "$APP/data/ollama-home"
    as_reel env OLLAMA_MODELS="$APP/models/ollama" OLLAMA_HOST=127.0.0.1:11434 \
        HOME="$APP/data/ollama-home" "$APP/tools/ollama/bin/ollama" serve \
        >>"$APP/logs/ollama.log" 2>&1 &
    server=$!
    for _ in $(seq 60); do curl -fs http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
    as_reel env OLLAMA_MODELS="$APP/models/ollama" OLLAMA_HOST=127.0.0.1:11434 \
        HOME="$APP/data/ollama-home" "$APP/tools/ollama/bin/ollama" pull "$LLM_MODEL"
    kill "$server"; wait "$server" 2>/dev/null || true
fi

echo "== .env (secrets and the server's settings; created once, owner-only)"
if [ ! -f "$APP/.env" ]; then
    secret=$("$APP/.venv/bin/python" -c "import secrets; print(secrets.token_urlsafe(48))")
    sed -e "s|@SESSION_SECRET@|$secret|" -e "s|@HOST@|$HOST|" \
        "$APP/deploy/gcp/server.env.template" > "$APP/.env"
    unset secret
fi
chown reel:reel "$APP/.env"
chmod 600 "$APP/.env"
[ -f "$APP/config/google-client.json" ] && chown reel:reel "$APP/config/google-client.json" \
    && chmod 600 "$APP/config/google-client.json"

echo "== initial configuration for a clean demo"
for name in reel groups; do
    if [ ! -f "$APP/config/$name.toml" ]; then
        as_reel cp "$APP/config/$name.example.toml" "$APP/config/$name.toml"
    fi
done
as_reel mkdir -p "$APP/inbox"

echo "== checks"
as_reel "$APP/.venv/bin/reel" db status >/dev/null 2>&1 || as_reel "$APP/.venv/bin/reel" db init
as_reel "$APP/.venv/bin/reel" config check || true

echo "== services: reel (127.0.0.1:8080) and Caddy (HTTPS for $HOST)"
install -m 644 "$APP/deploy/gcp/reel.service" /etc/systemd/system/reel.service
sed "s|@HOST@|$HOST|" "$APP/deploy/gcp/Caddyfile.template" > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now reel
systemctl reload-or-restart caddy
systemctl is-active --quiet reel caddy
curl --retry 12 --retry-delay 2 --retry-connrefused -fsS -o /dev/null \
    -w "local dashboard: %{http_code}\n" http://127.0.0.1:8080/login
echo "done: https://$HOST"
