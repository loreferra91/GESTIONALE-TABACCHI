# Staging — migrazioni esplicite, versionate e con gate

## Stato e regola

`STAGING_STARTUP_MODE=read_only` è attivo su Render staging: durante
l'avvio non esegue seed, migrazioni, riparazioni di giacenze o aliases.
Il runner `backend/staging_migration_runner.py` tiene **separati**
i comandi di ispezione dai comandi mutanti e non utilizza mai
la vecchia credenziale Atlas ad accesso globale.

## Inventario del vecchio on_start()

L'avvio legacy conteneva otto operazioni non versionate,
che possono cambiare dati senza un piano di recovery indipendente:

1. `seed_if_empty`
2. `insert_missing_default_parameters`
3. `ensure_product_ids`
4. `reconcile_adm_product_aliases`
5. `restore_canonical_product_snapshots`
6. `reconcile_all_vending_stock`
7. `reconcile_vending_cash_state`
8. `repair_smart_venue_auto_order_2026_10_12`

Inoltre richiamava `migrate_unique_business_indexes`,
`migrate_auto_order_2_schema` e
`migrate_vending_column_zero_padding`. La presenza di un
migration marker **non basta** a certificare l'idempotenza e
l'assenza di effetti collaterali su ogni dataset.

## Modalità di sola lettura

Con `ENVIRONMENT=staging`, `DB_NAME=gestionale_staging`
e `STAGING_SCOPED_MONGO_URL` configurati in un ambiente
sicuro di amministrazione:

```bash
python3 -m backend.staging_migration_runner --plan
```

Il piano non muta documenti né indici. Rifiuta un restore
esterno in corso o nello stato FAILED. Registra le operazioni
legacy ancora vietate automaticamente.

## Applicazione eccezionale e reversibilità

È abilitato in questa fase **un solo** tipo di modifica:
gli indici additivi `auto-order-2-schema-v1`, già presenti
nel database staging verificato. Quando il marker è registrato,
un eventuale nuovo `--apply` non produce scritture.

Un nuovo `--apply` richiede **tutti** i seguenti:
- un backup **esterno e cifrato** dello stesso database;
- ripristino su un database diverso, con controlli di
  quantità, indici e documenti operativi;
- un'attestazione JSON verificata dall'operatore con i
  campi `source_database`, `source_kind`,
  `restore_target`, `independent_offcluster_archive`,
  `restore_verified`, `indexes_verified`,
  `business_documents_verified`, `archive_sha256`;
- comando esplicito:

```bash
python3 -m backend.staging_migration_runner \
  --apply auto-order-2-schema-v1 \
  --confirm APPLY_STAGING_MIGRATION \
  --restore-attestation /secure/private/staging-restore-attestation.json
```

**L'attestazione è una dichiarazione dell'operatore, non una
verifica crittografica automatica dell'archivio**. Il backup deve
essere effettivamente verificato fuori da questo programma. Il
runner evita sia la URI privilegiata legacy sia i log contenenti
segreti.

## Operazioni sempre bloccate

Migrazioni dati vending, riconciliazioni automatiche di giacenze,
snapshot ADM, modifiche Smart Venue, seed e sostituzione di
indici legacy **non** sono state convertite in job mutanti
autonomi. Servono migrazioni per-record con test specifici,
backup indipendente, journal, idempotenza, approvazione,
dry-run e rollback verificato prima di essere autorizzate.

Non copiare l'intero staging su produzione: il branch staging
contiene ripristino sperimentale e strumenti di amministrazione
non destinati all'esercizio operativo.
