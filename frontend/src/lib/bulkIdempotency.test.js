import {
  BULK_PENDING_STORAGE_KEY,
  bulkPayloadFingerprint,
  clearPendingBulkRequest,
  getOrCreateBulkRequest,
} from "./bulkIdempotency";

function fakeStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

const base = {
  canale: "NEGOZIO", pagamento: "CONTANTI",
  righe: [{ codice: "P1", quantita: 2, importo: 10, data: "2026-11-19" }],
};

test("recovers the same request key after a simulated page reload", () => {
  const storage = fakeStorage();
  const first = getOrCreateBulkRequest(base, null, storage, 1000, () => "new-key-123456789");
  const afterReload = getOrCreateBulkRequest(base, null, storage, 2000, () => {
    throw Error("a new key must not be generated");
  });
  expect(afterReload).toEqual(first);
  expect(JSON.stringify(afterReload)).not.toContain("P1");
});

test("creates a fresh request key for a changed payload", () => {
  const storage = fakeStorage();
  const original = getOrCreateBulkRequest(base, null, storage, 1000, () => "first-key-12345678");
  const changed = getOrCreateBulkRequest(
    { ...base, righe: [{ ...base.righe[0], quantita: 3 }] },
    original, storage, 2000, () => "second-key-1234567",
  );
  expect(changed.key).not.toBe(original.key);
  expect(changed.fingerprint).not.toBe(original.fingerprint);
});

test("requires review instead of silently retrying with a new key after expiry", () => {
  const storage = fakeStorage();
  getOrCreateBulkRequest(base, null, storage, 1000, () => "initial-key-123456");
  expect(() => getOrCreateBulkRequest(base, null, storage, 1000 + 25 * 3600 * 1000, () => "different-key")).toThrow(/esito incerto/);
});

test("clears pending key only after acknowledged success", () => {
  const storage = fakeStorage();
  getOrCreateBulkRequest(base, null, storage, 1000, () => "initial-key-123456");
  expect(storage.getItem(BULK_PENDING_STORAGE_KEY)).toBeTruthy();
  clearPendingBulkRequest(storage);
  expect(storage.getItem(BULK_PENDING_STORAGE_KEY)).toBeNull();
});

test("fingerprint is stable and does not persist full sale payload", () => {
  expect(bulkPayloadFingerprint(base)).toBe(bulkPayloadFingerprint(base));
  expect(bulkPayloadFingerprint(base)).not.toContain("P1");
});
