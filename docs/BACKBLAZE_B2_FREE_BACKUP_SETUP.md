# GESTIONALE TABACCHI — backup cloud a 0 € con Backblaze B2

**Stato (20 novembre 2026): codice adattato e pronto alle prove CI;
account B2, bucket, chiave S3, chiave GPG e restore da B2 reale NON
ancora configurati né verificati. Non confondere questo documento con
un backup di produzione già attivo.**

## Perché non AWS Free Plan

Per nuovi account AWS il piano gratuito scade entro 6 mesi o al
consumo dei crediti. Backblaze B2 offre invece un livello gratuito
permanente **entro i limiti**: primi 10 GB di storage, quote gratuite
per alcune transazioni. Gli eventuali superamenti e altre risorse
possono comportare costi. Non associare un metodo di pagamento finché
non necessario, abilitare i **Caps & Alerts** e monitorare l'utilizzo
periodicamente. Questo runner usa un tetto locale prudenziale di
**8 GiB sul singolo bucket**, inclusi oggetti versionati. Non è
un limite amministrativo dell'account, non include eventuali altri
bucket né upload multipart sospesi. L'account deve essere dedicato
ai backup di questo progetto.

Fonti:
- https://www.backblaze.com/cloud-storage/pricing
- https://www.backblaze.com/docs/cloud-storage-data-caps-and-alerts
- https://www.backblaze.com/docs/cloud-storage-lifecycle-rules
- https://www.backblaze.com/docs/cloud-storage-server-side-encryption-with-the-cli

## 1. Creare account e bucket (operatore)

1. Registrarsi dal sito ufficiale:
   https://www.backblaze.com/sign-up/s3
2. Selezionare la **regione EU Central** durante l'attivazione
   dell'account, se disponibile. Non confondere questa scelta con
   la regione del cluster MongoDB Atlas; leggere l'endpoint
   effettivamente assegnato da B2.
3. Scegliere il livello gratuito, saltare il metodo di pagamento
   se il sito lo consente. Non attivare piani Business, Personal
   Backup o servizi di replica aggiuntivi.
4. **B2 Cloud Storage → Buckets → Create a Bucket**:
   - Nome globally unique, ad es. `gestionale-tabacchi-recovery-UNIQUE`.
   - Accesso **Private**, non pubblico.
   - Cifratura SSE-B2 (AES-256) attiva/di default.
   - Nessuna replica automatica o object lock senza una policy verificata.
   - Conservare l'endpoint reale `https://s3.<REGIONE>.backblazeb2.com`.
5. **Caps & Alerts**: limitare le categorie storage/transaction/download
   agli importi minimi concessi dal pannello (0 $/giorno dove supportato),
   abilitare email di avviso, verificare il contatore globale
   dei GB di storage e delle transazioni. Non fare affidamento solo
   sul controllo di budget dello script.
6. Non abilitare lifecycle distruttivi prima del primo backup e
   restore verificati. Le versioni B2, anche quelle nascoste,
   consumano storage. In seguito configurare **manualmente** una
   retention di 14 giorni sul prefisso backup; non cancellare il
   solo ultimo archivio utilizzabile. Nessuna cancellazione
   remota è eseguita automaticamente dal nostro codice.

## 2. Chiave applicativa B2 con privilegi minimi

In **Application Keys → Add a New Application Key**, scegliere
il bucket creato e, se applicabile, il prefisso
`gestionale/staging` (e una chiave separata per
`gestionale/production`).

Per il job di backup sono necessarie le capacità:
`listBuckets` (o funzionalità equivalente per il bucket specifico),
`listFiles`, `readFiles`, `writeFiles`.
La chiave **non** deve avere `deleteFiles`, autorizzazioni su altri
bucket o capacità di modificare policy e utenti.
Il programma usa `ListObjectVersions` per il budget di spazio,
`PutObject` e `GetObject`; questi privilegi vanno verificati
con una prova reale S3.

