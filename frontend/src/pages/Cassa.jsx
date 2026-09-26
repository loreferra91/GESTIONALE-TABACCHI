import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api } from "../lib/api";
import { Card, KpiCard, Badge, formatEur } from "../components/UI";
import { useSortSearch, Th, SearchBar } from "../lib/tableHooks";
import { toast } from "sonner";

export default function Cassa() {
  const [data, setData] = useState({ movimenti: [], saldo: 0 });
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = useState({ data: today, tipo: "ENTRATA", importo: 0, descrizione: "", operatore: "" });

  const { rows, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(data.movimenti || [], {
    initial: "data", dir: "desc",
    searchFields: ["tipo", "descrizione", "operatore"],
  });

  const load = async () => {
    const r = await api.get("/cassa");
    setData(r.data);
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    if (!form.importo) return toast.error("Importo obbligatorio");
    await api.post("/cassa", form);
    toast.success("Movimento registrato");
    setForm({ ...form, importo: 0, descrizione: "" });
    load();
  };

  const del = async (id) => {
    if (!window.confirm("Eliminare?")) return;
    await api.delete(`/cassa/${id}`);
    load();
  };

  return (
    <Layout title="Cassa" subtitle="movimenti contabili">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <KpiCard label="Saldo cassa" value={formatEur(data.saldo)} tone={data.saldo < 0 ? "danger" : "success"} />
        <KpiCard label="Movimenti" value={data.movimenti.length} />
      </div>

      <Card className="p-4 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-6 gap-3">
          <input data-testid="cassa-data" type="date" value={form.data} onChange={e => setForm({...form, data: e.target.value})} className="border rounded-md px-3 py-2 text-sm" />
          <select data-testid="cassa-tipo" value={form.tipo} onChange={e => setForm({...form, tipo: e.target.value})} className="border rounded-md px-3 py-2 text-sm">
            <option>ENTRATA</option><option>USCITA</option><option>SALDO_INIZIALE</option>
          </select>
          <input data-testid="cassa-importo" type="number" step="0.01" placeholder="Importo €" value={form.importo} onChange={e => setForm({...form, importo: parseFloat(e.target.value) || 0})} className="border rounded-md px-3 py-2 text-sm font-mono" />
          <input data-testid="cassa-desc" placeholder="Descrizione" value={form.descrizione} onChange={e => setForm({...form, descrizione: e.target.value})} className="border rounded-md px-3 py-2 text-sm md:col-span-2" />
          <button data-testid="cassa-save-btn" onClick={save} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors">Registra</button>
        </div>
      </Card>

      <div className="flex items-center gap-3 mb-4">
        <SearchBar data-testid="cassa-search" value={query} onChange={setQuery} placeholder="Cerca tipo, descrizione o operatore…" className="flex-1 max-w-md" />
        <span className="text-sm text-slate-500">{rows.length} movimenti</span>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead><tr>
              <Th sortKey="data" currentKey={sortKey} dir={sortDir} onClick={toggle}>Data</Th>
              <Th sortKey="tipo" currentKey={sortKey} dir={sortDir} onClick={toggle}>Tipo</Th>
              <Th sortKey="descrizione" currentKey={sortKey} dir={sortDir} onClick={toggle}>Descrizione</Th>
              <Th sortKey="operatore" currentKey={sortKey} dir={sortDir} onClick={toggle}>Operatore</Th>
              <Th sortKey="importo" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Importo</Th>
              <th></th>
            </tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id} data-testid={`cassa-row-${r.id}`}>
                  <td className="font-mono text-xs">{(r.data || "").slice(0, 10)}</td>
                  <td><Badge tone={r.tipo === "USCITA" ? "error" : "ok"}>{r.tipo}</Badge></td>
                  <td>{r.descrizione}</td>
                  <td className="text-xs text-slate-500">{r.operatore}</td>
                  <td className={`font-mono text-right font-semibold ${r.tipo === "USCITA" ? "text-red-600" : "text-emerald-700"}`}>
                    {r.tipo === "USCITA" ? "−" : "+"} {formatEur(r.importo)}
                  </td>
                  <td className="text-right"><button onClick={() => del(r.id)} className="text-red-600 text-xs">✕</button></td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={6} className="text-center py-8 text-slate-400">Nessun movimento</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </Layout>
  );
}
