# Staging — certificazione del trasporto offsite S3 (20 novembre 2026)

## Prova eseguita

Il workflow `.github/workflows/staging-offsite-s3-disposable-cert.yml`
ha avviato un server S3 HTTP temporaneo **Moto** solo su loopback
nel runner GitHub, con credenziali fittizie. Nessuna connessione ad
AWS reale, MongoDB Atlas, Render o al database di produzione.

- CI specifica [44713598499](https://github.com/loreferra91/GESTIONALE-TABACCHI/actions/runs/44713598499): **2/2 test superati**.
- CI generale [44713598711](https://github.com/loreferra91/GESTIONALE-TABACCHI/actions/runs/44713598711): **verde**.

### Certificazioni del protocollo

1. `upload_encrypted_backup` usa davvero l'AWS CLI verso un endpoint
   S3 HTTP, carica un oggetto, lo scarica e ne confronta byte e SHA-256.
2. Verifica che l'oggetto remoto esponga metadata
   `ServerSideEncryption: AES256` e che `last-success.json` esista
   soltanto dopo lettura e verifica dell'archivio.
3. Corrompendo l'oggetto remoto prima del download, la funzione
   blocca la procedura e **non pubblica `last-success.json`**.
4. Nessun URL MongoDB, credenziale reale, database operativo o
   chiave GPG privata viene utilizzata in questa prova.

### Limiti: cosa NON è ancora verificato

- L'oggetto sintetico di questo test **non è** un dump MongoDB e
  **non è realmente cifrato GPG**. La cifratura GPG, il
  `mongodump` e il `mongorestore` sono testati **separatamente**
  sul dataset sintetico di 25.108 documenti tramite
  `staging-encrypted-backup-real-restore.yml`.
- Moto emula l'API S3; non dimostra IAM, Object Lock, versioning,
  retention, permanenza, costi e disponibilità su AWS reale.
- L'applicazione in produzione non genera un backup offsite ricorrente.
  Lo storage staging resta **NON CERTIFICATO** per disaster recovery
  finché non esiste un backup indipendente, cifrato, recuperabile e
  ripristinato con successo su un cluster isolato.
- Un `mongodump --db` su un database attivo non certifica un unico
  punto transazionale multi-collezione; non interpretare il manifest
  come snapshot consistente.

## Criteri di attivazione successivi

Bucket esterno privato, IAM limitata al prefisso, versioning e retention,
public key GPG con fingerprint verificato, private key recuperabile
indipendentemente, runner esterno al filesystem Render, job pianificato
e monitorato, ripristino periodico su target isolato e RPO/RTO approvati.
Il file `docs/STAGING_INDEPENDENT_S3_BACKUP.md` contiene il runbook.

**Staging-only.** Nessun deploy, change di environment o modifica
delle collezioni Atlas è richiesto per questa certificazione.
