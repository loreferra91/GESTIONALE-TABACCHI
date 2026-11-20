# Gestionale Tabacchi — backup MongoDB automatici su macOS

**Stato al 20/11/2026:** codice pronto sul branch, **non installato o attivato sul Mac dell'operatore**. Nessuna modifica a `main`, Render o Atlas da questa procedura. Il backup manuale del 20/11/2026 resta intatto.

## Obiettivo e limiti

- Backup di sola lettura del database di produzione `gestionale` sul cluster `Cluster0`.
- Pianificazione alle **11:00 ora locale macOS (Europe/Rome, se il Mac usa tale fuso)** tramite LaunchAgent dell'utente; `RunAtLoad` permette un tentativo quando l'utente effettua l'accesso. Gli eventi durante il sonno possono essere raggruppati al risveglio; il Mac **spento** non esegue backup.
- `mongodump --archive --gzip` → `gpg --encrypt` tramite pipeline, senza un archivio BSON in chiaro su disco; file `.partial` fino al completamento.
- Password letta da un elemento del Portachiavi **login** mediante `/usr/bin/security`, senza password nell'URI, negli argomenti CLI, nel repository o nei log. Viene creato solo durante l'esecuzione un file YAML credenziali in directory privata 0700, file 0600, rimosso in uscita. Come per ogni credenziale di backup, il Mac va protetto con FileVault e accesso utente protetto.
- Filtro per evitare un secondo backup se l'ultimo file è verificato e risale a meno di 12 ore. Lock per evitare due esecuzioni contemporanee.
- SHA-256 e verifica del manifest dopo ogni backup; facoltativamente, copia del **solo archivio cifrato** sulla USB `MAG256` con verifica di lettura. **Nessuna cancellazione automatica.**
- Gli snapshot logici a caldo non garantiscono consistenza temporale multi-collezione; un backup ripristinabile non prova un RPO/RTO formale.

## Prerequisiti (già confermati nell'esecuzione manuale)

- `mongodump` e `mongorestore` MongoDB Tools 100.16.1, GPG 2.5.24, Docker e Python 3.
- Accesso IP dal Mac ad Atlas `Cluster0`, utente `gestionale_backup_reader_prod` con ruolo `read` sul solo DB `gestionale` e scope `Cluster0`.
- Chiave **pubblica** GPG del destinatario con fingerprint `5270DA0DC9C353FC55DAA45EC32735EE65573B62` disponibile nel portachiavi GPG; chiave privata custodita in backup separato cifrato su USB.
- Repo GitHub su checkout di un **branch dedicato**, in directory che **non venga spostata** dopo l'installazione. Non copiare gli script sul branch di produzione per attivarli.

## 1. Creare l'elemento nel Portachiavi macOS

Aprire **Accesso Portachiavi**, selezionare il portachiavi `login` e creare un nuovo elemento password (menu File → Nuovo elemento password, se presente nella versione macOS):

| Campo | Valore |
| --- | --- |
| Nome elemento / servizio | `it.gestionale.tabacchi.mongodb.backup` |
| Account | `gestionale_backup_reader_prod` |
| Password | La password **già impostata** per l'utente Atlas; non rigenerarla |

Non inserire la password nei comandi `security add-generic-password -w PASSWORD`, nella shell history o in Git. Verificare in terminale senza stamparla:

```bash
security find-generic-password -a gestionale_backup_reader_prod \
  -s it.gestionale.tabacchi.mongodb.backup >/dev/null && echo 'KEYCHAIN ITEM: OK'
```

Alla prima esecuzione automatica, macOS potrebbe chiedere di autorizzare la lettura dell'elemento. Verificare l'identità del processo prima di consentirla. Se il Portachiavi è bloccato, il job deve fallire, senza degradare la sicurezza.

## Risoluzione errori durante il primo avvio

Se `git clone` segnala che `~/GESTIONALE-TABACCHI-BACKUP` esiste, non cancellarla. Esamina prima il checkout:

