const STATUS_PRIORITY = {
  ANOMALIA: 0,
  ESAURITO: 1,
  "SCORTA BASSA": 2,
  "DA MONITORARE": 3,
  FERMO: 4,
  LENTO: 5,
  OK: 6,
};

export function classifyStockRow(row) {
  if (row.anomalia) return { label: "ANOMALIA", tone: "error" };
  if (row.stato === "ORDINA ORA") {
    return row.magazzino_reale <= 0
      ? { label: "ESAURITO", tone: "error" }
      : { label: "SCORTA BASSA", tone: "warning" };
  }
  if (row.stato === "MONITORA" || row.stato === "CONTROLLO MANUALE") {
    return { label: "DA MONITORARE", tone: "warning" };
  }
  if ((row.venduto_lungo || 0) === 0 && (row.magazzino_reale || 0) > 0) {
    return { label: "FERMO", tone: "warning" };
  }
  if (row.copertura_gg !== null && row.copertura_gg !== undefined && row.copertura_gg > 180) {
    return { label: "LENTO", tone: "warning" };
  }
  return { label: "OK", tone: "ok" };
}

export function buildStockAnalysisRows(data) {
  const aggio = Number(data?.parametri?.AGGIO_PCT ?? 0.10);
  const costFactor = Math.max(0, 1 - aggio);
  return [...(data?.righe || []), ...(data?.esclusi || [])]
    .map(row => {
      const status = classifyStockRow(row);
      const physicalStock = Number(row.giacenza || 0);
      return {
        ...row,
        analysisStatus: status,
        valore_scorta: Math.max(0, physicalStock) * Number(row.prezzo || 0) * costFactor,
      };
    })
    .sort((a, b) => (
      (STATUS_PRIORITY[a.analysisStatus.label] ?? 9) - (STATUS_PRIORITY[b.analysisStatus.label] ?? 9)
      || String(a.descrizione || "").localeCompare(String(b.descrizione || ""), "it")
    ));
}
