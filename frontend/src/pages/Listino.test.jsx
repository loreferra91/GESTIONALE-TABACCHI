import { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../components/Layout", () => ({ children, actions, title }) => (
  <main><h1>{title}</h1>{actions}{children}</main>
));

jest.mock("../lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn() },
  apiErrorMessage: (_error, fallback) => fallback,
}));

jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

import Listino from "./Listino";
import { api } from "../lib/api";

let container;
let root;

beforeEach(async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  api.get.mockImplementation(path => {
    if (path === "/listino") return Promise.resolve({
      data: {
        total: 1,
        items: [{
          id: "l1", codice: "AMMS338", adm_codice: "338",
          descrizione: "WINSTON BLUE", categoria_adm: "SIGARETTE",
          confezione: "Confezione da 20 pezzi", fonte: "FIT", prezzo: 5.8,
        }],
      },
    });
    if (path === "/fit/status") return Promise.resolve({
      data: {
        fit: { row_count: 4343, effective_date: "2026-10-14", categories: { SIGARETTE: 299 } },
        adm_newer_categories: [{ categoria: "SIGARI" }],
        last_sync: null,
      },
    });
    return Promise.reject(new Error(`Unexpected GET ${path}`));
  });
  api.post.mockImplementation(path => {
    if (path === "/fit/preview") return Promise.resolve({
      data: {
        preview_id: "preview-1", righe: 4325, prodotti_collegati: 1,
        prezzi_modificati: 1, prezzi_invariati: 0, prodotti_non_trovati: 0,
        nuovi_articoli_listino: 18, collegamenti_ambigui: 0,
        listino_nuovi: 1, listino_aggiornati: 1,
        listino_codici_adm_compilati: 1, listino_categorie_compilate: 1,
        listino_fonti_compilate: 1,
        listino_variazioni: [{
          stato: "AGGIORNATO", codice: "AMMS338", adm_codice: "338",
          descrizione: "WINSTON BLUE", categoria_adm: "SIGARETTE",
          campi: [
            { campo: "adm_codice", prima: "", dopo: "338" },
            { campo: "categoria_adm", prima: "", dopo: "SIGARETTE" },
          ],
        }],
        adm_overlays: [], warnings: ["PDF ADM piu recenti disponibili per: SIGARI"],
        variazioni: [{
          codice: "AMMS338", adm_codice: "338", descrizione: "WINSTON BLUE",
          prezzo_attuale: 5.5, prezzo_nuovo: 5.8, differenza: 0.3,
        }],
      },
    });
    return Promise.reject(new Error(`Unexpected POST ${path}`));
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(<Listino />));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  jest.clearAllMocks();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});

test("carica il listino paginato e mostra la fonte FIT", () => {
  expect(container.textContent).toContain("Tariffario FIT in formato XLS");
  expect(container.textContent).toContain("4343 articoli");
  expect(container.textContent).toContain("WINSTON BLUE");
  expect(api.get).toHaveBeenCalledWith("/listino", expect.objectContaining({
    params: expect.objectContaining({ limit: 100, skip: 0, sort: "descrizione", direction: "asc" }),
  }));
});

test("prepara l'anteprima prima di applicare i prezzi", async () => {
  const button = Array.from(container.querySelectorAll("button"))
    .find(item => item.textContent.includes("Prepara aggiornamento"));
  await act(async () => button.click());

  expect(api.post).toHaveBeenCalledWith("/fit/preview", {}, { timeout: 600000 });
  expect(container.textContent).toContain("Anteprima pronta");
  expect(container.textContent).toContain("Variazioni di prezzo");
  expect(container.textContent).toContain("5,80");
  expect(container.textContent).toContain("Nuove righe");
  expect(container.textContent).toContain("Modifiche effettive al listino");
  expect(container.textContent).toContain("Codici ADM compilati");
  expect(container.textContent).toContain("SIGARETTE");
  expect(container.textContent).toContain("PDF ADM piu recenti disponibili per: SIGARI");
});
