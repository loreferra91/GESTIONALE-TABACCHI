# Staging Atlas RBAC — stato effettivo e revoca legacy (20 novembre 2026)

## Stato verificato con accesso Atlas in sola lettura

- Progetto `Gestionale Staging`; cluster `gestionale-staging`;
  database `gestionale_staging`.
- Servizio Render `gestionale-tabacchi-staging` sul branch
  `openclaw/render-staging-deploy`, **distinto** da `main`.
- Credenziale nuova `gestionale_staging_scoped_20261119`:
  esclusivamente `readWrite` sul database `gestionale_staging`,
  scope cluster `gestionale-staging`.
- Credenziale legacy `gestionale_staging_app`:
  ancora esistente, `readWriteAnyDatabase` su `admin`.
  Il rischio dell'utenza legacy resta fino alla revoca.
- La webapp staging ha già superato il controllo in modalità
  `STAGING_MONGO_ROLE_MODE=active`; il vecchio
  `MONGO_URL` è conservato soltanto per rollback.
- La produzione è un altro cluster e **non è oggetto della revoca**.

## Controllo corretto del runtime

Il vecchio script `scripts/staging_rbac_check.py` interrogava
sempre `MONGO_URL`, quindi in modalità `active` verificava
**l'utente sbagliato** e poteva segnalare un falso errore.
Ora `select_effective_runtime_uri()` usa gli stessi selettori
della webapp staging e rifiuta `legacy`, `probe`, produzione
e URI con hostname diverso dal cluster dedicato.

Da un contesto amministrativo con variabili Render protette:

```bash
python -m scripts.staging_rbac_check
```

Esito necessario: `least_privilege: true` con un solo ruolo
`readWrite` su `gestionale_staging`. L'output non contiene URI,
password o nomi di account autenticati. Questo comando è
**read-only**, non revoca utenti.

## Procedura di revoca della vecchia utenza (non eseguita)

**Stop se non esiste un recupero indipendente e provato**:
il fallback `STAGING_MONGO_ROLE_MODE=legacy` diventerebbe
inutilizzabile dopo la revoca.

1. Verificare il login Render e accesso amministratore Atlas; un
   operatore autorizzato conserva una procedura di recupero e un
   backup **off-cluster**, cifrato, con chiave privata disponibile
   e test di restore isolato.
2. Confermare `STAGING_MONGO_ROLE_MODE=active` in Render staging,
   il servizio Live, i log `Scoped staging credential check
   passed`, e l'assenza di `RESTORING` / `FAILED`.
3. Confermare che la nuova credenziale funziona **senza leggere**
   `MONGO_URL`, con `scripts.staging_rbac_check` e
   health `GET /api/`.
4. Nel solo progetto Atlas staging: **Security → Database Access**,
   selezionare l'utente `gestionale_staging_app` e revocarlo.
   **Non toccare** `gestionale_staging_scoped_20261119`;
   non modificare gli utenti del cluster di produzione.
5. Verificare nuovamente app e API, operazioni di lettura, anteprima
   backup, riconciliazione ed esito del ruolo attivo. Per prudenza,
   non modificare prodotti, vendite e cassa soltanto per
   provare l'autenticazione.
6. Una volta completato il piano di recovery senza legacy,
   rimuovere dal servizio Render la variabile `MONGO_URL`
   obsoleta con una procedura di modifica env che preserva
   tutte le altre variabili, poi documentare la nuova via di
   rollback. Il codice richiede ancora il nome di questa
   variabile: **non rimuoverla senza modifica applicativa
   specifica testata**.
7. Registrare orario, account revocato, screenshot non sensibili,
   test eseguiti ed esito. Non inserire credenziali in GitHub.

## Limitazioni degli strumenti collegati

Il connettore Atlas disponibile in questa sessione permette di
**elencare e creare** utenti ma **non revocarli**. Il connettore
Render permette di modificare variabili ma non recuperarne il
valore precedente: una rotazione cieca potrebbe interrompere il
servizio. Una revoca non deve essere spacciata per eseguita
se non c'è conferma Atlas.

**Stato: attivazione least-privilege PASS; revoca utente legacy
PENDENTE; recovery indipendente PENDENTE.** I due ultimi punti
non si risolvono aggiungendo test sintetici o manipolando dati.
