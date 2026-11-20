# Collaudo manuale backup JSON su staging — 20 novembre 2026

**Ambiente:** https://gestionale-tabacchi-staging.onrender.com/  
**Database:** `gestionale_staging` sul cluster Atlas `gestionale-staging`, distinto da produzione.

## 0. Controllo preliminare — solo lettura (già eseguito)

- La collezione `backup_restore_control` è in fase `READY`.
- Nessun lock `backup-restore` risulta attivo.
- Sono presenti 232 prodotti, 84 colonne vending, 5.007 vendite e
  92 batch import: numeri di confronto, **non** baseline fiscale.
- Un checkpoint `pre-restore-file` verificato da metadata risulta
  `READY` (25.560 documenti). Non è un backup indipendente.
- Il nuovo pannello, una volta distribuito, può aggiornare
  `GET /api/backup/restore-file/status` senza modificare i dati.

## 1. Prima prova dell'operatore — solo anteprima

1. In staging aprire **Parametri di sistema → Backup e ripristino**.
2. Premere **Verifica stato ripristino**. Deve essere `READY`, senza
   avviso `RESTORING`, `FAILED` o `UNKNOWN`.
3. Prendere un vero file **.json** scaricato dalla webapp, di dimensione
   <= 20 MiB. Non scegliere `.archive.gz.gpg`, `.zip` o `.xlsm`.
4. Selezionarlo sotto **Ripristina da file**. Il server fa la *preview*
   **senza scritture**, mostra conteggi per collezione e avvisi.
5. **Non confermare ancora**. Registrare dimensione del file,
   `total_docs`, eventuali collezioni mancanti, avvisi e presenza
   della schermata di conferma. Non caricare il file originale
   in issue pubbliche o nei log.
6. Se il file è >20 MiB, la webapp deve fermarsi prima di inviarlo.
   In caso di errore di validazione, nessun backup di sicurezza
   aggiuntivo deve essere creato e le scorte devono restare invariate.

## 2. Prove di errore sicure — SOLO PREVIEW

Sono disponibili nel kit scaricabile separato file
deliberatamente invalidi. Caricare uno alla volta:

| File | Risultato atteso |
|---|---|
| `01_TEST_JSON_non_valido.json` | HTTP 422, file non leggibile |
| `02_TEST_checksum_errato.json` | HTTP 422, backup incompleto o checksum non valido |
| `03_TEST_collezione_sconosciuta.json` | HTTP 422, struttura/collezione non valida |
| `04_TEST_oltre_20_MiB.json` | Blocco locale per limite 20 MiB, nessun POST |

In nessuno di questi casi deve comparire una conferma
abilitata per un restore distruttivo.

## 3. Test positivo COMPLETO: solo dopo backup indipendente

**Non eseguire solo per vedere cosa succede.** Il restore sostituisce
tutte le collezioni presenti nel file e può rimuovere modifiche
staging effettuate dopo la data del backup.

Prima di fare clic su `Ripristina dal file`:

- scaricare un **backup completo attuale** con `Backup e scarica`,
  conservarlo fuori da Atlas e verificarne l'apertura;
- sospendere ogni vendita, ricarica vending, import, ordine e altra
  operazione di scrittura sullo staging;
- verificare file e checksum, eventuali nuove collezioni e data;
- verificare che i documenti attuali possano essere deliberatamente
  sostituiti e che ci sia spazio sufficiente per il checkpoint;
- registrare conteggi, giacenze, importi, batch e audit **prima**.

Se tutte le condizioni sono soddisfatte, confermare una volta
`Ripristina dal file` e attendere l'esito. **Non premere di nuovo**
dopo timeout, disconnessione o HTTP 503.

### Reazione attesa

- **Successo:** il server risponde `ok: true`, `restored_docs`,
  `pre_restore_backup`, `restore_strategy:
  staging_checkpoint_compensation`; ritorna in fase `READY`.
  Verificare campioni di stock, colonne vending, vendite, batch,
  cassa, audit e ordini confrontando con il backup.
- **422 / 413 / 409 preflight:** nessuna collezione sostituita.
  Esaminare la causa, non aggirare il controllo.
- **503 con rollback verificato:** il server segnala che ha
  recuperato e verificato il checkpoint. Confrontare i dati
  precedenti prima di decidere qualsiasi nuovo tentativo.
- **`PREPARING`, `RESTORING`, `ROLLING_BACK` o `FAILED`:**
  staging rimane in manutenzione; le API operative sono bloccate.
  Consultare `GET /api/backup/restore-file/status`, i log e il
  checkpoint. Non ripetere l'import.
- **Esito HTTP/rete incerto:** interrogare lo stato e confrontare
  i dati; il client non deve avviare un secondo ripristino automatico.

La recovery è possibile solo con richiesta esplicita
`POST /api/backup/restore-file/recover` e
`{"confirm":"RECOVER_STAGING_CHECKPOINT"}`, una volta verificato
l'ambiente e il checkpoint. Il recupero stesso sovrascrive lo stato
parziale. Non usarlo su un processo ancora attivo.

## 4. Verifiche di compatibilità e limiti

- Un vecchio JSON senza collezioni opzionali già popolate nella
  destinazione deve produrre `can_restore=false` e blocco 409
  alla conferma: non unire ledgers incompleti.
- Il checkpoint interno al medesimo database **non è un
  backup off-cluster**; nessun RPO/RTO è certificato da questa prova.
- Lo staging usa batch da 500, con verifica completa dei contenuti,
  ma non una transazione atomica cross-collection: per questo è
  necessario mettere in pausa le altre attività.
- **Non eseguire mai la prova su https://gestionale-tabacchi.onrender.com**
  o sul database `gestionale`.

**Punto di uscita del collaudo:** conservare screenshot della preview,
messaggi esatti e una checklist dei conteggi. Una prova non autorizzata
di restore positivo non è richiesta per verificare la preview.
