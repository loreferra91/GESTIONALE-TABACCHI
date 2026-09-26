import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api, API } from "../lib/api";
import { Card, KpiCard, Badge, formatEur, formatNum } from "../components/UI";
import { useSortSearch, Th, SearchBar } from "../lib/tableHooks";
import { toast } from "sonner";

export default function AutoOrder() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const allRighe = data?.righe || [];
  const { rows: righe, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(allRighe, {
    initial: "totale", dir: "desc",
    searchFields: ["codice", "descrizione", "categoria", "motivo"],
  });

  const load = async () => {
    setLoading(true);
    const r = await api.get("/auto-order");
    setData(r.data);
    setLoading(false);
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const conferma = async () => {
    if (!window.confirm(`Confermi ${data.n_righe} ordini per un totale di € ${data.totale}?`)) return;
    const r = await api.post("/auto-order/conferma");
    toast.success(`${r.data.ordinati} ordini creati`);
    load();
  };

  const downloadPdf = async () => {
    const fornitore = window.prompt("Nome fornitore per il PDF:", "Fornitore") || "Fornitore";
    const url = `${API}/auto-order/pdf?fornitore=${encodeURIComponent(fornitore)}`;
    window.open(url, "_blank");
  };

  return (
    <Layout title="Auto-Order" subtitle="proposta ordini automatica basata su soglie">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <KpiCard label="Righe proposte" value={formatNum(data?.n_righe || 0)} />
        <KpiCard label="Totale ordine" value={formatEur(data?.totale || 0)} tone="success" />
        <KpiCard label="Soglia allert" value={`${((data?.parametri?.SOGLIA_ALLERT_PCT || 0) * 100).toFixed(0)}%`} />
        <KpiCard label="Finestra storico" value={`${data?.parametri?.GIORNI_STORICO_VEND || 30}gg`} />
      </div>

      <div className="flex gap-3 mb-4">
        <button data-testid="ao-refresh" onClick={load} className="border border-slate-300 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors">Ricalcola</button>
        <button data-testid="ao-pdf" onClick={downloadPdf} disabled={!allRighe.length} className="border border-slate-900 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors disabled:opacity-40">
          Esporta PDF fornitore
        </button>
        <button data-testid="ao-conferma" onClick={conferma} disabled={!allRighe.length} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors disabled:opacity-40">
          Conferma tutti gli ordini
        </button>
      </div>

      <div className="flex items-center gap-3 mb-4">
        <SearchBar data-testid="ao-search" value={query} onChange={setQuery} placeholder="Cerca codice, articolo, tipo o motivo…" className="flex-1 max-w-md" />
        <span className="text-sm text-slate-500">{righe.length} risultati</span>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <Th sortKey="codice" currentKey={sortKey} dir={sortDir} onClick={toggle}>Codice</Th>
                <Th sortKey="descrizione" currentKey={sortKey} dir={sortDir} onClick={toggle}>Articolo</Th>
                <Th sortKey="categoria" currentKey={sortKey} dir={sortDir} onClick={toggle}>Tipo</Th>
                <Th sortKey="giacenza_negozio" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Giac. negozio</Th>
                <Th sortKey="giacenza_vending" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Giac. vend.</Th>
                <Th sortKey="venduto_30gg" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Venduto 30gg</Th>
                <Th sortKey="media_ordini_storico" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Media ord.</Th>
                <Th sortKey="n_ordini_storici" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">N ord.</Th>
                <Th sortKey="lotto_ordine" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Lotto</Th>
                <Th sortKey="qta_da_ordinare" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Qta da ordinare</Th>
                <Th sortKey="prezzo" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Prezzo</Th>
                <Th sortKey="totale" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Totale</Th>
                <Th sortKey="motivo" currentKey={sortKey} dir={sortDir} onClick={toggle}>Motivo</Th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={13} className="text-center py-8 text-slate-400">Elaborazione…</td></tr>}
              {righe.map(r => (
                <tr key={r.codice} data-testid={`ao-row-${r.codice}`}>
                  <td className="font-mono">{r.codice}</td>
                  <td className="max-w-xs truncate">{r.descrizione}</td>
                  <td><Badge tone="info">{r.categoria}</Badge></td>
                  <td className={`font-mono text-right ${(r.giacenza_negozio||0) <= 0 ? 'text-red-600 font-bold' : ''}`}>{formatNum(r.giacenza_negozio ?? 0)}</td>
                  <td className="font-mono text-right">{formatNum(r.giacenza_vending ?? 0)}</td>
                  <td className="font-mono text-right">{formatNum(r.venduto_30gg)}</td>
                  <td className="font-mono text-right">{r.media_ordini_storico}</td>
                  <td className="font-mono text-right">{r.n_ordini_storici}</td>
                  <td className="font-mono text-right">{r.lotto_ordine}</td>
                  <td className="font-mono text-right font-bold">{r.qta_da_ordinare}</td>
                  <td className="font-mono text-right">{formatEur(r.prezzo)}</td>
                  <td className="font-mono text-right font-semibold">{formatEur(r.totale)}</td>
                  <td><Badge tone={r.motivo?.includes("FAST") ? "warning" : "info"}>{r.motivo}</Badge></td>
                </tr>
              ))}
              {!loading && righe.length === 0 && <tr><td colSpan={13} className="text-center py-8 text-slate-400">Nessun ordine proposto — tutte le scorte sono OK.</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </Layout>
  );
}
