import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api } from "../lib/api";
import { Card, KpiCard, formatEur } from "../components/UI";
import { useSortSearch, Th, SearchBar } from "../lib/tableHooks";
import { toast } from "sonner";

export default function Versamenti() {
  const [data, setData] = useState({ movimenti: [], totaleVendutoNegozio: 0, totaleVersamenti: 0, liquiditaResidua: 0 });
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = useState({ data: today, importo: 0, descrizione: "", operatore: "" });

  const { rows, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(data.movimenti || [], {
    initial: "data", dir: "desc",
    searchFields: ["descrizione", "operatore"],
  });

  const load = async () => {
    try {
      const [dashboard, versamenti] = await Promise.all([
        api.get("/dashboard"),
        api.get("/versamenti"),
      ]);
      const totaleVersamenti = dashboard.data?.totale_versamenti || 0;
      const vendutoNegozio = dashboard.data?.venduto_negozio_contabilizzato || 0;
      setData({
        movimenti: versamenti.data.movimenti,
        totaleVendutoNegozio: vendutoNegozio,
        totaleVersamenti: totaleVersamenti,
        liquiditaResidua: dashboard.data?.liquidita_residua || 0,
      });
    } catch {
      toast.error("Impossibile caricare i versamenti");
    }
  };

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    if (!form.importo) return toast.error("Importo obbligatorio");
    await api.post("/versamenti", form);
    toast.success("Versamento registrato");
    setForm({ ...form, importo: 0, descrizione: "" });
    load();
  };

  const del = async (id) => {
    if (!window.confirm("Eliminare?")) return;
    await api.delete(`/versamenti/${id}`);
    load();
  };

  return (
    <Layout title="Versamenti" subtitle="registrazione versamenti">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <KpiCard label="Venduto Negozio contabilizzato" value={formatEur(data.totaleVendutoNegozio)} tone="info" />
        <KpiCard label="Totale Versamenti" value={formatEur(data.totaleVersamenti)} />
        <KpiCard label="Liquidità Residua" value={formatEur(data.liquiditaResidua)} tone={data.liquiditaResidua < 0 ? "danger" : "success"} />
      </div>

      <Card className="p-4 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
          <input data-testid="versamento-data" type="date" value={form.data} onChange={e => setForm({...form, data: e.target.value})} className="border rounded-md px-3 py-2 text-sm" />
          <input data-testid="versamento-importo" type="number" step="0.01" placeholder="Importo €" value={form.importo} onChange={e => setForm({...form, importo: parseFloat(e.target.value) || 0})} className="border rounded-md px-3 py-2 text-sm font-mono" />
          <input data-testid="versamento-desc" placeholder="Descrizione" value={form.descrizione} onChange={e => setForm({...form, descrizione: e.target.value})} className="border rounded-md px-3 py-2 text-sm" />
          <input data-testid="versamento-operatore" placeholder="Operatore" value={form.operatore} onChange={e => setForm({...form, operatore: e.target.value})} className="border rounded-md px-3 py-2 text-sm" />
          <button data-testid="versamento-save-btn" onClick={save} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors">Registra</button>
        </div>
      </Card>

      <div className="flex items-center gap-3 mb-4">
        <SearchBar data-testid="versamenti-search" value={query} onChange={setQuery} placeholder="Cerca descrizione o operatore..." className="flex-1 max-w-md" />
        <span className="text-sm text-slate-500">{data.movimenti.length} versamenti</span>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead><tr>
              <Th sortKey="data" currentKey={sortKey} dir={sortDir} onClick={toggle}>Data</Th>
              <Th sortKey="descrizione" currentKey={sortKey} dir={sortDir} onClick={toggle}>Descrizione</Th>
              <Th sortKey="operatore" currentKey={sortKey} dir={sortDir} onClick={toggle}>Operatore</Th>
              <Th sortKey="importo" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Importo</Th>
              <th></th>
            </tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id} data-testid={`versamento-row-${r.id}`}>
                  <td className="font-mono text-xs">{(r.data || "").slice(0, 10)}</td>
                  <td>{r.descrizione}</td>
                  <td className="text-xs text-slate-500">{r.operatore}</td>
                  <td className={`font-mono text-right font-semibold text-emerald-700`}>
                    + {formatEur(r.importo)}
                  </td>
                  <td className="text-right"><button onClick={() => del(r.id)} className="text-red-600 text-xs">✕</button></td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={5} className="text-center py-8 text-slate-400">Nessun versamento</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </Layout>
  );
}
