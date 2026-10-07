import { useEffect, useState } from "react";
import { ArrowRight } from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import Layout from "../components/Layout";
import { api, apiErrorMessage } from "../lib/api";
import { Card, KpiCard, formatEur } from "../components/UI";
import { useSortSearch, Th, SearchBar } from "../lib/tableHooks";
import { toast } from "sonner";

export default function PrelieviVending() {
  const [data, setData] = useState({ movimenti: [], totale: 0, venditeVendingContanti: 0, giacenzaVending: 0, prelievoVending: 0, scontriniVending: 0 });
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = useState({ data: today, importo: 0, descrizione: "", operatore: "" });

  const { rows, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(data.movimenti || [], {
    initial: "data", dir: "desc",
    searchFields: ["descrizione", "operatore"],
  });

  const load = async () => {
    try {
      const [prelievi, scontrini, dashboard] = await Promise.all([
        api.get("/prelievi-vending"),
        api.get("/scontrini-vending"),
        api.get("/dashboard"),
      ]);
      const saldi = dashboard.data?.saldi || {};
      const venditeVendingContanti = saldi.venditeVendingContanti
        ?? saldi.saldoVendingContanti
        ?? 0;
      const giacenzaInizialeVending = saldi.giacenzaInizialeVendingContanti ?? 0;
      const prelievoVending = Number(prelievi.data?.totale ?? 0);
      const scontriniVending = Number(scontrini.data?.totale ?? 0);
      const giacenzaVending = giacenzaInizialeVending + venditeVendingContanti - prelievoVending;
      setData({
        movimenti: prelievi.data.movimenti,
        totale: prelievi.data.totale,
        venditeVendingContanti,
        giacenzaVending,
        prelievoVending,
        scontriniVending,
      });
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile caricare i prelievi vending"));
    }
  };

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    if (!form.importo) return toast.error("Importo obbligatorio");
    try {
      await api.post("/prelievi-vending", form);
      toast.success("Prelievo vending registrato");
      setForm({ ...form, importo: 0, descrizione: "" });
      load();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile registrare il prelievo"));
    }
  };

  const del = async (id) => {
    if (!window.confirm("Eliminare il prelievo?")) return;
    try {
      await api.delete(`/prelievi-vending/${id}`);
      toast.success("Prelievo eliminato");
      load();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile eliminare il prelievo"));
    }
  };

  return (
    <Layout title="Prelievi Vending" subtitle="registrazione del contante prelevato dalla vending">
      <div className="grid grid-cols-1 gap-4 mb-6 md:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="Vendite vending in contanti" value={formatEur(data.venditeVendingContanti)} tone="info" />
        <KpiCard label="Prelievo da vending" value={formatEur(data.prelievoVending)} />
        <KpiCard label="Giacenza vending" value={formatEur(data.giacenzaVending)} tone={data.giacenzaVending < 0 ? "danger" : "success"} />
        <Link
          to="/scontrini-vending"
          data-testid="scontrini-vending-link"
          aria-label={`Apri Scontrini Vending, totale ${formatEur(data.scontriniVending)}`}
          className="group block min-w-0"
        >
          <Card className="h-full min-w-0 p-4 transition-all duration-200 group-hover:-translate-y-[2px] group-hover:border-slate-300 group-hover:shadow-md lg:p-5">
            <div className="flex items-start justify-between gap-3">
              <div className="overline min-h-[2.5rem] break-words leading-tight">Scontrini vending</div>
              <ArrowRight size={18} weight="bold" className="shrink-0 text-slate-400 transition-transform group-hover:translate-x-1 group-hover:text-slate-700" />
            </div>
            <div className="kpi-value mt-2 whitespace-nowrap text-[clamp(1.35rem,1.8vw,2.15rem)] leading-tight text-blue-800 tabular-nums">
              {formatEur(data.scontriniVending)}
            </div>
          </Card>
        </Link>
      </div>

      <Card className="p-4 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
          <input data-testid="prelievo-vending-data" type="date" value={form.data} onChange={e => setForm({...form, data: e.target.value})} className="border rounded-md px-3 py-2 text-sm" />
          <input data-testid="prelievo-vending-importo" type="number" min="0" step="0.01" placeholder="Importo €" value={form.importo} onChange={e => setForm({...form, importo: parseFloat(e.target.value) || 0})} className="border rounded-md px-3 py-2 text-sm font-mono" />
          <input data-testid="prelievo-vending-desc" placeholder="Descrizione" value={form.descrizione} onChange={e => setForm({...form, descrizione: e.target.value})} className="border rounded-md px-3 py-2 text-sm" />
          <input data-testid="prelievo-vending-operatore" placeholder="Operatore" value={form.operatore} onChange={e => setForm({...form, operatore: e.target.value})} className="border rounded-md px-3 py-2 text-sm" />
          <button data-testid="prelievo-vending-save-btn" onClick={save} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors">Registra</button>
        </div>
      </Card>

      <div className="flex items-center gap-3 mb-4">
        <SearchBar data-testid="prelievi-vending-search" value={query} onChange={setQuery} placeholder="Cerca descrizione o operatore..." className="flex-1 max-w-md" />
        <span className="text-sm text-slate-500">{data.movimenti.length} prelievi</span>
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
                <tr key={r.id} data-testid={`prelievo-vending-row-${r.id}`}>
                  <td className="font-mono text-xs">{(r.data || "").slice(0, 10)}</td>
                  <td>{r.descrizione}</td>
                  <td className="text-xs text-slate-500">{r.operatore}</td>
                  <td className="font-mono text-right font-semibold text-red-600">− {formatEur(r.importo)}</td>
                  <td className="text-right"><button onClick={() => del(r.id)} className="text-red-600 text-xs" aria-label="Elimina prelievo">✕</button></td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={5} className="text-center py-8 text-slate-400">Nessun prelievo vending</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </Layout>
  );
}
