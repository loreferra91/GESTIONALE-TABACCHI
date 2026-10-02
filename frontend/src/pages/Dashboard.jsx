import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api } from "../lib/api";
import { KpiCard, Card, formatEur, formatDashboardEur, formatSignedEur, formatNum, Badge } from "../components/UI";

function BalanceCard({ label, value, tone = "default", detail, signed = false, testId }) {
  const toneColor = {
    positive: "text-emerald-700",
    negative: "text-red-600",
    default: "text-slate-900",
  }[tone];

  return (
    <Card className="p-6 min-w-0">
      <div className="overline leading-4 min-h-8 break-words" data-testid={`${testId}-label`}>{label}</div>
      <div className={`kpi-value text-4xl mt-2 truncate tabular-nums ${toneColor}`} data-testid={testId}>
        {signed ? formatSignedEur(value) : formatDashboardEur(value)}
      </div>
      {detail && <div className="text-sm text-slate-500 mt-1">{detail}</div>}
    </Card>
  );
}

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [pivot, setPivot] = useState(null);

  useEffect(() => {
    api.get("/dashboard").then((r) => setData(r.data));
    api.get("/pivot").then((r) => setPivot(r.data));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const k = data?.kpi || {};
  const saldi = data?.saldi || {};
  const differenza = saldi.differenzaCassaVendingContanti;
  const differenzaTone = differenza > 0 ? "positive" : differenza < 0 ? "negative" : "default";

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

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-6">
        <BalanceCard label="Saldo vending" value={saldi.saldoVendingTotale} testId="saldo-vending-totale" />
        <BalanceCard label="Saldo vending contanti" value={saldi.saldoVendingContanti} testId="saldo-vending-contanti" />
        <BalanceCard
          label="Saldo vending bancomat / pagamenti elettronici"
          value={saldi.saldoVendingElettronico}
          testId="saldo-vending-elettronico"
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <BalanceCard
          label="Saldo cassa"
          value={saldi.saldoCassa}
          tone={saldi.saldoCassa < 0 ? "negative" : "default"}
          detail="Movimenti entrata − uscita"
          testId="saldo-cassa"
        />
        <BalanceCard
          label="Prelievi"
          value={saldi.totalePrelievi}
          tone={saldi.totalePrelievi > 0 ? "negative" : "default"}
          testId="totale-prelievi"
        />
        <BalanceCard
          label="Differenza saldo cassa - saldo vending contanti"
          value={differenza}
          tone={differenzaTone}
          signed
          testId="differenza-cassa-vending"
        />
        <Card className="p-6">
          <div className="overline leading-4 min-h-8">Vending</div>
          <div className="kpi-value text-4xl mt-2 text-slate-900 tabular-nums">
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
