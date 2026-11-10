import { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../components/Layout", () => ({ children, title }) => (
  <main><h1>{title}</h1>{children}</main>
));

jest.mock("../lib/api", () => ({
  API: "/api",
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn() },
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
  lotto_ordine: 1,
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

test("presenta il ciclo operativo senza import Excel obbligatorio", () => {
  expect(container.textContent).toContain("Registra le vendite in negozio");
  expect(container.textContent).toContain("Collega la ricezione all’ordine");
  expect(container.textContent).not.toContain("Import Excel");
  expect(container.querySelector('[data-testid="ao-pdf"]')).not.toBeNull();
  expect(container.querySelector('[data-testid="ao-excel"]')).not.toBeNull();
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

test("non propone all'aggiunta un articolo con fornitura da verificare", async () => {
  const search = container.querySelector('[data-testid="ao-add-product"]');
  await act(async () => {
    api.get.mockResolvedValueOnce({ data: {
      ...response,
      esclusi: [{ ...excludedProduct, codice: "PENDING", descrizione: "Fornitura da verificare", stato: "CONTROLLO MANUALE" }],
    } });
    container.querySelector('[data-testid="ao-refresh"]').click();
  });
  await write(search, "Fornitura da verificare");
  expect(container.querySelector('[data-testid="ao-product-result-PENDING"]')).toBeNull();
});

test("Ricalcola chiede prima di sostituire una bozza modificata", async () => {
  await act(async () => container.querySelector('[data-testid="ao-remove-AUTO1"]').click());
  const confirm = jest.spyOn(window, "confirm").mockReturnValue(false);
  const calls = api.get.mock.calls.length;
  await act(async () => container.querySelector('[data-testid="ao-refresh"]').click());
  expect(confirm).toHaveBeenCalled();
  expect(api.get.mock.calls.length).toBe(calls);
  confirm.mockRestore();
});

test("salva la scorta minima del negozio e richiede il ricalcolo", async () => {
  const prompt = jest.spyOn(window, "prompt").mockReturnValue("50");
  api.patch.mockResolvedValue({ data: { codice: "AMMS100", minimo: 50, version: 1 } });
  const show = [...container.querySelectorAll("button")].find(button => button.textContent.includes("Mostra esclusi"));
  await act(async () => show.click());
  await act(async () => container.querySelector('[data-testid="ao-minimum-AMMS100"]').click());
  expect(api.patch).toHaveBeenCalledWith("/auto-order/prodotti/AMMS100/scorta-minima", { minimo: 50, version: 0 });
  expect(container.querySelector('[data-testid="ao-minimum-AMMS100"]').textContent).toContain("50");
  expect(container.querySelector('[data-testid="ao-conferma"]').disabled).toBe(true);
  expect(container.querySelector('[role="alert"]').textContent).toContain("ricalcola Auto-Order");
  prompt.mockRestore();
});

test("una quantità frazionaria blocca la conferma senza arrotondamenti silenziosi", async () => {
  await write(container.querySelector('[data-testid="ao-quantity-AUTO1"]'), "1.5");
  expect(container.querySelector('[data-testid="ao-quantity-AUTO1"]').value).toBe("1.5");
  expect(container.querySelector('[data-testid="ao-conferma"]').disabled).toBe(true);
  expect(container.querySelector('[role="alert"]').textContent).toContain("Quantità non valide");
});

test("la conferma invia lo snapshot usato per la proposta", async () => {
  await act(async () => container.querySelector('[data-testid="ao-conferma"]').click());
  const confirm = [...container.querySelectorAll("button")].find(button => button.textContent === "Crea ordine fornitore");
  await act(async () => confirm.click());
  expect(api.post).toHaveBeenCalledWith("/auto-order/conferma", expect.objectContaining({
    snapshot_key: "snapshot-1",
    righe: [{ codice: "AUTO1", quantita: 5 }],
  }));
});

test("mostra il carico esistente e richiede conferma delle differenze prima di collegarlo", async () => {
  const batch = { batch_key: "order-key", stato: "CREATO", version: 0, righe: [{ codice: "A", quantita: 10 }] };
  const receipt = { ricezione_id: "receipt-1", data: "2026-11-06T00:00:00.000Z", file_sorgente: "ordine.xlsx", righe_coincidenti: 1, righe_ordine: 1, pezzi_caricati: 9, pezzi_ordinati: 10, mancanti: { A: 1 }, extra: {} };
  api.get.mockImplementation(url => Promise.resolve({ data: url === "/auto-order" ? response : url === "/auto-order/forniture" ? [batch] : { carichi: [receipt] } }));
  await act(async () => container.querySelector('[data-testid="ao-refresh"]').click());
  expect(container.textContent).toContain("L’arrivo previsto indica solo una consegna futura");
  const find = [...container.querySelectorAll("button")].find(button => button.textContent === "Merce già arrivata? Cerca carico");
  await act(async () => find.click());
  expect(container.textContent).toContain("ordine.xlsx");
  expect(container.textContent).toContain("ricevuto il 06/11/2026");
  expect(container.textContent).toContain("Mancanti: A 1");
  const confirm = jest.spyOn(window, "confirm").mockReturnValue(true);
  const link = [...container.querySelectorAll("button")].find(button => button.textContent === "Collega e chiudi senza nuovo carico");
  await act(async () => link.click());
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Il magazzino non sarà caricato nuovamente"));
  expect(api.post).toHaveBeenCalledWith("/auto-order/forniture/order-key/chiudi-con-carico/receipt-1", { version: 0, conferma_differenze: true });
  expect(container.textContent).not.toContain("Forniture pendenti");
  confirm.mockRestore();
});

test("annulla una fornitura segnata inviata solo dopo conferma e la rimuove dalle pendenti", async () => {
  const batch = { batch_key: "draft-key", stato: "INVIATO", version: 2, righe: [{ codice: "A", quantita: 10 }] };
  api.get.mockImplementation(url => Promise.resolve({ data: url === "/auto-order" ? response : [batch] }));
  api.patch.mockResolvedValue({ data: { ...batch, stato: "ANNULLATO", version: 3 } });
  await act(async () => container.querySelector('[data-testid="ao-refresh"]').click());
  const confirm = jest.spyOn(window, "confirm").mockReturnValue(true);
  const cancel = [...container.querySelectorAll("button")].find(button => button.textContent === "Annulla fornitura");
  await act(async () => cancel.click());
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("non è da considerare pendente"));
  expect(api.patch).toHaveBeenCalledWith("/auto-order/forniture/draft-key", { version: 2, stato: "ANNULLATO" });
  expect(container.textContent).not.toContain("Forniture pendenti");
  confirm.mockRestore();
});
