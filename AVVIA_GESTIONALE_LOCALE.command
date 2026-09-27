#!/bin/zsh

set -e

PROJECT_DIR="${0:A:h}"
PORT=18080
URL="http://127.0.0.1:${PORT}/auto-order"

cd "$PROJECT_DIR"

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Il gestionale è già attivo su $URL"
  if [[ "${NO_OPEN:-0}" != "1" ]]; then
    open "$URL"
  fi
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Errore: Python 3 non è installato."
  read -r "?Premi Invio per chiudere..."
  exit 1
fi

if ! command -v yarn >/dev/null 2>&1; then
  echo "Errore: Yarn non è installato."
  read -r "?Premi Invio per chiudere..."
  exit 1
fi

if ! python3 -c "import mongomock_motor" >/dev/null 2>&1; then
  echo "Installazione del database locale temporaneo..."
  python3 -m pip install --user "mongomock-motor==0.0.36"
fi

if [[ ! -d frontend/node_modules ]]; then
  echo "Installazione delle dipendenze frontend..."
  (cd frontend && yarn install --frozen-lockfile)
fi

BUILD_REQUIRED=0
if [[ ! -f frontend/build/index.html ]]; then
  BUILD_REQUIRED=1
elif find frontend/src frontend/public -type f -newer frontend/build/index.html -print -quit | grep -q .; then
  BUILD_REQUIRED=1
fi

if [[ "$BUILD_REQUIRED" == "1" ]]; then
  echo "Compilazione del frontend..."
  (cd frontend && yarn build)
fi

echo "Avvio del gestionale locale isolato..."
echo "Indirizzo: $URL"
echo "I dati sono temporanei e vengono eliminati alla chiusura."
echo "Per arrestare il server premi Control-C."

python3 -m uvicorn backend.local_test_app:app --host 127.0.0.1 --port "$PORT" &
SERVER_PID=$!

cleanup() {
  if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

for _ in {1..60}; do
  if curl -fsS "http://127.0.0.1:${PORT}/api/" >/dev/null 2>&1; then
    if [[ "${NO_OPEN:-0}" != "1" ]]; then
      open "$URL"
    fi
    wait "$SERVER_PID"
    exit $?
  fi
  sleep 0.5
done

echo "Errore: il server non si è avviato entro 30 secondi."
exit 1
