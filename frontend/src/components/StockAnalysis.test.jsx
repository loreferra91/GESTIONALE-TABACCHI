import { act } from "react";
import { createRoot } from "react-dom/client";
import { buildStockAnalysisRows, classifyStockRow, sortStockAnalysisRows } from "../lib/stockAnalysis";

jest.mock("react-router-dom", () => ({
  Link: ({ children, ...props }) => <a {...props}>{children}</a>,
}));

jest.mock("../lib/api", () => ({
  api: { get: jest.fn() },
  apiErrorMessage: (_error, fallback) => fallback,
}));

import StockAnalysis from "./StockAnalysis";
import { api } from "../lib/api";

test("classifica le scorte usando gli stessi segnali operativi di Auto-Order", () => {
  expect(classifyStockRow({ anomalia: true }).label).toBe("ANOMALIA");
  expect(classifyStockRow({ stato: "ORDINA ORA", magazzino_reale: 0 }).label).toBe("ESAURITO");
  expect(classifyStockRow({ stato: "ORDINA ORA", magazzino_reale: 3 }).label).toBe("SCORTA BASSA");
  expect(classifyStockRow({ stato: "NESSUN ORDINE", magazzino_reale: 12, venduto_lungo: 0 }).label).toBe("FERMO");
  expect(classifyStockRow({ stato: "NESSUN ORDINE", magazzino_reale: 12, venduto_lungo: 2, copertura_gg: 181 }).label).toBe("LENTO");
});

test("calcola sullo stock totale la valorizzazione netta e ordina prima le righe urgenti", () => {
  const rows = buildStockAnalysisRows({
    parametri: { AGGIO_PCT: 0.10 },
    righe: [{ codice: "B", descrizione: "Basso", stato: "ORDINA ORA", magazzino_reale: 2, giacenza_negozio: 2, giacenza_vending: 3, giacenza: 5, prezzo: 5 }],
    esclusi: [{ codice: "A", descrizione: "Regolare", stato: "NESSUN ORDINE", magazzino_reale: 10, giacenza: 10, venduto_lungo: 5, copertura_gg: 20, prezzo: 5 }],
  });

  expect(rows.map(row => row.codice)).toEqual(["B", "A"]);
  expect(rows[0].valore_scorta).toBe(22.5);
  expect(rows[0].giacenza).toBe(5);
  expect(rows[1].valore_scorta).toBe(45);
});

test("ordina tutte le colonne dell'analisi mantenendo i valori mancanti in fondo", () => {
  const rows = [
    { descrizione: "Beta", giacenza: 3, copertura_gg: null, analysisStatus: { label: "OK" } },
    { descrizione: "Alfa", giacenza: 8, copertura_gg: 12, analysisStatus: { label: "SCORTA BASSA" } },
    { descrizione: "Gamma", giacenza: 1, copertura_gg: 4, analysisStatus: { label: "FERMO" } },
  ];

  expect(sortStockAnalysisRows(rows, "descrizione", "asc").map(row => row.descrizione)).toEqual(["Alfa", "Beta", "Gamma"]);
  expect(sortStockAnalysisRows(rows, "giacenza", "desc").map(row => row.giacenza)).toEqual([8, 3, 1]);
  expect(sortStockAnalysisRows(rows, "copertura_gg", "asc").map(row => row.copertura_gg)).toEqual([4, 12, null]);
  expect(sortStockAnalysisRows(rows, "analysis_status", "asc").map(row => row.analysisStatus.label)).toEqual(["FERMO", "OK", "SCORTA BASSA"]);
});

test("mostra i KPI economici della dashboard prima degli indicatori operativi", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  api.get.mockImplementation(path => {
    if (path === "/auto-order") {
      return Promise.resolve({ data: {
        parametri: { AGGIO_PCT: 0.10 },
        finestra_lunga_gg: 30,
        righe: [{ codice: "A", descrizione: "Prodotto", stato: "NESSUN ORDINE", magazzino_reale: 5, giacenza: 5, venduto_lungo: 2, prezzo: 10 }],
        esclusi: [],
      } });
    }
    if (path === "/dashboard") {
      return Promise.resolve({ data: { kpi: {
        valore_acquistato: 1000,
        valore_venduto: 1250,
        margine_lordo: 250,
        valore_giacenza: 45,
        aggio_pct: 0.10,
      } } });
    }
    return Promise.reject(new Error(`Unexpected GET ${path}`));
  });

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => root.render(<StockAnalysis />));

  expect(api.get).toHaveBeenCalledWith("/auto-order", { timeout: 20000 });
  expect(api.get).toHaveBeenCalledWith("/dashboard");
  expect(container.textContent).toContain("Totale acquistato (netto aggio 10%)");
  expect(container.textContent).toContain("1000,00 €");
  expect(container.textContent).toContain("Totale venduto attuale");
  expect(container.textContent).toContain("1250,00 €");
  expect(container.textContent).toContain("Margine lordo");
  expect(container.textContent).toContain("250,00 €");
  expect(container.textContent).toContain("Valore scorte attuali");
  expect(container.textContent).toContain("45,00 €");

  await act(async () => root.unmount());
  container.remove();
  jest.clearAllMocks();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});
