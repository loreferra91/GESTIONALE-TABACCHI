import { useDeferredValue, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  CheckCircle,
  Clock,
  Coins,
  PauseCircle,
  ShoppingCart,
  WarningCircle,
} from "@phosphor-icons/react";
import { api, apiErrorMessage } from "../lib/api";
import { Badge, Card, formatEur, formatNum } from "./UI";
import { SearchBar } from "../lib/tableHooks";
import { buildStockAnalysisRows } from "../lib/stockAnalysis";

function SummaryCard({ icon: Icon, label, value, tone = "default" }) {
  const tones = {
    default: "text-slate-950 bg-amber-50 text-amber-700",
    danger: "text-red-600 bg-red-50 text-red-600",
    warning: "text-amber-700 bg-amber-50 text-amber-700",
  };
  const [valueClass, iconClass, iconColor] = tones[tone].split(" ");
  return (
    <Card className="flex min-w-0 items-center gap-3 p-4 lg:p-5">
      <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-lg ${iconClass} ${iconColor}`}>
        <Icon size={23} weight="duotone" />
      </div>
      <div className="min-w-0">
        <div className="text-sm font-semibold text-slate-600">{label}</div>
        <div className={`mt-1 whitespace-nowrap font-heading text-xl font-black tabular-nums 2xl:text-2xl ${valueClass}`} title={String(value)}>{value}</div>
      </div>
    </Card>
  );
}

export default function StockAnalysis() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const deferredQuery = useDeferredValue(query);

  useEffect(() => {
    let active = true;
    setLoading(true);
    api.get("/auto-order", { timeout: 20000 })
      .then(response => {
        if (active) setData(response.data);
      })
      .catch(requestError => {
        if (active) setError(apiErrorMessage(requestError, "Impossibile calcolare l'analisi scorte"));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, []);

  const rows = useMemo(() => buildStockAnalysisRows(data), [data]);
  const visibleRows = useMemo(() => {
    const needle = deferredQuery.trim().toLocaleLowerCase("it-IT");
    return rows.filter(row => {
      const matchesQuery = !needle || [row.codice, row.descrizione, row.categoria]
        .some(value => String(value || "").toLocaleLowerCase("it-IT").includes(needle));
      const matchesStatus = !status || row.analysisStatus.label === status;
      return matchesQuery && matchesStatus;
    });
  }, [deferredQuery, rows, status]);

  const value = rows.reduce((sum, row) => sum + row.valore_scorta, 0);
  const reorderCount = rows.filter(row => ["ESAURITO", "SCORTA BASSA"].includes(row.analysisStatus.label)).length;
  const anomalyCount = rows.filter(row => row.analysisStatus.label === "ANOMALIA").length;
  const stationaryCount = rows.filter(row => row.analysisStatus.label === "FERMO").length;
  const slowCount = rows.filter(row => row.analysisStatus.label === "LENTO").length;
  const period = data?.finestra_lunga_gg || 30;

  if (loading) {
    return <Card className="p-10 text-center text-sm text-slate-500">Calcolo dell'analisi scorte…</Card>;
  }

  if (error) {
    return (
      <Card className="border-red-200 bg-red-50 p-6 text-red-800">
        <div className="flex items-center gap-2 font-bold"><WarningCircle size={20} /> Analisi non disponibile</div>
        <p className="mt-2 text-sm">{error}</p>
      </Card>
    );
  }

  return (
    <section aria-labelledby="stock-analysis-title">
      <h2 id="stock-analysis-title" className="sr-only">Analisi scorte</h2>

      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-5">
        <SummaryCard icon={Coins} label="Valore scorte" value={formatEur(value)} />
        <SummaryCard icon={ShoppingCart} label="Da riordinare" value={formatNum(reorderCount)} tone={reorderCount ? "danger" : "default"} />
        <SummaryCard icon={WarningCircle} label="Anomalie" value={formatNum(anomalyCount)} tone={anomalyCount ? "danger" : "default"} />
        <SummaryCard icon={PauseCircle} label="Prodotti fermi" value={formatNum(stationaryCount)} tone="warning" />
        <SummaryCard icon={Clock} label="Prodotti lenti" value={formatNum(slowCount)} tone="warning" />
      </div>

      <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-center">
        <SearchBar value={query} onChange={setQuery} placeholder="Cerca prodotto…" className="min-w-0 flex-1 lg:max-w-md" data-testid="stock-analysis-search" />
        <select value={status} onChange={event => setStatus(event.target.value)} className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700" data-testid="stock-analysis-status">
          <option value="">Tutti gli stati</option>
          <option value="ANOMALIA">Anomalie</option>
          <option value="ESAURITO">Esauriti</option>
          <option value="SCORTA BASSA">Scorta bassa</option>
          <option value="DA MONITORARE">Da monitorare</option>
          <option value="FERMO">Fermi</option>
          <option value="LENTO">Lenti</option>
          <option value="OK">OK</option>
        </select>
        <div className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600">
          Vendite ultimi <b>{period} giorni</b>
        </div>
        <span className="text-sm text-slate-500 lg:ml-auto">{formatNum(visibleRows.length)} prodotti</span>
        <Link to="/auto-order" className="inline-flex items-center justify-center gap-2 rounded-md bg-amber-500 px-4 py-2 text-sm font-bold text-slate-950 transition-colors hover:bg-amber-400">
          Apri Auto-Order <ArrowRight size={16} weight="bold" />
        </Link>
      </div>

      <div className="mb-3 flex items-start gap-2 rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-sm text-blue-900">
        <CheckCircle size={18} className="mt-0.5 shrink-0 text-blue-600" />
        <span>La copertura usa la giacenza del negozio; il valore scorte include negozio e vending.</span>
      </div>

      <Card className="overflow-hidden">
        <div className="max-h-[62vh] overflow-auto">
          <table className="data-table w-full min-w-[1100px]" data-testid="stock-analysis-table">
            <thead>
              <tr>
                <th>Prodotto</th>
                <th className="text-right">Negozio</th>
                <th className="text-right">Vending</th>
                <th className="text-right">Totale</th>
                <th className="text-right">Vendite {period} gg</th>
                <th className="text-right">Copertura</th>
                <th className="text-right">Valore scorta</th>
                <th>Stato</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map(row => (
                <tr key={row.codice}>
                  <td className="wrap min-w-64">
                    <div className="font-semibold text-slate-900">{row.descrizione}</div>
                    <div className="mt-0.5 text-xs text-slate-500"><span className="font-mono">{row.codice}</span>{row.categoria ? ` · ${row.categoria}` : ""}</div>
                  </td>
                  <td className="text-right font-mono font-semibold">{formatNum(row.magazzino_reale_lordo)}</td>
                  <td className="text-right font-mono">{formatNum(row.giacenza_vending)}</td>
                  <td className="text-right font-mono font-semibold">{formatNum(row.giacenza)}</td>
                  <td className="text-right font-mono">{formatNum(row.venduto_lungo)}</td>
                  <td className="text-right font-mono font-semibold">{row.copertura_gg === null || row.copertura_gg === undefined ? "—" : `${formatNum(Math.round(row.copertura_gg))} gg`}</td>
                  <td className="text-right font-mono">{formatEur(row.valore_scorta)}</td>
                  <td><Badge tone={row.analysisStatus.tone}>{row.analysisStatus.label}</Badge></td>
                  <td className="text-right"><Link to="/auto-order" className="inline-flex items-center gap-1 text-xs font-bold text-amber-700 hover:text-amber-900">Dettagli <ArrowRight size={13} /></Link></td>
                </tr>
              ))}
              {!visibleRows.length ? <tr><td colSpan={9} className="py-10 text-center text-slate-500">Nessun prodotto corrisponde ai filtri.</td></tr> : null}
            </tbody>
          </table>
        </div>
      </Card>

      {reorderCount > 0 ? (
        <div className="mt-4 flex flex-col gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2 font-bold text-amber-950"><WarningCircle size={20} /> {formatNum(reorderCount)} prodotti richiedono attenzione</div>
          <Link to="/auto-order" className="inline-flex items-center justify-center gap-2 rounded-md bg-amber-500 px-4 py-2 text-sm font-bold text-slate-950 hover:bg-amber-400">Vai ad Auto-Order <ArrowRight size={16} /></Link>
        </div>
      ) : null}
    </section>
  );
}