**Segreti:** Key ID e Application Key devono essere immessi
direttamente nel profilo AWS CLI protetto del Mac, mai in
GitHub, Render, chat, screenshot o repository.

## 3. Mac come runner senza canone

Questo approccio usa un Mac già posseduto come runner. Non richiede
un server a pagamento. **Il Mac deve essere acceso e in grado di
eseguire i job pianificati.** Un notebook spento/scollegato dalla rete
non garantisce un backup giornaliero.

Installare MongoDB Database Tools, GnuPG e AWS CLI tramite Homebrew:

```bash
brew tap mongodb/brew
brew install mongodb-database-tools gnupg awscli
```

Verificare `mongodump --version`, `gpg --version`,
`aws --version` e `python3 --version`.

Da Backblaze, salvare la coppia **Key ID/Application Key** nel
profilo AWS CLI locale:

```bash
umask 077
aws configure --profile gestionale-b2
```

- `AWS Access Key ID`: Key ID B2.
- `AWS Secret Access Key`: Application Key B2.
- Region: quella indicata dall'endpoint B2 (es. `eu-central-003`,
  **non** assumere che sia questa).
- Output: `json`.

Impostare i permessi di `~/.aws/credentials` a `0600`.
Non riportare il suo contenuto in chat.

## 4. Lettura MongoDB con utente dedicato

Nel progetto **Atlas staging**, creare un nuovo utente con il solo
ruolo di lettura necessario a `mongodump` sul database
`gestionale_staging` (minimo `read` se sufficiente).
L'utente applicativo in esecuzione e il vecchio account legacy non
devono essere riutilizzati per il backup.

Per produzione creare separatamente un utente `read` su
`gestionale`; questo richiede prima l'approvazione della
strategia di recupero produzione. Non effettuare test mutanti sui
database reali.

Nel Mac creare `~/.config/gestionale-tabacchi/mongodump-staging.yaml`
con permessi `0600` e configurazione ufficiale del Database Tools:

```yaml
uri: "mongodb+srv://BACKUP_READER:URL_ENCODED_SECRET@REAL_STAGING_ATLAS_CLUSTER/gestionale_staging?authSource=admin"
```

Sostituire i segnaposto **solo sul Mac**; non usare la URI di
produzione per lo staging. Abilitare il proprio IP pubblico
nella Network Access List di Atlas (non `0.0.0.0/0`) e
verificare l'autenticazione in lettura.

## 5. Chiave GPG di recupero

Generare sul Mac una coppia GPG per backup, con passphrase forte:

```bash
gpg --full-generate-key
gpg --list-keys --with-fingerprint
```

Usare il fingerprint **completo** di 40 caratteri, non il key ID
abbreviato. Il processo usa la chiave **pubblica** per cifrare
prima del trasferimento HTTPS. Il materiale GPG privato e la
passphrase devono essere esportati e custoditi **fuori dal Mac e
fuori da Backblaze**, in una copia offline sicura e verificata.
Senza chiave privata non è possibile ripristinare il contenuto.

Non pubblicare il fingerprint insieme a informazioni riservate se
non necessario; non caricare la private key su GitHub o Render.

## 6. Configurazione locale (staging prima)

Creare, fuori dal repository:

`~/.config/gestionale-tabacchi/b2-backup.env`

Esempio di contenuto, da personalizzare sul Mac:

```bash
B2_BUCKET='gestionale-tabacchi-recovery-UNIQUE'
B2_ENDPOINT='https://s3.YOUR-REGION.backblazeb2.com'
B2_GPG_FINGERPRINT='FULL_40_HEX_CHARACTER_GPG_FINGERPRINT'
B2_MONGODUMP_CONFIG='/Users/YOU/.config/gestionale-tabacchi/mongodump-staging.yaml'
B2_DB_NAME='gestionale_staging'
B2_PREFIX='gestionale/staging'
B2_AWS_PROFILE='gestionale-b2'
```

