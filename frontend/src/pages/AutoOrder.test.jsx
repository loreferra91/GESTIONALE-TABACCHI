import { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../components/Layout", () => ({ children, title }) => (
  <main><h1>{title}</h1>{children}</main>
));

jest.mock("../lib/api", () => ({
  API: "/api",
  api: { get: jest.fn(), post: jest.fn() },
  apiErrorMessage: (_error, fallback) => fallback,
}));

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn() },
}));

import AutoOrder from "./AutoOrder";
import { api } from "../lib/api";

const excludedProduct = {
  codice: "AMMS100",
  descrizione: "Prodotto manuale test",
  categoria: "SIGARETTE",
  stato: "NESSUN ORDINE",
  motivo: "COPERTURA SUFFICIENTE",
  anomalia: false,
  prezzo: 5,
  lotto_ordine: 10,
  qta_da_ordinare: 0,
  totale: 0,
  giacenza_negozio: 20,
  giacenza_vending: 2,
  magazzino_reale_lordo: 20,
  venduti_vending: 0,
  venduto_breve: 1,
  venduto_lungo: 2,
  domanda_gg: 0.1,
  copertura_gg: 200,
  target_scorta: 2,
};

const automaticProduct = {
  ...excludedProduct,
  codice: "AUTO1",
  descrizione: "Prodotto automatico",
  stato: "ORDINA ORA",
  motivo: "COPERTURA 2gg < 7gg",
  qta_da_ordinare: 5,
  totale: 25,
};

const response = {
  righe: [automaticProduct],
  esclusi: [excludedProduct],
  snapshot_key: "snapshot-1",
  riepilogo_stati: { "ORDINA ORA": 1, "NESSUN ORDINE": 1 },
  parametri: { GIORNI_COPERTURA_MIN: 7, GIORNI_COPERTURA_TARGET: 14 },
  finestra_breve_gg: 10,
  finestra_lunga_gg: 30,
  peso_breve: 0.7,
  peso_lungo: 0.3,
  fattore_sicurezza: 1.15,
  data_riferimento_domanda: "2026-10-20",
  giorni_ritardo_dati: 0,
};

let container;
let root;

const write = async (input, value) => {
  await act(async () => {
    const setInputValue = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value",
    ).set;
    setInputValue.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
};

beforeEach(async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  api.get.mockResolvedValue({ data: response });
  api.post.mockResolvedValue({ data: { ordinati: 2, duplicate: false } });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(<AutoOrder />));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  jest.clearAllMocks();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});

test("cerca un prodotto per descrizione e lo aggiunge alla bozza con il lotto corretto", async () => {
  const search = container.querySelector('[data-testid="ao-add-product"]');
  await write(search, "manuale test");

  const result = container.querySelector('[data-testid="ao-product-result-AMMS100"]');
  expect(result).not.toBeNull();
  expect(result.textContent).toContain("Prodotto manuale test");
  expect(result.textContent).toContain("lotto 10");

  await act(async () => result.click());
  expect(search.value).toContain("AMMS100");
  expect(container.querySelector('[data-testid="ao-add-product-btn"]').disabled).toBe(false);

  await act(async () => container.querySelector('[data-testid="ao-add-product-btn"]').click());

  expect(search.value).toBe("");
  expect(container.querySelector('[data-testid="ao-row-AMMS100"]')).not.toBeNull();
  expect(container.querySelector('[data-testid="ao-quantity-AMMS100"]').value).toBe("10");
  expect(container.querySelector('[data-testid="ao-row-AMMS100"]').textContent).toContain("AGGIUNTO MANUALMENTE");
});

test("premendo Invio aggiunge il primo risultato e non duplica articoli già presenti", async () => {
  const search = container.querySelector('[data-testid="ao-add-product"]');
  await write(search, "AMMS100");

  await act(async () => search.dispatchEvent(new KeyboardEvent("keydown", {
    key: "Enter",
    bubbles: true,
  })));

  expect(container.querySelector('[data-testid="ao-row-AMMS100"]')).not.toBeNull();
  expect(container.querySelector('[data-testid="ao-quantity-AMMS100"]').value).toBe("10");

  await write(search, "AMMS100");
  expect(container.querySelector('[data-testid="ao-product-result-AMMS100"]')).toBeNull();
  expect(container.querySelector('[data-testid="ao-product-no-results"]')).not.toBeNull();
});
