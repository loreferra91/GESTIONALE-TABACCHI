import { act } from "react";
import { createRoot } from "react-dom/client";
import CashCountEvidence from "./CashCountEvidence";
import { api } from "../lib/api";

jest.mock("../lib/api", () => ({
  api: { post: jest.fn() },
  apiErrorMessage: (_error, fallback) => fallback,
}));

let container;
let root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  sessionStorage.clear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  jest.clearAllMocks();
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});

function update(input, value) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, "value",
  ).set;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

async function fill() {
  const toggle = container.querySelector('button[aria-expanded="false"]');
  await act(async () => toggle.click());
  const oneEuro = container.querySelector('input[aria-label="Quantità 1 euro"]');
  await act(async () => update(oneEuro, "2"));
  const reference = container.querySelector('input[placeholder^="Es. verbale"]');
  await act(async () => update(reference, "registro firmato 19-11"));
  expect(container.querySelector('[data-testid="cash-count-total"]').textContent)
    .toContain("2,00 €");
  return [...container.querySelectorAll("button")].find(x => x.textContent === "Registra evidenza");
}

test("conteggio fisico calcola in centesimi, non corregge i saldi e visualizza la prova", async () => {
  api.post.mockResolvedValue({ data: {
    id: "count-1", total_cents: 200, replayed: false,
    source_reference: "registro firmato 19-11",
  } });
  await act(async () => root.render(<CashCountEvidence />));
  const save = await fill();
  await act(async () => save.click());
  expect(api.post).toHaveBeenCalledTimes(1);
  const [endpoint, payload] = api.post.mock.calls[0];
  expect(endpoint).toBe("/contabilita/conteggi-fisici");
  expect(payload.denominations["1"]).toBe(2);
  expect(payload.kind).toBe("spot");
  expect(payload.idempotency_key).toBeTruthy();
  expect(container.querySelector('[role="status"]').textContent)
    .toContain("Saldo contabile invariato");
});

test("errore ambiguo mantiene la chiave e non permette modifiche fino al retry", async () => {
  api.post.mockRejectedValueOnce(new Error("network"))
    .mockResolvedValueOnce({ data: {
      id: "count-2", total_cents: 200, replayed: true,
      source_reference: "registro firmato 19-11",
    } });
  await act(async () => root.render(<CashCountEvidence />));
  const save = await fill();
  await act(async () => save.click());
  expect(container.querySelector('[role="alert"]').textContent)
    .toContain("Conteggio non confermato");
  expect(container.querySelector('input[aria-label="Quantità 1 euro"]').disabled).toBe(true);
  const retry = [...container.querySelectorAll("button")]
    .find(x => x.textContent === "Riprova identico invio");
  await act(async () => retry.click());
  expect(api.post).toHaveBeenCalledTimes(2);
  expect(api.post.mock.calls[0][1]).toEqual(api.post.mock.calls[1][1]);
  expect(container.querySelector('[role="status"]').textContent)
    .toContain("già registrato");
});