Applicare:
```bash
chmod 600 ~/.config/gestionale-tabacchi/b2-backup.env
chmod 600 ~/.config/gestionale-tabacchi/mongodump-staging.yaml
chmod 600 ~/.aws/credentials
```

Nessun segreto Backblaze deve finire nel file `b2-backup.env`.

## 7. Primo upload manuale e verifica

Dal checkout GitHub aggiornato sul branch di staging:

```bash
bash scripts/run_local_b2_backup.sh
```

Il programma deve:
1. Creare lo stream `mongodump --archive --gzip`, cifrato GPG
   **senza scrivere il dump in chiaro**.
2. Leggere le versioni del bucket B2 e rifiutare upload quando
   supererebbe **8 GiB** di byte versionati dichiarati.
3. Caricare solo `.archive.gz.gpg` e verificarlo con download
   e SHA-256/size.
4. Pubblicare `last-success.json` solo se il file remoto
   è stato riletto e verificato; il manifest dichiara
   `restore_certified: false` finché il restore non è testato.
5. Non modificare MongoDB Atlas, vendite, scorte, cassa, indici
   né migrazioni.

**Preflight storage:** l'inventario considera tutte le versioni
elencate dal bucket; se le credenziali non possono leggere
l'inventario o il budget è esaurito il backup FALLISCE. Verificare
sempre le quote dell'intero account nel pannello B2:
il check locale non è un limite contrattuale di spesa.

Per la **produzione** usare una configurazione
`GESTIONALE_BACKUP_ENV` separata e
`B2_DB_NAME=gestionale`, dopo il primo ripristino staging.
Il runner richiede il consenso esplicito
`B2_PRODUCTION_ENABLED=I_HAVE_VERIFIED_PRODUCTION_BACKUP`.

## 8. Pianificazione gratuita sul Mac (solo dopo il restore)

```bash
python3 scripts/install_macos_b2_backup_schedule.py
python3 scripts/install_macos_b2_backup_schedule.py --install --hour 2 --minute 30
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/it.gestionale.tabacchi.backup.plist
```

Il setup è volontario: il primo comando valida e basta,
il secondo scrive un LaunchAgent personale con orario
**locale Europe/Rome**. Il job non parte al momento
dell'installazione; la sua esecuzione dipende dal Mac
acceso, dalla rete, dalle credenziali e dalle impostazioni
di risparmio energetico. La CI non può dimostrare la
schedulazione su un Mac che non controlla.

**Attenzione:** una sola pianificazione per ambiente
per computer, evitare backup staging+produzione simultanei.
Il runner usa un lock `mkdir` per evitare doppio avvio;
non elimina lock rimasti dopo un'interruzione brusca.

## 9. Certificazione del ripristino e alert

Scaricare la copia cifrata **da Backblaze**, non da Atlas.
Con la chiave privata di recupero verificata decrittare solo
in un ambiente protetto e ripristinare con `mongorestore`
in un database **completamente isolato**. Controllare documenti,
indici, vendite, stock negozio/vending, ordini, audit e hash
dell'archivio, quindi distruggere la copia di laboratorio.
Non restaurare sul database corrente per provare il backup.

**Doppio avviso:** se `last-success.json` non avanza per
oltre 36 ore, o la quota B2 supera 80%, occorre un alert
operativo, non limitarsi a un job pianificato senza supervisione.
Un controllo realizzato manualmente su B2 e un promemoria
sono accettabili nella prima fase; un controllo automatico
sarebbe più affidabile una volta disponibili accessi sicuri.

## Cosa questa guida NON attiva

Account B2, bucket, application key, Mongo read-only user,
chiave privata recuperabile, pianificazione sul Mac e restore
B2 reale **sono ancora da eseguire**; nessun costo né servizio
aggiuntivo è stato attivato automaticamente.
