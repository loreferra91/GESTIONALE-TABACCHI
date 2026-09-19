# Gestionale Tabacchi

Gestionale full stack per prodotti, magazzino, vending, vendite, ordini e cassa.

- **Frontend:** React, installato e compilato con Yarn 1.22.22
- **API:** FastAPI + Uvicorn
- **Database:** MongoDB / MongoDB Atlas
- **Hosting:** un unico Web Service Docker su Render

In produzione FastAPI serve sia le API sotto `/api` sia la build React. In questo
modo il frontend e il backend condividono lo stesso dominio e basta un solo
servizio gratuito Render.

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
