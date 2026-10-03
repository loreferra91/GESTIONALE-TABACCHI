#!/bin/zsh

set -euo pipefail

PROJECT_DIR="${0:A:h}"
PORT="${PORT:-18085}"
HOST="127.0.0.1"
URL="http://${HOST}:${PORT}"
FIXTURE_PATH="$PROJECT_DIR/backend/tests/fixtures/gods34.xlsm"
LOG_DIR="$PROJECT_DIR/logs"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

echo "Gestionale Tabacchi"
echo "Cartella: $PROJECT_DIR"
echo "Indirizzo: $URL"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "Errore: Python 3 non è installato."
  read -r "?Premi Invio per chiudere..."
  exit 1
fi

if ! command -v yarn >/dev/null 2>&1; then
  echo "Yarn non trovato: uso npm."
  PACKAGE_RUNNER="npm"
else
  PACKAGE_RUNNER="yarn"
fi

if ! python3 -c "import uvicorn, mongomock_motor, openpyxl" >/dev/null 2>&1; then
  echo "Installazione dipendenze backend locali..."
  python3 -m pip install --user -r "$PROJECT_DIR/backend/requirements.txt" "mongomock-motor==0.0.36" >>"$LOG_DIR/backend-install.log" 2>&1
fi

if [[ ! -d "$PROJECT_DIR/frontend/node_modules" ]]; then
  echo "Installazione dipendenze frontend..."
  if [[ "$PACKAGE_RUNNER" == "yarn" ]]; then
    (cd "$PROJECT_DIR/frontend" && yarn install --frozen-lockfile) >>"$LOG_DIR/frontend-install.log" 2>&1
  else
    (cd "$PROJECT_DIR/frontend" && npm install) >>"$LOG_DIR/frontend-install.log" 2>&1
  fi
fi

echo "Compilazione frontend locale..."
if [[ "$PACKAGE_RUNNER" == "yarn" ]]; then
  (cd "$PROJECT_DIR/frontend" && REACT_APP_BACKEND_URL="" yarn build) >>"$LOG_DIR/frontend-build.log" 2>&1
else
  (cd "$PROJECT_DIR/frontend" && REACT_APP_BACKEND_URL="" npm run build) >>"$LOG_DIR/frontend-build.log" 2>&1
fi

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "La porta $PORT è già occupata."
  echo "Chiudi il vecchio gestionale oppure avvia con un'altra porta:"
  echo "PORT=18081 \"$PROJECT_DIR/AVVIA_GESTIONALE.command\""
  read -r "?Premi Invio per chiudere..."
  exit 1
fi

echo "Avvio server locale..."
python3 -m uvicorn backend.local_test_app:app --host "$HOST" --port "$PORT" >"$LOG_DIR/server.log" 2>&1 &
SERVER_PID=$!

cleanup() {
  if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "Attendo il backend..."
for _ in {1..80}; do
  if curl -fsS "$URL/api/" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    echo "Errore: il server si è chiuso durante l'avvio."
    echo "Log: $LOG_DIR/server.log"
    tail -n 60 "$LOG_DIR/server.log" 2>/dev/null || true
    read -r "?Premi Invio per chiudere..."
    exit 1
  fi
  sleep 0.5
done

if ! curl -fsS "$URL/api/" >/dev/null 2>&1; then
  echo "Errore: il server non risponde."
  echo "Log: $LOG_DIR/server.log"
  tail -n 60 "$LOG_DIR/server.log" 2>/dev/null || true
  read -r "?Premi Invio per chiudere..."
  exit 1
fi

if [[ -f "$FIXTURE_PATH" ]]; then
  echo "Carico dati Excel di prova..."
  curl -fsS --max-time 240 \
    -F "file=@${FIXTURE_PATH};type=application/vnd.ms-excel.sheet.macroEnabled.12" \
    "$URL/api/import/excel-full" >"$LOG_DIR/import.json" || {
      echo "Attenzione: import Excel non riuscito. Log import: $LOG_DIR/import.json"
    }
fi

echo
echo "Gestionale pronto: $URL"
echo "Log server: $LOG_DIR/server.log"
echo "Per fermarlo, chiudi questa finestra o premi Control-C."
if [[ "${NO_OPEN:-0}" != "1" ]]; then
  open "$URL"
fi

wait "$SERVER_PID"