```bash
git -C "$HOME/GESTIONALE-TABACCHI-BACKUP" status --short
git -C "$HOME/GESTIONALE-TABACCHI-BACKUP" branch --show-current
```

Se il branch è `feature/macos-encrypted-backup-launchd-20261120` e la directory non contiene modifiche locali, aggiornare senza forzature:

```bash
git -C "$HOME/GESTIONALE-TABACCHI-BACKUP" pull --ff-only origin feature/macos-encrypted-backup-launchd-20261120
cd "$HOME/GESTIONALE-TABACCHI-BACKUP"
python3 scripts/macos_backup_runner.py --allow-production --diagnose
```

`--diagnose` è **in sola lettura**, non legge la password e non effettua connessioni Atlas. Restituisce soltanto presenza dell'elemento Portachiavi, della chiave pubblica GPG, strumenti disponibili e permessi delle directory. `keychain_item_found: false` indica elemento assente/non accessibile, non una password errata. `permissions_not_private` richiede ispezione dei permessi prima di modificarli. `not_created_yet` per cartelle temporanee è normale al primo avvio. Se `--force` continua a fallire, il codice aggiornato stampa una fase, ad esempio `ERROR [keychain_access]` o `ERROR [mongodump_gpg]`, senza stampare eccezioni potenzialmente sensibili. Il preflight originale non prova Keychain, autenticazione o comunicazione con Atlas.

Non pubblicare in issue, log o chat il risultato di `security ... -w`, il file YAML temporaneo, gli URI con password o screenshot del Portachiavi contenenti credenziali.

## 2. Configurare il LaunchAgent (non attiva la pianificazione finché non caricato)

Con il branch dedicato aperto nel terminale, eseguire:

```bash
python3 scripts/macos_backup_runner.py --allow-production --preflight
python3 scripts/install_macos_backup_schedule.py
python3 scripts/install_macos_backup_schedule.py --install
```

Il primo comando non usa Keychain né Atlas, il secondo è un'anteprima. `--install` crea in modo **esclusivo** senza sovrascritture:

`~/Library/LaunchAgents/it.gestionale.tabacchi.backup.plist`

Con l'impostazione predefinita il job conserva un backup **locale** anche senza USB collegata. Per rendere obbligatoria la seconda copia USB usare `--usb-volume /Volumes/MAG256` al momento dell'installazione. In quest'ultimo caso, se la USB è assente l'archivio locale può comunque essere creato, ma l'esecuzione è segnalata come fallita e la copia esterna **non** è dichiarata completata. Il volume è FAT32 e non può contenere singoli file da 4 GiB o più.

### 3. Test manuale reale (solo lettura Atlas)

```bash
python3 scripts/macos_backup_runner.py --allow-production --force
python3 scripts/backup_health.py --directory "$HOME/Gestionale-Backups" \
  --max-age-hours 2 --verify-hash
```

`--force` esegue un nuovo dump. Controllare che il comando termini con `local_verified` e `backup_fresh: true`. **Non** lanciare il comando su un Mac che non si considera attendibile.

### 4. Attivazione alle 11:00

```bash
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/it.gestionale.tabacchi.backup.plist"
launchctl print "gui/$(id -u)/it.gestionale.tabacchi.backup"
```

**Avvertenza:** `RunAtLoad` provoca un tentativo anche subito dopo il bootstrap. Il controllo delle ultime 12 ore evita backup ridondanti se il manifest precedente è sano.

Controllare i log locali (non contengono intenzionalmente credenziali):

```bash
tail -n 40 "$HOME/Library/Logs/GestionaleTabacchiBackup/backup.stdout.log"
tail -n 40 "$HOME/Library/Logs/GestionaleTabacchiBackup/backup.stderr.log"
```

In caso di errore viene inoltre tentata una notifica macOS generica. Il controllo di salute va controllato periodicamente; le notifiche macOS da sole **non garantiscono** consegna.

