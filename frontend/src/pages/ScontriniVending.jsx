import { useEffect, useState } from "react";
import { toast } from "sonner";
import Layout from "../components/Layout";
import { api, apiErrorMessage } from "../lib/api";
import { Card, KpiCard, formatEur } from "../components/UI";
import { SearchBar, Th, useSortSearch } from "../lib/tableHooks";

export default function ScontriniVending() {
  const [data, setData] = useState({ movimenti: [], totale: 0 });
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = useState({ data: today, importo: 0, descrizione: "", operatore: "" });

  const { rows, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(data.movimenti, {
    initial: "data",
    dir: "desc",
    searchFields: ["descrizione", "operatore"],
  });

  const load = async () => {
    try {
      const response = await api.get("/scontrini-vending");
      setData(response.data);
    } catch (error) {
      toast.error(apiErrorMessage(error, "Impossibile caricare gli scontrini vending"));
    }
  };

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    if (!form.importo) return toast.error("Importo obbligatorio");
    try {
      await api.post("/scontrini-vending", form);
      toast.success("Scontrino vending registrato");
      setForm(current => ({ ...current, importo: 0, descrizione: "" }));
      load();
    } catch (error) {
      toast.error(apiErrorMessage(error, "Impossibile registrare lo scontrino"));
    }
  };

  const del = async (id) => {
    if (!window.confirm("Eliminare lo scontrino?")) return;
    try {
      await api.delete(`/scontrini-vending/${id}`);
      toast.success("Scontrino eliminato");
      load();
    } catch (error) {
      toast.error(apiErrorMessage(error, "Impossibile eliminare lo scontrino"));
    }
  };

  return (
    <Layout title="Scontrini Vending" subtitle="scontrini sommati al prelievo vending">
      <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-3">
        <KpiCard label="Totale scontrini vending" value={formatEur(data.totale)} tone="info" />
        <KpiCard label="Scontrini registrati" value={data.movimenti.length} />
      </div>

      <Card className="mb-6 p-4">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-5">
          <input data-testid="scontrino-vending-data" type="date" value={form.data} onChange={event => setForm({...form, data: event.target.value})} className="rounded-md border px-3 py-2 text-sm" />
          <input data-testid="scontrino-vending-importo" type="number" min="0" step="0.01" placeholder="Importo €" value={form.importo} onChange={event => setForm({...form, importo: parseFloat(event.target.value) || 0})} className="rounded-md border px-3 py-2 font-mono text-sm" />
          <input data-testid="scontrino-vending-desc" placeholder="Descrizione" value={form.descrizione} onChange={event => setForm({...form, descrizione: event.target.value})} className="rounded-md border px-3 py-2 text-sm" />
          <input data-testid="scontrino-vending-operatore" placeholder="Operatore" value={form.operatore} onChange={event => setForm({...form, operatore: event.target.value})} className="rounded-md border px-3 py-2 text-sm" />
          <button data-testid="scontrino-vending-save-btn" onClick={save} className="rounded-md bg-slate-900 px-4 py-2 text-sm text-white transition-colors hover:bg-slate-800">Registra</button>
        </div>
      </Card>

      <div className="mb-4 flex items-center gap-3">
        <SearchBar data-testid="scontrini-vending-search" value={query} onChange={setQuery} placeholder="Cerca descrizione o operatore..." className="max-w-md flex-1" />
        <span className="text-sm text-slate-500">{rows.length} scontrini</span>
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
              {rows.map(row => (
                <tr key={row.id} data-testid={`scontrino-vending-row-${row.id}`}>
                  <td className="font-mono text-xs">{(row.data || "").slice(0, 10)}</td>
                  <td>{row.descrizione}</td>
                  <td className="text-xs text-slate-500">{row.operatore}</td>
                  <td className="text-right font-mono font-semibold text-blue-800">+ {formatEur(row.importo)}</td>
                  <td className="text-right"><button onClick={() => del(row.id)} className="text-xs text-red-600" aria-label="Elimina scontrino">✕</button></td>
                </tr>
              ))}
              {rows.length === 0 ? <tr><td colSpan={5} className="py-8 text-center text-slate-400">Nessuno scontrino vending</td></tr> : null}
            </tbody>
          </table>
        </div>
      </Card>
    </Layout>
  );
}
