import { buildStockAnalysisRows, classifyStockRow } from "../lib/stockAnalysis";

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