## Rollback senza perdita dati

```bash
launchctl bootout "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/it.gestionale.tabacchi.backup.plist"
```

Il comando disattiva il job, **senza cancellare** file di backup, manifest o password nel Portachiavi. Non rimuovere gli archivi prima di avere una seconda copia verificata.

## Da completare prima di chiamare il sistema definitivo

1. **Archivio offsite indipendente** su account cloud dedicato (es. B2/S3) con bucket privato, versioning, limiti di spesa e upload/download verificato. Nel repository esiste un lavoro sperimentale su `feature/staging-free-b2-backup-20261120`; non è stato unito o attivato in questo branch.
2. **Retention 30 giorni** soltanto dopo prove effettive della copia offsite e protezione da cancellazione/sovrascrittura; questa versione non elimina alcun backup.
3. **Restore drill mensile** su MongoDB isolato + confronto di tutte le collezioni, indici e invarianti contabili. Il ripristino manuale del 20/11/2026 ha già recuperato 139.800 documenti, 38 collezioni e zero errori, ma non sostituisce un test mensile automatico.
4. **Consistenza transazionale:** valutare un metodo di snapshot coerente e un RPO/RTO documentato. Free tier e `mongodump` a caldo non danno automaticamente PITR.
5. **Monitoraggio autonomo:** la notifica macOS non funziona se il Mac è spento. Per un backup sempre attivo occorre un host/scheduler affidabile esterno, con credenziali isolate e storage persistente offsite.

## Test

- `python3 -m pytest backend/tests/test_macos_backup_runner.py -q` — test del wrapper e dell'installer su dati fittizi, senza accesso Mac/Atlas.
- `python3 -m compileall scripts` — verifica sintattica.
- Esecuzione reale di `launchctl`, Keychain e `mongodump`: **NON VERIFICATA su questo Mac dalla sessione remota**. Seguire i passaggi sopra e registrare il risultato.

## Cambio orario e percorso locale (20 novembre 2026)

Nuova pianificazione: **11:00 ora locale**, destinazione **~/Backup-Gestionale-Tabacchi**.

Lo strumento scripts/reconfigure_macos_backup.py esegue una **copia conservativa**
degli archivi GPG e del manifest last-success.json. Verifica i byte con SHA-256,
non sovrascrive file differenti e non cancella la directory originale.
Salva il plist precedente e tenta il rollback se il bootstrap aggiornato fallisce.

### Installazione sul Mac (la chat non ha accesso al computer locale)

```bash
cd "$HOME/GESTIONALE-TABACCHI-BACKUP"
git status --short
git pull --ff-only origin feature/macos-encrypted-backup-launchd-20261120
python3 scripts/reconfigure_macos_backup.py
```

Dopo PREFLIGHT OK:

```bash
python3 scripts/reconfigure_macos_backup.py --apply
python3 scripts/backup_health.py --directory "$HOME/Backup-Gestionale-Tabacchi" --max-age-hours 24 --verify-hash
plutil -extract StartCalendarInterval json -o - "$HOME/Library/LaunchAgents/it.gestionale.tabacchi.backup.plist"
launchctl print "gui/$(id -u)/it.gestionale.tabacchi.backup"
```

Il risultato atteso include SCHEDULE: 11:00 local time e LAUNCHAGENT: loaded.
Verificare backup_fresh: true e hash_valid: true nella nuova directory.
La prima **esecuzione realmente pianificata** alle 11:00 resta da verificare.

### Backup manuale nel nuovo percorso

```bash
python3 scripts/macos_backup_runner.py --allow-production --output-dir "$HOME/Backup-Gestionale-Tabacchi" --force
```

**Rollback:** conservare la copia del precedente plist indicata dallo script.
Non eliminare i backup originali. Se il LaunchAgent è ancora running, la
migrazione si interrompe prima di disattivare il servizio.
