# Gate 4A — encrypted recovery drill (isolated laboratory only)

This commit implements a **bounded, encrypted logical snapshot and test restore**, not a production-grade desktop backup system. It cannot be used against the real `gestionale_desktop` database.

## Capabilities
- AES-256-GCM authenticated encryption, scrypt key derivation (N=32768, r=8, p=1), random salt and nonce per archive. Wrong passphrase and corrupted ciphertext are rejected **before any target database write**.
- A bounded 32 MiB BSON logical snapshot, generated through a MongoDB snapshot-read transaction, preserves document types across collections. A manifest stores counts and SHA-256 of the raw records.
- The output is created exclusively (no overwrite) and flushed to disk. No passwords are written to logs.
- Restore is allowed only into a never-populated `desktop_restore_cert_*` database and validates both collection counts and full logical content hash after insert.
- The source must be `desktop_backup_cert_*` with an explicit `sync_metadata` marker `{_id: "backup-laboratory", value: "disposable-only"}`. Both operations require a writable local replica set named **exactly** `rsDesktopCert`. No Atlas URI, main database or Render is used.

## Limitations and blockers
- **Not compatible with real desktop production data.** No desktop scheduler, recovery UI, lifecycle coordination, automatic external copy, tested Windows/macOS storage integration, MongoDB authentication, index/TTL/collection metadata restore, or oplog integration.
- It is a logical *document* drill. Restored MongoDB secondary indexes and collection options are not yet reconstructed. The restore is intentionally into an isolated namespace; the production application must NEVER point to it directly.
- Input is capped at 32 MiB and buffers the snapshot in memory. A future production tool must support large streaming backups, retained external encrypted copies, atomic quiescence or oplog-consistent backups, actual authentication, and tested disaster recovery.
- Restore target collections are created before the insertion transaction. If restoration fails mid-transaction, inserted documents roll back, but empty collections can remain; the operator must create a fresh disposable target on retry. No destructive cleanup is automated.
- This is one piece of Gate 4. Dedicated sender tests now simulate a lost HTTP reply and retry with the exact immutable operation identity; this is not a full operating-system network partition drill. Backup rotation, crash recovery of the actual desktop process, and secure local process hardening remain future gates.

## Tests
The new real-replica test `backend/tests/test_desktop_backup_lab.py` demonstrates encrypted backup, verified restore, no overwrite, incorrect passphrase, tampered ciphertext, missing opt-in marker, and preservation of the source.
