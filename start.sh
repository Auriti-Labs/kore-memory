#!/usr/bin/env sh
# Kore — start server in background
# Usage: ./start.sh

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

PID_FILE="$DIR/logs/kore.pid"
mkdir -p "$DIR/logs"

# Controlla se il server risponde davvero (non solo se il PID esiste)
if curl -sf http://127.0.0.1:8765/health >/dev/null 2>&1; then
    echo "Kore already running and healthy"
    exit 0
fi

# PID esiste ma server non risponde → processo zombie, kill forzato
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    kill -9 "$OLD_PID" 2>/dev/null
    rm -f "$PID_FILE"
fi

# Libera la porta 8765 nel caso ci fosse qualcos'altro
fuser -k 8765/tcp 2>/dev/null || true
sleep 1

# Defaults: usa il DB nella cartella data/ del progetto
export KORE_DB_PATH="${KORE_DB_PATH:-$DIR/data/memory.db}"

# Load .env and start server in one command (preserves env vars in nohup)
if [ -f "$DIR/.env" ]; then
    # shellcheck disable=SC2046
    nohup env $(grep -v '^#' "$DIR/.env" | xargs) \
        "$DIR/.venv/bin/kore" \
        --host 127.0.0.1 \
        --port 8765 \
        --log-level warning \
        > "$DIR/logs/server.log" 2>&1 &
else
    nohup "$DIR/.venv/bin/kore" \
        --host 127.0.0.1 \
        --port 8765 \
        --log-level warning \
        > "$DIR/logs/server.log" 2>&1 &
fi

echo $! > "$PID_FILE"
echo "Kore started (pid $!)"
