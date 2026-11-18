# Gate 2.2 — real MongoDB integration certification

This commit adds a **fail-closed, disposable replica-set test** and a GitHub Actions job scoped to the desktop development branch. Nothing connects to Atlas or deploys to Render.

- Runs a MongoDB 7 container as replica set `rsDesktopCert` bound to runner loopback.
- Test accepts only explicit opt-in (`CERT_DESKTOP_ALLOW_TEST=1`) with `mongodb://127.0.0.1` or `localhost` and verified replica-set ID `rsDesktopCert`. Each run operates on a random `gestionale_cert_desktop_*` database and drops only that generated namespace.
- Exercises actual Motor sessions, sale + stock + outbox atomic commit, replay with same key, changed payload rejection, stock underflow protection and injected failure before outbox commit followed by verified rollback and clean retry.
- Unit tests for local launcher, outbox and conflict protocol run in the same job.
- This is a software-test gate **not** a multi-device, full application, network partition, crash-failover or cloud synchronization certification.

Manual test without the workflow (only on isolated locally managed MongoDB replica set named exactly rsDesktopCert):

```bash
CERT_DESKTOP_ALLOW_TEST=1 \\
CERT_MONGO_URL='mongodb://127.0.0.1:27019/?directConnection=true' \\
python -m pytest backend/tests/test_desktop_sale_replica.py -q
```

A green GitHub run must be inspected before declaring the test passed. Workflow creation does not prove it ran.
