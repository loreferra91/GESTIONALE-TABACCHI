import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api } from "../lib/api";
import { Card, KpiCard, Badge, formatEur, formatNum } from "../components/UI";
import { useSortSearch, Th, SearchBar } from "../lib/tableHooks";

export default function Magazzino() {
  const [data, setData] = useState(null);
  const [stato, setStato] = useState("");
  useEffect(() => { api.get("/pivot").then(r => setData(r.data)); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = (data?.righe || []).filter(r => !stato || r.stato === stato);
  const { rows, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(filtered, {
    initial: "tot_giacenza", dir: "desc",
    searchFields: ["codice", "descrizione", "categoria", "stato"],
  });

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
        <SearchBar data-testid="mag-search" value={query} onChange={setQuery} placeholder="Cerca codice, descrizione, categoria o stato…" className="flex-1 max-w-md" />
        <select data-testid="mag-filter-stato" value={stato} onChange={e => setStato(e.target.value)} className="border rounded-md px-3 py-2 text-sm">
          <option value="">Tutti gli stati</option>
          <option>OK</option><option>ESAURITO</option><option>FERMO</option><option>LENTO</option>
        </select>
        <span className="text-sm text-slate-500 self-center">{rows.length} risultati</span>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <Th sortKey="codice" currentKey={sortKey} dir={sortDir} onClick={toggle}>Codice</Th>
                <Th sortKey="descrizione" currentKey={sortKey} dir={sortDir} onClick={toggle}>Descrizione</Th>
                <Th sortKey="acq" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Acq.</Th>
                <Th sortKey="vend_negozio" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Vend. Neg.</Th>
                <Th sortKey="vend_vending" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Vend. Vend.</Th>
                <Th sortKey="giac_negozio" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Giac. Neg.</Th>
                <Th sortKey="giac_vending" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Giac. Vend.</Th>
                <Th sortKey="giac_totale" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Giac. Tot.</Th>
                <Th sortKey="prezzo" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Prezzo</Th>
                <Th sortKey="tot_giacenza" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Val. Giac.</Th>
                <Th sortKey="mesi_smaltimento" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Mesi smalt.</Th>
                <Th sortKey="stato" currentKey={sortKey} dir={sortDir} onClick={toggle}>Stato</Th>
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
