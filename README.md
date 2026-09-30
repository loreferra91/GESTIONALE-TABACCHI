# Gestionale Tabacchi

Gestionale full stack per prodotti, magazzino, vending, vendite, ordini e cassa.

- **Frontend:** React, installato e compilato con Yarn 1.22.22
- **API:** FastAPI + Uvicorn
- **Database:** MongoDB / MongoDB Atlas
- **Hosting:** un unico Web Service Docker su Render

In produzione FastAPI serve sia le API sotto `/api` sia la build React. In questo
modo il frontend e il backend condividono lo stesso dominio e basta un solo
servizio gratuito Render.

## Flusso Emergent → produzione

Emergent è l'ambiente di sviluppo, GitHub è la fonte ufficiale del codice e
Render pubblica soltanto il branch `main`:

```text
Emergent → emergent/develop → verifica e test → main → Render
```

- Le modifiche create in Emergent vanno prima salvate su `emergent/develop`.
- `main` deve contenere solo versioni compilate e verificate.
- Render segue `main` e avvia automaticamente il deploy dopo ogni push.
- Non eseguire un reset o un pull forzato nel workspace Emergent quando contiene
  file non committati: prima creare un commit sul branch di sviluppo.

Per evitare che prove e importazioni modifichino i dati reali, usare due database
distinti anche quando condividono lo stesso cluster Atlas:

- Emergent: `DB_NAME=gestionale_dev`
- Render: `DB_NAME=gestionale`

## Avvio locale

Requisiti: Node.js 20, Yarn 1.22, Python 3.12 e un database MongoDB raggiungibile.

### Backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-prod.txt
cp backend/.env.example backend/.env
uvicorn backend.server:app --reload --port 8000
```

Prima di avviare, modificare `backend/.env` con una `MONGO_URL` valida. Il file
`.env` è escluso da Git.

### Frontend

In un secondo terminale:

```bash
cd frontend
cp .env.example .env
printf 'REACT_APP_BACKEND_URL=http://localhost:8000\n' > .env
yarn install --frozen-lockfile
yarn start
```

Il frontend locale sarà disponibile su `http://localhost:3000`.

## Semantica magazzino e ordini

`giacenza_negozio` indica lo stock fisico libero in negozio/magazzino, cioe i
pezzi realmente disponibili per vendita o ricarica. Nei file Excel la colonna
`RIMANENZE` e invece aggregata: durante l'import viene convertita una sola volta
in stock fisico libero sottraendo giacenza vending e venduto vending. Da quel
momento Auto-Order usa direttamente `giacenza_negozio` e non sottrae di nuovo la
vending; una ricarica vending `-negozio/+vending` riduce quindi lo stock
Auto-Order esattamente una volta.

Stock importati o movimentati in modo incoerente, per esempio con
`giacenza_negozio` negativa, sono trattati come `ANOMALIA`: non generano quantita
da ordinare, non entrano in righe Auto-Order, PDF, totale o conferma, anche se le
vendite recenti sarebbero sufficienti.

La conferma Auto-Order crea solo un batch ordine fornitore idempotente
(`ordini_fornitore` e `ordini_fornitore_righe`) usando la chiave inviata dal
client o lo snapshot corrente. Non incrementa `giacenza_negozio`, non incrementa
`acquistati` e non scrive in `storico_ordini`. Il carico merce resta
`POST /api/ordini/bulk`: usare quell'endpoint quando la merce arriva fisicamente;
quello e il percorso che registra lo storico di ricezione e aumenta scorte e
acquistati.

Le finestre Auto-Order sono configurabili con `AUTO_ORDER_FINESTRA_BREVE_GG` e
`AUTO_ORDER_FINESTRA_LUNGA_GG`. Le API espongono i campi generici
`venduto_breve` e `venduto_lungo`; `venduto_10gg` e `venduto_30gg` restano
presenti per compatibilita.

Il ciclo di vita FastAPI usa un `lifespan` asincrono: allo startup inizializza
seed, parametri e indici; allo shutdown chiude il client Mongo. La migrazione da
`on_event` è coperta dalla suite locale e da un avvio Uvicorn isolato.

## Pubblicazione gratuita

### 1. Creare MongoDB Atlas Free

