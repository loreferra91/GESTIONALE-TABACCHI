# Staging — ripristino da backup JSON esterno

**Stato: preparato su branch dedicato; nessun deploy o ripristino su Atlas autorizzato/effettuato da questo documento.**

## Diagnosi verificata sul codice

- `openclaw/render-staging-deploy` (`03ad52b`) ha 32 collezioni nel formato JSON.
- `main` (`2062610`, 19/11/2026) ne ha 35.
- Un backup JSON esportato dal nuovo `main` contiene anche
  `cassa_rapida_batches`, `vending_operations`, `accounting_baselines`.
  La versione precedente di staging le rifiuta con HTTP 422.
- Il file `render.staging.example.yaml` è **solo un esempio**:
  non dimostra che un servizio staging sia configurato su Render.
- La funzione web accetta file `.json` fino a **20 MiB** (limite
  `backend/server.py:MAX_UPLOAD_BYTES`); non accetta archivi
  `mongodump` o `.archive.gz.gpg`.

## Modifiche predisposte

- `POST /api/backup/restore-file/preview`: valida il JSON, la versione,
  le collezioni, i conteggi e gli eventuali checksum, senza mutare
  i dati. Restituisce conteggi, avvisi e `can_restore`.
- La webapp invia il file prima all'anteprima, mostra numero di documenti
  ed eventuali avvisi e chiede conferma soltanto se il ripristino è ammesso.
- Un backup legacy che omette collezioni opzionali già popolate sulla
  destinazione viene bloccato con HTTP 409, per non generare registri
  operativi misti. Se le collezioni omesse sono vuote, il file resta
  compatibile con la versione attuale.
- `POST /api/backup/restore-file` **ricontrolla** il contenuto e
  la compatibilità della destinazione sotto restore lock prima
  del backup pre-ripristino e di qualsiasi sostituzione.
- Backup con checksum assenti restano importabili ma vengono esplicitamente
  segnalati come non integralmente verificati.

## Gate obbligatori prima di distribuire su staging

1. Identificare il vero **servizio Render staging** e il commit distribuito;
   non assumere che il branch o il Blueprint di esempio lo rappresentino.
2. Confermare nella configurazione staging `ENVIRONMENT=staging`,
   `DB_NAME=gestionale_staging` (o altro DB test esplicitamente
   identificato), `MONGO_URL` verso la risorsa di test, credenziali
   indipendenti e `CORS_ORIGINS` del servizio test. **Non pubblicare**
   i valori segreti nei report.
3. Verificare l'isolamento effettivo di MongoDB: database e privilegi
   dell'utenza non devono consentire scritture al DB operativo
   `gestionale`. Controllare anche Render auto-deploy prima di muovere
   qualunque ref utilizzato da quel servizio.
4. Allineare il codice staging alle tre collezioni del backup recente;
   preferire la base corrente di `main`, senza cambiare `main`.
5. Eseguire CI e un restore drill su DB usa-e-getta `gestionale_cert*`
   usando **dati sintetici o una copia sanificata**, non dati
   produttivi per le prime prove. Controllare conteggi, indici,
   giacenze negozio/vending, vendite, idempotenza, ordini e audit.
6. Solo dopo verifiche isolate, eseguire la preview del backup reale,
   conservare un backup indipendente e ottenere autorizzazione esplicita
   per sovrascrivere qualsiasi collezione della destinazione.

## Diagnostica dell'import web

| Esito | Causa |
| --- | --- |
| 422 "collezioni sconosciute" | Il backend staging è più vecchio del formato esportato |
| 422 "checksum non valido" | File alterato/incompleto o serializzazione incongruente |
| 422 "counts/checksums" | Metadati malformati |
| 409 "collezioni già popolate" | Backup legacy incompleto rispetto ai ledger presenti |
| 413 "File troppo grande" | JSON sopra 20 MiB |
| 409 "Un altro ripristino" | Lock di restore già attivo |
| Errore di rete/timeout | Esito incerto: verificare i dati prima di riprovare |

**Nota:** la preview non garantisce spazio Atlas sufficiente, tempo
transazionale sufficiente o completamento del restore. Il backup
pre-ripristino interno a MongoDB non sostituisce una copia esterna.
Non utilizzare il ripristino web per `.archive.gz.gpg`: per questi
file seguire `docs/ENCRYPTED_BACKUP_RUNBOOK.md` su un cluster isolato.

## Render staging service discovered (19 November 2026)

- Service ID: `srv-dbh081k9v7es73bh2i0g`, URL:
  `https://gestionale-tabacchi-staging.onrender.com`
- Production branch is `main`; staging branch is `openclaw/render-staging-deploy`.
- Before release staging ran `03ad52b`, 41 commits behind the then-current
  production commit `2062610`.
- Environment screenshot confirms `DB_NAME=gestionale_staging`,
  `ENVIRONMENT=staging`, and explicit staging CORS origin.
- Staging's connection uses a dedicated `gestionale-staging.*.mongodb.net`
  hostname. The runtime now also refuses staging startup if the
  database name or cluster hostname differs. **This guard does not
  establish Atlas database-user privileges**. Verify Atlas permissions
  independently and restrict the service user to its staging database.
- The staging Render service has Auto-Deploy enabled for its selected
  branch: merging PR #15 into that branch triggers a deployment.
- A screenshot exposed application and Atlas credentials. Rotate both
  secrets in their respective providers, then update the Render staging
  environment. Do not copy those secrets to GitHub or into an issue.

## Release validation

1. Confirm latest branch CI and disposable MongoDB restore workflow both pass.
2. Verify `main` SHA is unchanged before merging into **staging only**.
3. Merge PR #15 into `openclaw/render-staging-deploy`; observe the
   automatic Render deploy. If startup is refused, inspect the
   redacted `Staging bloccato` log message and correct configuration,
   **not** the validation code.
4. Call `GET /api/` to verify application health and Mongo reachability.
5. Sign in with rotated staging-only credentials and open Parametri.
6. Preview only a sanitized test backup, then manually confirm a restore
   into staging after preserving an independent backup.
7. Record observed documents/stock/vending/audit checks and only then
   propose promoting the tested *individual changes* into production.
