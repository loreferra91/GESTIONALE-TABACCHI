#!/bin/zsh

set -e

PROJECT_DIR="${0:A:h}"
PORT="${PORT:-18080}"
URL="http://127.0.0.1:${PORT}/auto-order"
FIXTURE_PATH="$PROJECT_DIR/backend/tests/fixtures/gods34.xlsm"

cd "$PROJECT_DIR"

import_local_data() {
  if [[ ! -f "$FIXTURE_PATH" ]]; then
    echo "Attenzione: fixture Excel non trovata: $FIXTURE_PATH"
    echo "Il gestionale partirà, ma Auto-Order potrebbe non mostrare ordini."
    return 0
  fi

  echo "Caricamento dati locali da gods34.xlsm..."
  if ! curl -fsS --max-time 240 \
    -F "file=@${FIXTURE_PATH};type=application/vnd.ms-excel.sheet.macroEnabled.12" \
    "http://127.0.0.1:${PORT}/api/import/excel-full" >/tmp/gestionale-import-local.json; then
    echo "Errore: import dei dati locali fallito."
    echo "Dettaglio eventuale:"
    cat /tmp/gestionale-import-local.json 2>/dev/null || true
    return 1
  fi

  python3 - <<'PY'
import json
from pathlib import Path

path = Path("/tmp/gestionale-import-local.json")
data = json.loads(path.read_text())
totali = data.get("totali", {})
print(
    "Dati caricati: "
    f"{totali.get('prodotti_inseriti', 0) + totali.get('prodotti_aggiornati', 0)} prodotti, "
    f"{totali.get('db_storico_vend_righe', 0)} vendite storiche, "
    f"{totali.get('vending_inseriti', 0) + totali.get('vending_aggiornati', 0)} colonne vending."
)
PY
}

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Il gestionale è già attivo su $URL"
  import_local_data
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
  (cd frontend && REACT_APP_BACKEND_URL="" yarn build)
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
    import_local_data
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
