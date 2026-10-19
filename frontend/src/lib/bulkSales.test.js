import { isBulkCategorySummary, parseBulkSales } from "./bulkSales";

const summaries = [
  ["10209", "ACCESSORI TABACCHI", 4, "6.40"],
  ["10008", "InalazioneSenzaCombustione", 8, "56.70"],
  ["10007", "TrinciatiPerSigarette", 2, "16.00"],
  ["10004", "Sigari", 1, "2.80"],
];

test.each(summaries)("riconosce il riepilogo %s %s", (codice, descrizione) => {
  expect(isBulkCategorySummary(codice, descrizione)).toBe(true);
});

test("esclude i riepiloghi dal formato verticale e conserva le vendite", () => {
  const text = [
    ...summaries.flat(),
    "AMMS338", "WINSTON BLUE", "2", "12.40",
  ].join("\n");

  const parsed = parseBulkSales(text, "2026-10-19", idx => `row-${idx}`);

  expect(parsed.excludedRows).toHaveLength(4);
  expect(parsed.rows).toEqual([expect.objectContaining({
    codice: "AMMS338",
    descrizione: "WINSTON BLUE",
    quantita: 2,
    importo: 12.4,
  })]);
});

test("non esclude un prodotto reale che condivide il codice 10008", () => {
  const parsed = parseBulkSales("10008\tOCB FILTRI\t1\t3.50", "2026-10-19", () => "row-0");

  expect(parsed.excludedRows).toHaveLength(0);
  expect(parsed.rows).toHaveLength(1);
});
