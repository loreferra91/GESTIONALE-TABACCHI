import { clearIdempotencyKey, idempotencyRecordFor } from "./idempotency";

beforeEach(() => sessionStorage.clear());

test("reuses the same key and timestamp for a retry of the same operation", () => {
  const payload = { righe: [{ id: "A01", quantita: 2 }], pagamento: "CONTANTI" };
  const first = idempotencyRecordFor("retry-test", payload);
  const retry = idempotencyRecordFor("retry-test", {
    pagamento: "CONTANTI", righe: [{ quantita: 2, id: "A01" }],
  });

  expect(retry.key).toBe(first.key);
  expect(retry.createdAt).toBe(first.createdAt);
});

test("rotates the key when the payload changes or an operation completes", () => {
  const first = idempotencyRecordFor("rotation-test", { quantita: 1 });
  const changed = idempotencyRecordFor("rotation-test", { quantita: 2 });
  expect(changed.key).not.toBe(first.key);

  clearIdempotencyKey("rotation-test");
  const next = idempotencyRecordFor("rotation-test", { quantita: 2 });
  expect(next.key).not.toBe(changed.key);
});
