const BULK_CATEGORY_SUMMARY_ROWS = new Map([
  ["10209", "ACCESSORITABACCHI"],
  ["10008", "INALAZIONESENZACOMBUSTIONE"],
  ["10007", "TRINCIATIPERSIGARETTE"],
  ["10004", "SIGARI"],
]);

function normalizedSummaryDescription(value = "") {
  return String(value)
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^A-Z0-9]/gi, "")
    .toUpperCase();
}

export function isBulkCategorySummary(codice, descrizione) {
  const normalizedCode = String(codice ?? "").trim().toUpperCase();
  return BULK_CATEGORY_SUMMARY_ROWS.get(normalizedCode) === normalizedSummaryDescription(descrizione);
}

export function parseBulkSales(text, defaultData, uidFactory = (idx) => `bulk-${Date.now()}-${idx}-${Math.random().toString(36).slice(2, 8)}`) {
  const raw = String(text || "").split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  if (!raw.length) return { rows: [], excludedRows: [] };

  // Excel può incollare una cella per riga: in quel caso ricostruiamo
  // blocchi da 4 (senza data) o 5 campi (con data).
  const singleField = raw.every(l => !/\t|;|,/.test(l));
  const groups = [];
  if (singleField && (raw.length % 4 === 0 || raw.length % 5 === 0)) {
    const firstFieldLooksLikeDate = /^\d{4}-\d{1,2}-\d{1,2}(?:[T\s].*)?$/.test(raw[0]) ||
      /^\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}$/.test(raw[0]);
    // Se il numero di celle è divisibile sia per 4 sia per 5 (es. 20),
    // la data distingue in modo affidabile i due formati.
    const step = raw.length % 5 === 0 && (raw.length % 4 !== 0 || firstFieldLooksLikeDate) ? 5 : 4;
    for (let i = 0; i < raw.length; i += step) groups.push(raw.slice(i, i + step));
  } else {
    // Excel usa TAB e mantiene la virgola decimale: scegliamo un solo
    // delimitatore per non spezzare importi come 6,40.
    for (const line of raw) {
      const delimiter = line.includes("\t") ? "\t" : (line.includes(";") ? ";" : ",");
      groups.push(line.split(delimiter).map(x => x.trim()));
    }
  }

  const parsedRows = groups.map((parts, idx) => {
    let data, codice, descrizione, qta, importo;
    if (parts.length >= 5) [data, codice, descrizione, qta, importo] = parts;
    else if (parts.length === 4) { [codice, descrizione, qta, importo] = parts; data = defaultData; }
    else if (parts.length === 3) { [codice, qta, importo] = parts; descrizione = ""; data = defaultData; }
    else return null;
    return {
      _uid: uidFactory(idx),
      data: data || defaultData,
      codice: (codice || "").trim(),
      descrizione: (descrizione || "").trim(),
      quantita: parseInt(String(qta || "0").replace(/[^\d-]/g, ""), 10) || 0,
      importo: parseFloat(String(importo || "0").replace(",", ".").replace(/[^\d.-]/g, "")) || 0,
    };
  }).filter(Boolean);

  const excludedRows = parsedRows.filter(row => isBulkCategorySummary(row.codice, row.descrizione));
  return {
    rows: parsedRows.filter(row => !isBulkCategorySummary(row.codice, row.descrizione)),
    excludedRows,
  };
}
