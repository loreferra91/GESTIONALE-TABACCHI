# Gate 2.1 — durable frontend retry + complete local event

This change is limited to `feature/desktop-hybrid-offline-sync`. No cloud sync, desktop installers or production activation.

- Manual sale requests now persist an immutable pending payload + stable idempotency UUID in browser `localStorage` **before** the POST. The same key/payload can be retried after a page reload or uncertain HTTP outcome. The pending record is removed only after an acknowledged HTTP response. If the user edits the form while a sale remains pending, the client refuses to silently replace it; the UI offers to restore the original request.
- Outbox records now retain an immutable event snapshot (`event_payload`) including sale ID, date, cent-based amount, channel and quantity, not just a digest. The outbox write remains within the same Mongo transaction as the sale.
- Desktop bootstrap skips repository demo inventory seed. An empty desktop DB is intentionally empty; controlled local migration is a separate gate.
- Desktop startup rejects a nontransactional Mongo topology before running DB initialization, rather than after.
- Vending sales and all other uninstrumented write routes remain blocked in desktop mode.

Limitations: `localStorage` protects browser retry identity, **not** a substitute for Mongo transactions or encrypted backups. Multiple concurrent windows and cross-device stock conflicts remain unsupported. The final Tauri app will need one controlled window and explicit durable client state. The data transport, cloud authoritative ledger, and reconciliation UI are not built.

Verification: `yarn test:ci` and `python -m pytest backend/tests/test_desktop_outbox.py -q`; see the new replica-set certification test for real transaction behavior.
