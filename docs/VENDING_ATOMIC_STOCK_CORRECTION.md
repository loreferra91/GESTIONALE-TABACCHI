# Vending manual stock correction — atomic integrity

Manual vending column corrections update both the column and the corresponding product vending aggregate in a single Atlas transaction, with append-only audit. The physical shop stock must not change from a vending correction.

A repeated PUT using the already-current quantity produces no duplicate audit. Missing products block the operation; the aggregate includes all matching columns rather than silently truncating after 500.

## Certification

The regular Verify CI runs backend and frontend regressions and builds. A separate disposable real MongoDB replica-set workflow injects a product collection validation failure after the column update: it must roll back the column and leave no audit event. The same test then checks one valid correction, its replay, shop stock preservation and exactly one audit entry.

No test contacts either Atlas production or staging.

## Release and rollback

Release to staging first; verify the exact Render commit and the real-Mongo workflow. The production branch receives only the same small change after successful CI and read-only Atlas checks. Revert the change to return to the last known build; no migration, stock rewrite or manual reconciliation is authorized.
