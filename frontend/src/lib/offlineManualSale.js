/** Persistent, fail-closed manual-sale request, for unknown network outcomes. */
const KEY = "gestionale:manual-sale-pending:v1";

const canonical = (value) => {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  }
  return value;
};
const fingerprint = (payload) => JSON.stringify(canonical(payload));

function newUuid() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  if (!globalThis.crypto?.getRandomValues) throw new Error("Generatore UUID sicuro non disponibile");
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  return Array.from(bytes, (value, i) => value.toString(16).padStart(2, "0") + ([3, 5, 7, 9].includes(i) ? "-" : "")).join("");
}

export function pendingManualSale() {
  const raw = localStorage.getItem(KEY);
  if (!raw) return null;
  let record;
  try { record = JSON.parse(raw); } catch { throw new Error("Richiesta vendita locale danneggiata: verifica necessaria"); }
  if (!record || typeof record.key !== "string" || record.key.length < 10 ||
      !record.payload || typeof record.payload !== "object" ||
      record.fingerprint !== fingerprint(record.payload)) {
    throw new Error("Richiesta vendita locale incoerente: verifica necessaria");
  }
  return record;
}

export function stageManualSale(payload) {
  const stored = pendingManualSale();
  const encoded = fingerprint(payload);
  if (stored) {
    if (stored.fingerprint !== encoded) {
      throw new Error("Vendita precedente da verificare: ripristina e ritenta la richiesta in sospeso, senza generarne una nuova");
    }
    return stored;
  }
  const record = { key: newUuid(), payload: JSON.parse(encoded), fingerprint: encoded, createdAt: new Date().toISOString() };
  localStorage.setItem(KEY, JSON.stringify(record));
  const verified = pendingManualSale();
  if (verified?.key !== record.key) throw new Error("Non è stato possibile salvare la vendita in sospeso");
  return verified;
}

export function acknowledgeManualSale(key) {
  const stored = pendingManualSale();
  if (!stored || stored.key !== key) {
    throw new Error("Conferma locale non coerente: verifica la vendita prima di proseguire");
  }
  localStorage.removeItem(KEY);
}