1. Accedere a [MongoDB Atlas](https://www.mongodb.com/atlas/database) e creare
   un cluster **Free / M0**.
2. In **Database Access**, creare un utente dedicato all'applicazione.
3. In **Network Access**, autorizzare `0.0.0.0/0`. Il servizio Render gratuito
   non ha un IP di uscita statico. Usare credenziali robuste perché questa regola
   permette la connessione da qualsiasi IP, pur lasciando il database protetto
   dall'autenticazione Atlas.
4. In **Connect > Drivers**, copiare la stringa Python, ad esempio:

   ```text
   mongodb+srv://NOME_UTENTE:PASSWORD@cluster0.example.mongodb.net/?retryWrites=true&w=majority
   ```

   Se la password contiene caratteri speciali, usare la versione già codificata
   fornita da Atlas oppure applicare la codifica URL.

### 2. Creare il servizio su Render

Il repository include [`render.yaml`](./render.yaml) e un `Dockerfile` pronto.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/loreferra91/GESTIONALE-TABACCHI)

Durante la creazione del Blueprint, impostare i valori richiesti:

| Variabile | Valore |
| --- | --- |
| `MONGO_URL` | stringa di connessione Atlas completa |
| `APP_USERNAME` | nome utente per proteggere il gestionale |
| `APP_PASSWORD` | password lunga e casuale |

`DB_NAME=gestionale` e `CORS_ORIGINS=*` sono già configurate. I segreti non
devono essere inseriti nel repository.

Render eseguirà automaticamente queste operazioni:

1. costruzione del frontend con `yarn install --frozen-lockfile` e `yarn build`;
2. installazione delle dipendenze Python di produzione;
3. avvio di Uvicorn sulla porta assegnata da Render;
4. controllo salute su `/api/`.

Ogni push sul branch collegato genera un nuovo deploy automatico.

## Variabili d'ambiente

| Nome | Obbligatoria | Descrizione |
| --- | --- | --- |
| `MONGO_URL` | sì | URI MongoDB locale o Atlas |
| `DB_NAME` | sì | nome del database; default Render: `gestionale` |
| `CORS_ORIGINS` | no | origini separate da virgola; in produzione l'app usa lo stesso dominio |
| `APP_USERNAME` | consigliata | abilita Basic Auth se presente insieme alla password |
| `APP_PASSWORD` | consigliata | password Basic Auth; non salvarla in Git |
| `PORT` | su Render | assegnata automaticamente dalla piattaforma |

Se `APP_USERNAME` o `APP_PASSWORD` mancano, l'applicazione resta pubblicamente
accessibile. L'endpoint di health check `/api/` resta intenzionalmente libero.

## Limiti del piano gratuito

- Render sospende il Web Service dopo 15 minuti senza traffico; il primo accesso
  successivo può richiedere circa un minuto.
- Il filesystem del container è effimero. I dati persistenti sono quindi sempre
  conservati in MongoDB Atlas, non nel container.
- Atlas può sospendere un cluster Free rimasto senza connessioni per 30 giorni.
- I piani gratuiti sono indicati per test, demo e uso personale leggero, non per
  un servizio commerciale con requisiti di disponibilità.

Riferimenti: [Render Free](https://render.com/docs/free),
[Render Docker](https://render.com/docs/docker),
[MongoDB Atlas Free](https://www.mongodb.com/docs/atlas/tutorial/deploy-free-tier-cluster/).

## Verifica prima del deploy

Test locali principali:

```bash
pytest backend/tests -q
python3 -m compileall backend
cd frontend && yarn build
```

I test esterni che mutano un backend live sono marcati `external` e saltano in
modo pulito senza URL. Per eseguirli, usare un backend isolato:

```bash
TEST_BACKEND_URL=http://localhost:8000 pytest backend/tests -m external -q
```

Smoke fixture import locale:

```bash
TEST_BACKEND_URL=http://localhost:8000 pytest backend/tests/test_import_excel_full.py backend/tests/test_iter8_features.py -m external -q
```

```bash
cd frontend
yarn install --frozen-lockfile
yarn build

cd ..
docker build -t gestionale-tabacchi .
docker run --rm -p 10000:10000 \
  -e MONGO_URL='mongodb://host.docker.internal:27017' \
  -e DB_NAME='gestionale' \
  gestionale-tabacchi
```

Aprire `http://localhost:10000` e verificare anche
`http://localhost:10000/api/`.
