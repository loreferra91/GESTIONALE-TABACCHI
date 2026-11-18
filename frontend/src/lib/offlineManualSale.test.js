import { stageManualSale, pendingManualSale, acknowledgeManualSale } from "./offlineManualSale";

let serial = 0;
let priorCrypto;

beforeAll(() => {
  priorCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: {
      randomUUID: () => `00000000-0000-4000-8000-${String(++serial).padStart(12, "0")}`,
    },
  });
});

afterAll(() => {
  if (priorCrypto) Object.defineProperty(globalThis, "crypto", priorCrypto);
  else delete globalThis.crypto;
});

beforeEach(() => {
  serial = 0;
  localStorage.clear();
});

test("persists immutable retry across page reloads and clears after acknowledgment", () => {
  const sale = { data: "2026-11-18", codice: "A1", quantita: 1, importo: 5.50, canale: "NEGOZIO" };
  const first = stageManualSale(sale);
  expect(first.key).toHaveLength(36);
  expect(stageManualSale({ canale: "NEGOZIO", importo: 5.5, codice: "A1", quantita: 1, data: "2026-11-18" }).key).toBe(first.key);
  expect(pendingManualSale().key).toBe(first.key);
  acknowledgeManualSale(first.key);
  expect(pendingManualSale()).toBeNull();
});

test("does not silently overwrite an unresolved sale or clear the wrong receipt", () => {
  const record = stageManualSale({ codice: "A" });
  expect(() => stageManualSale({ codice: "B" })).toThrow(/precedente/);
  expect(() => acknowledgeManualSale("another-key")).toThrow(/non coerente/);
  expect(pendingManualSale().key).toBe(record.key);
});

test("fails closed on damaged local storage rather than issuing a second sale", () => {
  stageManualSale({ codice: "A" });
  localStorage.setItem("gestionale:manual-sale-pending:v1", "{bad-json");
  expect(() => stageManualSale({ codice: "A" })).toThrow(/danneggiata/);
});
