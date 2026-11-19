# Runbook — backup MongoDB indipendente e cifrato

**Stato:** componente preparato, NON attivo in produzione.  
**Riferimento:** `scripts/encrypted_backup.py` e `scripts/backup_health.py`.

## Prerequisiti

- Host dedicato o supporto **persistente esterno** a MongoDB Atlas e Render.
- Eseguibili `mongodump`, `mongorestore`, `gpg` installati e testati.
- Utente MongoDB separato con privilegi minimi necessari alla lettura.
- File YAML `mongodump --config` contenente le credenziali, custodito
  fuori dal repository con permessi 0600.
- Chiave pubblica GPG del destinatario importata e fingerprint completo
  (40/64 caratteri esadecimali) verificato fuori banda.
- Directory backup con permessi 0700 e spazio libero monitorato.
- Retention, frequenza e destinazione concordate **prima** dell'attivazione.
- Operazioni produttive autorizzate esplicitamente.

La connessione MongoDB resta nel file di configurazione e **non** viene
passata come parametro di processo: il classico `--uri` con password
potrebbe comparire in strumenti come `ps`. Non salvare credenziali in
script, Git, issue, log o CI.

## 1. Preparazione su database usa-e-getta

Creare il file di configurazione fuori dalla checkout e renderlo leggibile
soltanto dall'utente di backup.

```bash
umask 077
mkdir -p "$HOME/gestionale-offsite-test"
chmod 700 "$HOME/gestionale-offsite-test"
# Creare manualmente un YAML mongodump valido in un percorso privato.
# Usare credenziali di test: non importare il segreto Atlas in CI.
```

Eseguire con il **fingerprint realmente verificato**:

```bash
python3 scripts/encrypted_backup.py \
  --db-name gestionale_dev \
  --mongodump-config /private/path/mongodump.yaml \
  --output-dir /persistent/offsite/gestionale \
  --recipient YOUR_VERIFIED_FULL_GPG_FINGERPRINT
```

Il programma genera un file `.archive.gz.gpg` e un manifest
`last-success.json` con dimensione, hash SHA-256 e data UTC.
Il successo prova l'avvenuta cifratura, **non** il recupero del DB.

La produzione `gestionale` richiede il flag esplicito
`--allow-production`, oltre all'autorizzazione organizzativa:
il flag da solo non costituisce autorizzazione al deploy.

## 2. Verifica periodica

```bash
python3 scripts/backup_health.py \
  --directory /persistent/offsite/gestionale \
  --max-age-hours 30 \
  --verify-hash
```

Codice di uscita `0` se il backup cifrato è presente, recente e hash
corrispondente; `2` in caso contrario. La soglia è un esempio, non un
RPO approvato. Integrare successivamente con un canale alert gestito.

## 3. Restore drill (esclusivamente su DB isolato)

Prima del test:

1. Preparare un ambiente MongoDB separato, con credenziali dedicate.
2. Verificare che la configurazione restore punti al database usa-e-getta.
3. Effettuare un confronto dei namespace e un inventario delle collezioni.
4. Decrittare e trasmettere il flusso al tool di restore, senza scrivere
   temporaneamente un archivio MongoDB in chiaro.
5. Controllare codici di uscita **di entrambi** i processi (usare
   `set -o pipefail` nelle shell compatibili).
6. Verificare conteggi, indici univoci, batch, baseline e movimentazioni.
7. Registrare la verifica con firma, ora e risultato esternamente al
   semplice manifest di backup.

Esempio da adattare **solo sul target di laboratorio** dopo aver verificato
la versione dei MongoDB Database Tools:

```bash
set -o pipefail
gpg --batch --decrypt /persistent/offsite/gestionale/ARCHIVE.gpg |
  mongorestore --config /private/path/restore-test.yaml \
    --archive --gzip \
    --nsFrom='gestionale_dev.*' \
    --nsTo='gestionale_restore_test.*'
```

Non eseguire questo esempio verso produzione. Nessun restore produttivo
è autorizzato dal presente runbook.

## 4. Rischi e limitazioni

- `mongodump` su un database attivo **non assicura da solo** uno snapshot
  transazionale di tutte le collezioni.
- I backup cifrati non sono utili senza una copia privata della chiave
  GPG recuperabile e custodita separatamente.
- Uno snapshot sul filesystem effimero Render non è un backup indipendente.
- Gli snapshot interni al medesimo cluster non sostituiscono il backup.
- Il manifest non è una prova di restore riuscito.
- Per una garanzia RPO/RTO, selezionare una strategia Atlas/PITR o una
  procedura coordinata, testarla e approvarne costi e retention.
- La cancellazione automatica dei vecchi backup non è stata attivata;
  la retention va progettata in modo da non perdere l'ultima copia sana.
- I test unitari usano anche un flusso di **byte sintetici**.
  La pipeline `.github/workflows/staging-encrypted-backup-real-restore.yml`
  esercita invece un vero `mongodump` cifrato GPG e un `mongorestore`
  su un MongoDB replica set usa-e-getta. Verifica 5 collezioni, dati e indici
  con **7 documenti sintetici**, senza credenziali Atlas.
- Questa certificazione prova il percorso tecnico, **non** un recupero
  completo dei dati operativi, una procedura PITR, un RPO/RTO approvato
  o la persistenza esterna dei backup reali. Non attiva job periodici.

## Riferimenti documentali

- [MongoDB Database Tools — mongodump](https://www.mongodb.com/docs/database-tools/mongodump/)
- [MongoDB Database Tools — mongorestore](https://www.mongodb.com/docs/database-tools/mongorestore/)
