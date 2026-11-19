# Staging recovery drill — 25k synthetic sales (2026-11-19)

## Verified result

GitHub Actions: [run 44550192422](https://github.com/loreferra91/GESTIONALE-TABACCHI/actions/runs/44550192422)

The dedicated CI workflow started a **disposable MongoDB 7 replica set**,
created a synthetic operational dataset, ran
`mongodump --archive --gzip | gpg --encrypt` without writing plaintext
archives, verified the encrypted archive checksum, streamed it through
`gpg --decrypt | mongorestore` into a **second disposable database**,
and compared source vs restored documents.

| Object | Verified restored documents |
|---|---:|
| Native sales (`vendite`) | 25,002 |
| Products (`prodotti`) | 102 |
| Cash movements (`cassa`) | 1 |
| POS batches (`cassa_rapida_batches`) | 1 |
| Audit events (`audit_events`) | 1 |
| BULK batches (`vendite_bulk_imports`) | 1 |
| **Total** | **25,108** |

The 25,000 additional BULK rows are split across 100 synthetic products.
For each product, its 250 sold units and 750 remaining shop units are
preserved. Batch metadata, contents, and indexes are also checked.
Restored data are checked document-by-document with bounded cursors, not
by materializing the entire archive in memory.

**GitHub Actions result:** `25108 document(s) restored successfully.
0 document(s) failed to restore.` The separate post-restore document
comparison and index check also passed.

## Safety properties

- No Atlas username, password or URI was provided to this workflow.
- Neither `gestionale_staging` nor `gestionale` was accessed.
- Synthetic databases and temporary GPG keys are deleted at the end.
- The workflow is CI-only and does not enable a live scheduled backup,
  automatic restore, data migration, or production deployment.

## What remains unverified

This is **not** a recovery certification for a real business backup.
Before enabling a live RPO/RTO, independently verify: off-cluster durable
storage, retention, monitoring and alerting, cryptographic key recovery,
least-privilege backup credentials, consistent MongoDB snapshots, restore
of a sanitized representative Atlas staging backup to a new isolated
cluster, throughput at the actual data volume, and an approved recovery
operator procedure.

Until those checks are completed, the output manifest must continue
to set `restore_certified: false` for operational archives.
