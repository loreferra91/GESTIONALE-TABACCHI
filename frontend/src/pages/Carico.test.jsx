import { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../components/Layout", () => ({ children, title }) => (
  <main><h1>{title}</h1>{children}</main>
));

jest.mock("../lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn() },
  apiErrorMessage: (_error, fallback) => fallback,
}));

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn() },
}));

import Carico from "./Carico";
import { api } from "../lib/api";

const product = {
  id: "product-1",
  codice: "SIG1",
  descrizione: "Articolo test",
  categoria: "SIGARETTE",
  prezzo: 5.5,
};

const smartVenueReceipt = {
  id: "order-1",
  codice: "SIG1",
  descrizione: "Articolo test",
  quantita: 10,
  data: "2026-10-30T01:00:00Z",
  file_sorgente: "Auto-Order Excel",
  smart_venue_applicabile: true,
  caricato_smart_venue: false,
};

let container;
let root;

beforeEach(async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  api.get.mockImplementation((path) => {
    if (path === "/prodotti") return Promise.resolve({ data: [product] });
    if (path === "/ordini") return Promise.resolve({ data: [smartVenueReceipt] });
    if (path === "/smart-venue") return Promise.resolve({ data: [{ codice: "SIG1", barcode: "0012345678901" }] });
    return Promise.reject(new Error(`Unexpected GET ${path}`));
  });
  api.post.mockImplementation((path) => {
    if (path === "/ordini/import-auto-order/preview") {
      return Promise.resolve({ data: {
        file: "ordine_20261020.xlsx",
        fornitore: "Deposito Rossi",
        data_ordine: "2026-10-20",
        selezione: "Tutte le categorie",
        righe: [{ codice: "SIG1", descrizione: "Articolo test", quantita: 10, prezzo: 5.5 }],
        avvisi: [],
      } });
    }
    if (path === "/ordini/bulk") {
      return Promise.resolve({ data: {
        caricate: 1,
        prodotti_nuovi: 0,
        batch_id: "receipt-1",
        backup_id: "backup-1",
        gia_registrato: false,
      } });
    }
    return Promise.reject(new Error(`Unexpected POST ${path}`));
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(<Carico />));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  jest.clearAllMocks();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});

test("importa l'Excel Auto-Order, chiede conferma con riepilogo e mostra l'esito", async () => {
  const file = new File(["xlsx"], "ordine_20261020.xlsx", {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  const input = container.querySelector('[data-testid="carico-import-excel"]');
  Object.defineProperty(input, "files", { configurable: true, value: [file] });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));

  expect(api.post).toHaveBeenCalledWith(
    "/ordini/import-auto-order/preview",
    expect.any(FormData),
  );
  expect(container.querySelector('[data-testid="carico-import-summary"]')).not.toBeNull();
  expect(container.textContent).toContain("Deposito Rossi");
  expect(container.querySelector('[data-testid="carico-row-0-qta"]').value).toBe("10");

  await act(async () => container.querySelector('[data-testid="carico-conferma"]').click());
  expect(container.textContent).toContain("Confermare ordine e carico?");
  expect(container.textContent).toContain("55,00");

  const confirmButton = Array.from(container.querySelectorAll("button"))
    .find(button => button.textContent.includes("Conferma e aggiorna magazzino"));
  await act(async () => confirmButton.click());

  expect(api.post).toHaveBeenLastCalledWith(
    "/ordini/bulk",
    expect.objectContaining({
      file_sorgente: "Auto-Order Excel · ordine_20261020.xlsx",
      righe: [expect.objectContaining({ codice: "SIG1", quantita: 10, prezzo: 5.5 })],
      idempotency_key: expect.any(String),
    }),
  );
  const summary = container.querySelector('[data-testid="carico-success-summary"]');
  expect(summary).not.toBeNull();
  expect(summary.textContent).toContain("Carico confermato");
  expect(summary.textContent).toContain("receipt-1");
  expect(summary.textContent).toContain("backup-1");
});

test("mostra il barcode Smart Venue tra sorgente e conferma del carico", () => {
  const barcodeCell = container.querySelector('[data-testid="carico-smart-barcode-SIG1"]');

  expect(barcodeCell).not.toBeNull();
  expect(barcodeCell.textContent).toBe("0012345678901");
  expect(barcodeCell.previousElementSibling.textContent).toContain("Auto-Order Excel");
  expect(barcodeCell.nextElementSibling.textContent).toContain("Conferma caricamento esterno");
});
