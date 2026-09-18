import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api } from "../lib/api";
import { KpiCard, Card, formatEur, formatNum, Badge } from "../components/UI";

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [pivot, setPivot] = useState(null);

  useEffect(() => {
    api.get("/dashboard").then((r) => setData(r.data));
    api.get("/pivot").then((r) => setPivot(r.data));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const k = data?.kpi || {};

  return (
    <Layout title="Dashboard operativa" subtitle="panoramica magazzino & vendita">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <KpiCard label={`Valore acquistato (netto aggio ${((k.aggio_pct||0)*100).toFixed(0)}%)`} value={formatEur(k.valore_acquistato)} />
        <KpiCard label="Valore venduto (retail)" value={formatEur(k.valore_venduto)} tone="success" />
        <KpiCard label="Margine lordo stimato" value={formatEur(k.margine_lordo)} tone={k.margine_lordo >= 0 ? "success" : "danger"} />
        <KpiCard label="Valore giacenza (a costo)" value={formatEur(k.valore_giacenza)} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <KpiCard label="Prodotti totali" value={formatNum(k.prodotti_totali)} />
        <KpiCard label="Da riordinare" value={formatNum(k.da_riordinare)} tone={k.da_riordinare ? "danger" : "default"} />
        <KpiCard label="Prodotti fermi" value={formatNum(k.prodotti_fermi)} tone={k.prodotti_fermi ? "warning" : "default"} />
        <KpiCard label="Pezzi a magazzino" value={formatNum(k.pezzi_magazzino)} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card className="p-6">
          <div className="overline">Vendite oggi</div>
          <div className="kpi-value text-4xl mt-2 text-slate-900">{formatEur(data?.vendite_oggi?.importo)}</div>
          <div className="text-sm text-slate-500 mt-1 font-mono">{formatNum(data?.vendite_oggi?.pezzi)} pezzi</div>
        </Card>
        <Card className="p-6">
          <div className="overline">Saldo cassa</div>
          <div className={`kpi-value text-4xl mt-2 ${data?.saldo_cassa < 0 ? 'text-red-600' : 'text-slate-900'}`}>{formatEur(data?.saldo_cassa)}</div>
          <div className="text-sm text-slate-500 mt-1">Movimenti entrata − uscita</div>
        </Card>
        <Card className="p-6">
          <div className="overline">Vending</div>
          <div className="kpi-value text-4xl mt-2 text-slate-900">
            {formatNum(data?.vending_da_caricare)}
            <span className="text-lg text-slate-400 ml-2">/ {formatNum(data?.vending_totale)}</span>
          </div>
          <div className="text-sm text-slate-500 mt-1">Colonne da caricare</div>
        </Card>
      </div>

      <div className="mt-8">
        <div className="flex items-baseline justify-between mb-3">
          <h2 className="font-heading font-black text-xl text-slate-900">Top prodotti per valore giacenza</h2>
          <Badge tone="info">TOP 10</Badge>
        </div>
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="data-table w-full">
              <thead>
                <tr>
                  <th>Codice</th>
                  <th>Descrizione</th>
                  <th className="text-right">Giacenza</th>
                  <th className="text-right">Prezzo</th>
                  <th className="text-right">Valore</th>
                  <th>Stato</th>
                </tr>
              </thead>
              <tbody>
                {(pivot?.righe || []).slice(0, 10).map((r) => (
                  <tr key={r.codice} data-testid={`dash-row-${r.codice}`}>
                    <td className="font-mono">{r.codice}</td>
                    <td className="max-w-md truncate">{r.descrizione}</td>
                    <td className="font-mono text-right">{formatNum(r.giac_totale)}</td>
                    <td className="font-mono text-right">{formatEur(r.prezzo)}</td>
                    <td className="font-mono text-right font-semibold">{formatEur(r.tot_giacenza)}</td>
                    <td><Badge tone={r.stato === "OK" ? "ok" : r.stato === "ESAURITO" ? "error" : "warning"}>{r.stato}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </Layout>
  );
}
