const PREFIX = "gestionale:idempotency:";

const newKey = () => {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}-${Math.random().toString(16).slice(2)}`;
};

const canonicalize = (value) => {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map(key => [key, canonicalize(value[key])]),
    );
  }
  return value;
};

const stablePayload = (payload) => JSON.stringify(canonicalize(payload));

export function idempotencyRecordFor(scope, payload) {
  const storageKey = `${PREFIX}${scope}`;
  const fingerprint = stablePayload(payload);
  try {
    const current = JSON.parse(sessionStorage.getItem(storageKey) || "null");
    if (current?.fingerprint === fingerprint && current?.key) return current;
    const key = newKey();
    const record = { fingerprint, key, createdAt: new Date().toISOString() };
    sessionStorage.setItem(storageKey, JSON.stringify(record));
    return record;
  } catch (_error) {
    return { fingerprint, key: newKey(), createdAt: new Date().toISOString() };
  }
}

export function idempotencyKeyFor(scope, payload) {
  return idempotencyRecordFor(scope, payload).key;
}

export function clearIdempotencyKey(scope) {
  try {
    sessionStorage.removeItem(`${PREFIX}${scope}`);
  } catch (_error) {
    // Storage can be unavailable in hardened/private browser contexts.
  }
}
