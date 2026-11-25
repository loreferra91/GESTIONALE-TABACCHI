import { useEffect, useMemo, useState } from "react";
import Layout from "../components/Layout";
import { Card } from "../components/UI";
import { api, apiErrorMessage } from "../lib/api";

import { SortHeader, sortScratchCardRows, nextSort } from "../components/GrattaVinciSort";

const columns = [
  ["codice", "Codice", false],
  ["descrizione", "Descrizione", false],
  ["giacenza", "Giacenza", true],
  ["venduti_7", "Venduti 7 gg", true],
  ["venduti_30", "Venduti 30 gg", true],
  ["copertura", "Copertura (gg)", true],
  ["da_ordinare", "Da ordinare", true],
  ["stato", "Stato", false],
];
const numericFields = columns.filter(column => column[2]).map(column => column[0]);

export default function GrattaVinciAutoOrder() {
  const [sort, setSort] = useState({ field: "codice", direction: "asc" });
  const changeSort = field => setSort(previous => nextSort(previous, field));
  const [settings, setSettings] = useState({ copertura_giorni: "14", lead_time_giorni: "7", lotto: "1" });
  const [applied, setApplied] = useState(settings);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setResult(null);
    setError("");
    api.get("/gratta-e-vinci/auto-order", { params: applied }).then(response => {
      if (active) setResult(response.data);
    }).catch(err => {
      if (active) setError(apiErrorMessage(err, "Impossibile calcolare la proposta"));
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [applied]);
  const rows = useMemo(() => sortScratchCardRows(result?.righe || [], sort, numericFields), [result, sort]);
  return (
    <Layout title="Gratta e Vinci — Auto-Order" subtitle="Proposte di riordino indipendenti">
      <Card className="p-4 mb-4">
        <form className="flex flex-wrap items-end gap-4" onSubmit={e => { e.preventDefault(); setApplied({ ...settings }); }}>
          {[["copertura_giorni", "Copertura desiderata (giorni)", 1, 90], ["lead_time_giorni", "Tempo di consegna (giorni)", 0, 60], ["lotto", "Biglietti per lotto", 1, 100000]].map(([key, label, min, max]) => (
            <label key={key} className="flex flex-col gap-1 text-sm">{label}
              <input type="number" required min={min} max={max} step="1" value={settings[key]}
                onChange={e => setSettings(old => ({ ...old, [key]: e.target.value }))}
                className="border rounded-md px-3 py-2" />
            </label>
          ))}
          <button type="submit" disabled={loading} className="bg-slate-900 text-white rounded-md px-4 py-2 disabled:opacity-50">Calcola proposta</button>
        </form>
        <p className="text-sm text-slate-600 mt-3">Parametri provvisori modificabili. Usa le vendite dei 7 e 30 giorni conclusi; richiede almeno 30 giorni dalla registrazione del prodotto. Non crea ordini né carichi.</p>
        <p className="text-sm text-amber-800 mt-2">Verificare manualmente forniture pendenti e lotti effettivi prima di ordinare. Le proposte non sottraggono ordini in arrivo.</p>
      </Card>
      {loading && <p role="status">Calcolo in corso…</p>}
      {error && <p role="alert" className="text-red-700">{error}</p>}
      {result && <Card className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50"><tr>{columns.map(([field, label, numeric]) => <SortHeader key={field} field={field} label={label} numeric={numeric} sort={sort} onSort={changeSort} />)}</tr></thead>
          <tbody className="divide-y divide-slate-100">{rows.map(row => (
            <tr key={row.codice}>{[row.codice, row.descrizione, row.giacenza, row.venduti_7, row.venduti_30, row.copertura, row.da_ordinare, row.stato].map((value, i) => <td key={i} className="p-3">{value ?? "—"}</td>)}</tr>
          ))}</tbody>
        </table>
        {!result.righe.length && <p className="p-4 text-slate-500">Nessun prodotto Gratta e Vinci registrato.</p>}
      </Card>}
    </Layout>
  );
}
