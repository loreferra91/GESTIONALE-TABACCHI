// Only a non-cryptographic payload identity and a random request key are kept
// in sessionStorage. The backend SHA-256 fingerprint remains authoritative.
export const BULK_PENDING_STORAGE_KEY = "gestionale:bulk:pending:v1";
const MAX_PENDING_AGE_MS = 24 * 60 * 60 * 1000;

export function bulkPayloadFingerprint(payload) {
  const value = JSON.stringify(payload);
  let hash = 0xcbf29ce484222325n;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= BigInt(value.charCodeAt(i));
    hash = (hash * 0x100000001b3n) & 0xffffffffffffffffn;
  }
  return `${value.length}:${hash.toString(16).padStart(16, "0")}`;
}

function sessionStore() {
  try { return window.sessionStorage; } catch (_) { return null; }
}

function readPending(storage) {
  try {
    return JSON.parse(storage?.getItem(BULK_PENDING_STORAGE_KEY) || "null");
  } catch (_) {
    return null;
  }
}

export function getOrCreateBulkRequest(
  payload,
  previous = null,
  storage = sessionStore(),
  now = Date.now(),
  createKey = () => crypto.randomUUID(),
) {
  const fingerprint = bulkPayloadFingerprint(payload);
  if (previous?.fingerprint === fingerprint) return previous;
  const saved = readPending(storage);
  if (saved?.fingerprint === fingerprint && typeof saved.key === "string" &&
      saved.key.length >= 10 && Number.isFinite(saved.createdAt)) {
    // An uncertain result must never silently become a fresh operation.
    if (now - saved.createdAt > MAX_PENDING_AGE_MS || saved.createdAt > now) {
      throw new Error("Importazione di esito incerto: verifica l'ultimo BULK prima di riprovare.");
    }
    return saved;
  }
  const pending = { fingerprint, key: createKey(), createdAt: now };
  try { storage?.setItem(BULK_PENDING_STORAGE_KEY, JSON.stringify(pending)); } catch (_) {
    // Storage can be disabled; the in-memory key still protects ordinary retries.
  }
  return pending;
}

export function clearPendingBulkRequest(storage = sessionStore()) {
  try { storage?.removeItem(BULK_PENDING_STORAGE_KEY); } catch (_) {
    // Private mode and disabled storage must not break a completed import.
  }
}
