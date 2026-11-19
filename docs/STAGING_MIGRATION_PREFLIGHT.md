# Staging — preflight migrazioni all'avvio (sola lettura)

## Contesto

`backend/server.py:on_start()` esegue ancora attività che scrivono
all'avvio, tra cui seed/parametri, indici, assegnazione ID ai prodotti
legacy, riconciliazione alias ADM e cache vending e rinomina delle
colonne H1/H2/H3.

**Questa modifica non elimina né modifica quelle scritture.**
Aggiunge un diagnostico separato per rilevare rischi prima di
progettare migrazioni versionate, approvate ed eseguibili una volta sola.

## Uso

Su una macchina autorizzata, con rete e credenziali **di sola lettura**
verso il cluster staging, impostare le variabili tramite il secret manager,
senza inserirle in shell history o in file del repository.

```bash
ENVIRONMENT=staging DB_NAME=gestionale_staging \
  python -m backend.migration_preflight
```

La variabile `MONGO_URL` deve essere già disponibile nell'ambiente e
puntare al cluster `gestionale-staging.*.mongodb.net`; lo script
rifiuta `production`, `gestionale` e qualunque host non riconosciuto.

L'output JSON include:
- `business_indexes`: indici univoci già disponibili, assenti o bloccati
  da duplicati per prodotti, colonne vending e parametri;
- `vending_column_zero_padding`: rinomine H01/H02/H03 pendenti e conflitti;
- `migration_records`: presenza delle tre migrazioni versionate note;
- `unversioned_startup_writes_require_review`: operazioni mutanti che
  richiedono analisi distinta prima di disattivarle/estrarle;
- `safe_to_run_automatic_migration: false`: il preflight **non
  autorizza mai** migrazioni automatiche.

## Limiti

- Nessuna lettura dello stato corrente è una fotografia
  transazionale: le anomalie possono cambiare subito dopo il check.
- Il controllo degli indici non verifica tutte le query, né i
  costi/performance dell'aggregazione su volumi reali.
- La presenza di un record `app_migrations` non certifica
  che la migrazione sia riuscita o che il dataset sia integro.
- I valori duplicati riportati vanno gestiti come dati operativi:
  non copiarli in issue o log pubblici se sensibili.
- Non eseguire correzioni massive, `drop_index` o restore per
  "far diventare verde" il preflight.
- Test CI su Mongo simulato; esecuzione reale verso Atlas staging
  **NON VERIFICATA** finché non esiste un risultato verificato da
  un operatore autorizzato.
