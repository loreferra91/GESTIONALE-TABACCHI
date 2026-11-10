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
const otherCategoryProduct = {
  ...automaticProduct,
  codice: "ALTRO1",
  descrizione: "Prodotto altra categoria",
  categoria: "ACCESSORI",
  qta_da_ordinare: 3,
  totale: 15,
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

test("mostra il confronto 3.0 in sola lettura senza sostituire la conferma 2.0", async () => {
  const comparison = {
    mode: "CONFRONTO_SENZA_CONFERMA", native_history_days: 60,
    summary: { stockout_risk: 1, supplementary_orders: 1, already_on_order: 1,
      vending_refills: 0, pieces_2: 5, pieces_3: 3, insufficient_data: 0,
      known_purchase_cost: 9, missing_purchase_costs: 0 },
    rows: [{ codice: "AUTO1", descrizione: "Prodotto automatico", quantita_2: 5,
      quantita_suggerita: 3, metodo_previsione: "BASELINE 10/30",
      motivazione: "Stock fisico 10; arrivi 20; fabbisogno netto 3.",
      domanda_negozio_gg: 1, domanda_vending_gg: 0, scorta_sicurezza: 1,
      inventory_position: 30, planned_vending_transfer: 0,
      qualita_previsione: "OSSERVABILE", classe_rotazione: "NORMAL MOVER",
      metodo_negozio: "BASELINE 10/30", metodo_vending: "BASELINE 10/30" }],
  };
  api.get.mockImplementation(url => Promise.resolve({ data: url === "/auto-order/confronto" ? comparison
    : url === "/auto-order" ? response : [] }));
  await act(async () => container.querySelector('[data-testid="ao-refresh"]').click());
  expect(container.textContent).toContain("Previsione e pianificazione");
  expect(container.querySelector('[data-testid="ao-comparison-AUTO1"]').textContent)
    .toContain("2.0 5 / 3.1 3");
  await act(async () => container.querySelector('[data-testid="ao-conferma"]').click());
  const confirm = [...container.querySelectorAll("button")].find(button => button.textContent === "Crea ordine fornitore");
  await act(async () => confirm.click());
  expect(api.post).toHaveBeenCalledWith("/auto-order/conferma", expect.objectContaining({
    righe: [{ codice: "AUTO1", quantita: 5 }],
  }));
});

test("permette una riga integrativa datata e blocca quantità oltre il fabbisogno", async () => {
  const supplementary = { ...automaticProduct, codice: "SUP1", descrizione: "Integrativo",
    quantita_in_arrivo: 20, quantita_senza_data: 0, quantita_integrativa_massima: 30,
    lotto_ordine: 10, qta_da_ordinare: 30 };
  api.get.mockImplementation(url => Promise.resolve({ data: url === "/auto-order" ? {
    ...response, righe: [supplementary], esclusi: [],
  } : [] }));
  await act(async () => container.querySelector('[data-testid="ao-refresh"]').click());
  expect(container.querySelector('[data-testid="ao-row-SUP1"]')).not.toBeNull();
  await write(container.querySelector('[data-testid="ao-quantity-SUP1"]'), "40");
  expect(container.querySelector('[data-testid="ao-conferma"]').disabled).toBe(true);
  await write(container.querySelector('[data-testid="ao-quantity-SUP1"]'), "30");
  expect(container.querySelector('[data-testid="ao-conferma"]').disabled).toBe(false);
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

test("premendo Invio seleziona il risultato e poi lo aggiunge", async () => {
  const search = container.querySelector('[data-testid="ao-add-product"]');
  await write(search, "AMMS100");

  await act(async () => search.dispatchEvent(new KeyboardEvent("keydown", {
    key: "Enter",
    bubbles: true,
  })));
  expect(container.querySelector('[data-testid="ao-manual-quantity"]').value).toBe("10");
  await act(async () => search.dispatchEvent(new KeyboardEvent("keydown", {
    key: "Enter",
    bubbles: true,
  })));

  expect(container.querySelector('[data-testid="ao-row-AMMS100"]')).not.toBeNull();
  expect(container.querySelector('[data-testid="ao-quantity-AMMS100"]').value).toBe("10");

  await write(search, "AMMS100");
  expect(container.querySelector('[data-testid="ao-product-result-AMMS100"]').disabled).toBe(true);
  expect(container.querySelector('[data-testid="ao-product-result-AMMS100"]').textContent).toContain("già nella bozza");
});

test("mostra ma blocca un articolo con fornitura da verificare", async () => {
  const search = container.querySelector('[data-testid="ao-add-product"]');
  await act(async () => {
    api.get.mockResolvedValueOnce({ data: {
      ...response,
      esclusi: [{ ...excludedProduct, codice: "PENDING", descrizione: "Fornitura da verificare", stato: "CONTROLLO MANUALE", quantita_senza_data: 10 }],
    } });
    container.querySelector('[data-testid="ao-refresh"]').click();
  });
  await write(search, "Fornitura da verificare");
  expect(container.querySelector('[data-testid="ao-product-result-PENDING"]').disabled).toBe(true);
  expect(container.querySelector('[data-testid="ao-product-result-PENDING"]').textContent).toContain("COPERTURA SUFFICIENTE");
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

test("le categorie escluse non entrano nella conferma e si possono ripristinare", async () => {
  api.get.mockImplementation(url => Promise.resolve({ data: url === "/auto-order" ? {
    ...response, righe: [automaticProduct, otherCategoryProduct],
  } : [] }));
  await act(async () => container.querySelector('[data-testid="ao-refresh"]').click());
  const category = container.querySelector('[data-testid="ao-category-ACCESSORI"]');
  await act(async () => category.click());
  expect(category.getAttribute("aria-pressed")).toBe("false");
  expect(container.querySelector('[data-testid="ao-row-ALTRO1"]')).toBeNull();
  const prompt = jest.spyOn(window, "prompt").mockReturnValue("Fornitore");
  const open = jest.spyOn(window, "open").mockImplementation(() => null);
  await act(async () => container.querySelector('[data-testid="ao-pdf"]').click());
  const exportUrl = new URL(open.mock.calls[0][0], "https://example.test");
  expect(JSON.parse(exportUrl.searchParams.get("selezione"))).toEqual([{ codice: "AUTO1", quantita: 5 }]);
  open.mockRestore();
  prompt.mockRestore();
  await act(async () => container.querySelector('[data-testid="ao-conferma"]').click());
  const confirm = [...container.querySelectorAll("button")].find(button => button.textContent === "Crea ordine fornitore");
  await act(async () => confirm.click());
  expect(api.post).toHaveBeenCalledWith("/auto-order/conferma", expect.objectContaining({
    righe: [{ codice: "AUTO1", quantita: 5 }],
  }));
  await act(async () => category.click());
  expect(container.querySelector('[data-testid="ao-row-ALTRO1"]')).not.toBeNull();
});

test("un prodotto con giacenza positiva si aggiunge con la quantità scelta", async () => {
  const search = container.querySelector('[data-testid="ao-add-product"]');
  await write(search, "AMMS100");
  await act(async () => container.querySelector('[data-testid="ao-product-result-AMMS100"]').click());
  await write(container.querySelector('[data-testid="ao-manual-quantity"]'), "30");
  await act(async () => container.querySelector('[data-testid="ao-add-product-btn"]').click());
  expect(container.querySelector('[data-testid="ao-quantity-AMMS100"]').value).toBe("30");
  expect(container.querySelector('[data-testid="ao-row-AMMS100"]').textContent).toContain("AGGIUNTO MANUALMENTE");
});

test("un articolo da controllare senza anomalie né forniture resta aggiungibile", async () => {
  api.get.mockImplementation(url => Promise.resolve({ data: url === "/auto-order" ? {
    ...response,
    esclusi: [{ ...excludedProduct, stato: "CONTROLLO MANUALE", giacenza_negozio: 40 }],
  } : [] }));
  await act(async () => container.querySelector('[data-testid="ao-refresh"]').click());
  await write(container.querySelector('[data-testid="ao-add-product"]'), "AMMS100");
  const result = container.querySelector('[data-testid="ao-product-result-AMMS100"]');
  expect(result.disabled).toBe(false);
  await act(async () => result.click());
  await act(async () => container.querySelector('[data-testid="ao-add-product-btn"]').click());
  expect(container.querySelector('[data-testid="ao-row-AMMS100"]')).not.toBeNull();
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


test("3.1 pronto carica la quantita prevista e conferma con snapshot politica", async () => {
  const forecast = {
    mode: "CONFRONTO_SENZA_CONFERMA", operational_ready: true,
    policy_snapshot_key: "auto-order-3-1:test-snapshot",
    native_history_days: 90,
    operational_blockers: [],
    summary: { stockout_risk: 0, supplementary_orders: 0, already_on_order: 0,
      vending_refills: 0, pieces_2: 5, pieces_3: 3, insufficient_data: 0,
      known_purchase_cost: 0, missing_purchase_costs: 0 },
    rows: [{ codice: "AUTO1", descrizione: "Prodotto automatico",
      quantita_2: 5, quantita_suggerita: 3, metodo_previsione: "BASELINE 10/30",
      motivazione: "Riordina tre pezzi", qualita_previsione: "OSSERVABILE",
      anomalie: [], domanda_negozio_gg: 1, domanda_vending_gg: 0,
      scorta_sicurezza: 1, inventory_position: 0, planned_vending_transfer: 0,
      metodo_negozio: "BASELINE 10/30", metodo_vending: "BASELINE 10/30" }],
  };
  api.get.mockImplementation(url => Promise.resolve({
    data: url === "/auto-order/confronto" ? forecast :
      url === "/auto-order" ? response : [],
  }));
  await act(async () => container.querySelector('[data-testid="ao-refresh"]').click());
  await act(async () => container.querySelector('[data-testid="ao-use-3-1"]').click());
  expect(container.querySelector('[data-testid="ao-use-3-1"]').getAttribute("aria-pressed")).toBe("true");
  expect(container.querySelector('[data-testid="ao-quantity-AUTO1"]').value).toBe("3");
  expect(container.querySelector('[data-testid="ao-pdf"]').disabled).toBe(true);
  await act(async () => container.querySelector('[data-testid="ao-conferma"]').click());
  const submit = [...container.querySelectorAll("button")].find(button => button.textContent === "Crea ordine fornitore");
  await act(async () => submit.click());
  expect(api.post).toHaveBeenCalledWith("/auto-order/conferma", expect.objectContaining({
    policy_version: "AUTO_ORDER_3_1",
    policy_snapshot_key: "auto-order-3-1:test-snapshot",
    righe: [{ codice: "AUTO1", quantita: 3 }],
  }));
});

test("3.1 senza parametri validi resta disabilitato e mantiene fallback 2.0", async () => {
  const forecast = {
    mode: "CONFRONTO_SENZA_CONFERMA",
    operational_ready: false,
    policy_snapshot_key: "snapshot-disabled",
    operational_blockers: ["Configurare il lead time reale del fornitore"],
    native_history_days: 90, summary: { pieces_2: 5, pieces_3: 3 }, rows: [],
  };
  api.get.mockImplementation(url => Promise.resolve({ data: url === "/auto-order/confronto"
    ? forecast : url === "/auto-order" ? response : [] }));
  await act(async () => container.querySelector('[data-testid="ao-refresh"]').click());
  expect(container.querySelector('[data-testid="ao-use-3-1"]').disabled).toBe(true);
  expect(container.querySelector('[data-testid="ao-use-2"]').getAttribute("aria-pressed")).toBe("true");
  expect(container.querySelector('[data-testid="ao-quantity-AUTO1"]').value).toBe("5");
  expect(container.textContent).toContain("Configurare il lead time reale del fornitore");
});
