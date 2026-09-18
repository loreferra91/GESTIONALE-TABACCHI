import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api } from "../lib/api";
import { Card, KpiCard, Badge, formatEur, formatNum } from "../components/UI";

export default function Magazzino() {
  const [data, setData] = useState(null);
  const [q, setQ] = useState("");
  const [stato, setStato] = useState("");
  useEffect(() => { api.get("/pivot").then(r => setData(r.data)); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const rows = (data?.righe || []).filter(r =>
    (!q || r.codice.toLowerCase().includes(q.toLowerCase()) || r.descrizione.toLowerCase().includes(q.toLowerCase())) &&
    (!stato || r.stato === stato)
  );

  const k = data?.kpi || {};

  return (
    <Layout title="Magazzino / Pivot" subtitle="analisi giacenze & valorizzazione">
      <div className="grid grid-cols-2 sm:grid-cols-3 2xl:grid-cols-6 gap-3 mb-6">
        <KpiCard label={`Val. acquistato (−${((k.aggio_pct||0)*100).toFixed(0)}% aggio)`} value={formatEur(k.valore_acquistato)} />
        <KpiCard label="Val. venduto" value={formatEur(k.valore_venduto)} tone="success" />
        <KpiCard label="Margine lordo" value={formatEur(k.margine_lordo)} tone={k.margine_lordo >= 0 ? "success" : "danger"} />
        <KpiCard label="Val. giacenza" value={formatEur(k.valore_giacenza)} />
        <KpiCard label="Da riordinare" value={formatNum(k.da_riordinare)} tone={k.da_riordinare ? "danger" : "default"} />
        <KpiCard label="Fermi / Lenti" value={`${formatNum(k.prodotti_fermi)} / ${formatNum(k.lenti_oltre_6_mesi)}`} tone="warning" />
      </div>

      <div className="flex gap-3 mb-4">
        <input data-testid="mag-search" value={q} onChange={e => setQ(e.target.value)} placeholder="Cerca…" className="border rounded-md px-3 py-2 text-sm flex-1 max-w-md" />
        <select data-testid="mag-filter-stato" value={stato} onChange={e => setStato(e.target.value)} className="border rounded-md px-3 py-2 text-sm">
          <option value="">Tutti gli stati</option>
          <option>OK</option><option>ESAURITO</option><option>FERMO</option><option>LENTO</option>
        </select>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <th>Codice</th><th>Descrizione</th>
                <th className="text-right">Acq.</th>
                <th className="text-right">Vend. Neg.</th>
                <th className="text-right">Vend. Vend.</th>
                <th className="text-right">Giac. Neg.</th>
                <th className="text-right">Giac. Vend.</th>
                <th className="text-right">Giac. Tot.</th>
                <th className="text-right">Prezzo</th>
                <th className="text-right">Val. Giac.</th>
                <th className="text-right">Mesi smalt.</th>
                <th>Stato</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.codice} data-testid={`mag-row-${r.codice}`}>
                  <td className="font-mono">{r.codice}</td>
                  <td className="max-w-xs truncate">{r.descrizione}</td>
                  <td className="font-mono text-right">{formatNum(r.acq)}</td>
                  <td className="font-mono text-right">{formatNum(r.vend_negozio)}</td>
                  <td className="font-mono text-right">{formatNum(r.vend_vending)}</td>
                  <td className={`font-mono text-right ${r.giac_negozio < 0 ? 'text-red-600 font-bold' : ''}`}>{formatNum(r.giac_negozio)}</td>
                  <td className="font-mono text-right">{formatNum(r.giac_vending)}</td>
                  <td className="font-mono text-right font-semibold">{formatNum(r.giac_totale)}</td>
                  <td className="font-mono text-right">{formatEur(r.prezzo)}</td>
                  <td className="font-mono text-right font-semibold">{formatEur(r.tot_giacenza)}</td>
                  <td className="font-mono text-right">{r.mesi_smaltimento}</td>
                  <td><Badge tone={r.stato === "OK" ? "ok" : r.stato === "ESAURITO" ? "error" : "warning"}>{r.stato}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </Layout>
  );
}
