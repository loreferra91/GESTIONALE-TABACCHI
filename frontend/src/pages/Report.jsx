import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { toast } from "sonner";
import Layout from "../components/Layout";
import { Card, KpiCard, formatEur, formatNum } from "../components/UI";
import { api, apiErrorMessage } from "../lib/api";

function shortDate(value) {
  if (!value) return "—";
  const [year, month, day] = String(value).slice(0, 10).split("-");
  return day && month && year ? `${day}/${month}` : value;
}

function fullDate(value) {
  if (!value) return "—";
  return new Date(`${String(value).slice(0, 10)}T12:00:00`).toLocaleDateString("it-IT");
}

function csvCell(value) {
  return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

export default function Report() {
  const [daily, setDaily] = useState(null);
  const [sales, setSales] = useState(null);
  const [days, setDays] = useState(7);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([
      api.get("/report/giornaliero"),
      api.get(`/report/vendite?giorni=${days}`),
    ])
      .then(([dailyResponse, salesResponse]) => {
        if (!active) return;
        setDaily(dailyResponse.data);
        setSales(salesResponse.data);
      })
      .catch((error) => {
        if (active) toast.error(apiErrorMessage(error, "Impossibile generare il report"));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [days, refresh]);

  const chartData = useMemo(() => sales?.serie || [], [sales]);
  const variation = sales?.variazione_pct;
  const variationText = variation === null || variation === undefined
    ? "Confronto non disponibile"
    : `${variation >= 0 ? "+" : ""}${variation.toLocaleString("it-IT")}%`;

  const exportCsv = () => {
    if (!sales?.serie?.length) return;
    const rows = [
      ["Data", "Negozio", "Vending", "Totale"],
      ...sales.serie.map((row) => [row.data, row.negozio, row.vending, row.totale]),
    ];
    const csv = `\uFEFF${rows.map((row) => row.map(csvCell).join(";")).join("\n")}`;
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `report-vendite-${sales.data_da}-${sales.data_a}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const k = daily?.dashboard?.kpi || {};
  const todaySales = daily?.dashboard?.vendite_oggi || {};
  const ao = daily?.auto_order || {};
  const status = daily?.status || {};

  return (
    <Layout title="Report vendite" subtitle="analisi settimanale e mensile" actions={(
      <button onClick={() => window.print()} className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-50">Stampa</button>
    )}>
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between print:hidden">
        <div className="inline-flex w-full rounded-md border border-slate-200 bg-white p-1 sm:w-auto" aria-label="Periodo del report">
          {[{ value: 7, label: "7 giorni" }, { value: 30, label: "30 giorni" }].map((period) => (
            <button key={period.value} type="button" data-testid={`report-period-${period.value}`} aria-pressed={days === period.value} onClick={() => setDays(period.value)} className={`flex-1 rounded px-4 py-2 text-sm font-semibold transition-colors ${days === period.value ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-50"}`}>
              {period.label}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          <button type="button" data-testid="report-export-csv" onClick={exportCsv} disabled={loading || !sales?.serie?.length} className="flex-1 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold hover:bg-slate-50 disabled:opacity-40 sm:flex-none">Esporta CSV</button>
          <button type="button" onClick={() => setRefresh((value) => value + 1)} disabled={loading} className="flex-1 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm hover:bg-slate-50 disabled:opacity-40 sm:flex-none">{loading ? "Aggiorno…" : "Aggiorna"}</button>
        </div>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard label={`Vendite · ${days} giorni`} value={formatEur(sales?.totale)} tone="success" />
        <KpiCard label="Media giornaliera" value={formatEur(sales?.media_giornaliera)} />
        <KpiCard label="Pezzi venduti" value={formatNum(sales?.pezzi)} />
        <KpiCard label="Confronto periodo precedente" value={variationText} tone={variation === null || variation === undefined ? "default" : variation >= 0 ? "success" : "danger"} />
      </div>

      <Card className="mb-6 p-5 lg:p-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="overline">Andamento per canale</div>
            <h2 className="mt-1 font-heading text-lg font-black text-slate-900">{fullDate(sales?.data_da)} – {fullDate(sales?.data_a)}</h2>
          </div>
          <div className="text-sm text-slate-500">{formatNum(sales?.giorni_con_vendite)} giorni con vendite</div>
        </div>
        <div className="mt-5 h-72 w-full" data-testid="report-sales-chart">
          <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={288} initialDimension={{ width: 1000, height: 288 }}>
            <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#E2E8F0" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="data" tickFormatter={shortDate} tick={{ fontSize: 11, fill: "#64748B" }} axisLine={false} tickLine={false} minTickGap={20} />
              <YAxis tick={{ fontSize: 11, fill: "#64748B" }} tickFormatter={(value) => `€${value}`} axisLine={false} tickLine={false} width={58} />
              <Tooltip labelFormatter={fullDate} formatter={(value, name) => [formatEur(value), name === "negozio" ? "Negozio" : "Vending"]} contentStyle={{ border: "1px solid #E2E8F0", borderRadius: 6, boxShadow: "0 8px 24px rgba(15, 23, 42, .08)" }} />
              <Legend formatter={(value) => value === "negozio" ? "Negozio" : "Vending"} />
              <Bar dataKey="negozio" stackId="sales" fill="#0F2B5B" radius={[3, 3, 0, 0]} />
              <Bar dataKey="vending" stackId="sales" fill="#10B981" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <div className="mb-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="p-5 lg:col-span-2">
          <h2 className="font-heading text-lg font-black">Prodotti principali</h2>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full table-fixed text-sm">
              <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500"><th className="py-2 pr-3">Prodotto</th><th className="py-2 pr-3 text-right">Pezzi</th><th className="py-2 text-right">Vendite</th></tr></thead>
              <tbody>
                {(sales?.prodotti || []).map((product) => (
                  <tr key={product.codice} className="border-b border-slate-100 last:border-0"><td className="w-1/2 py-3 pr-2"><span className="block truncate font-semibold text-slate-900" title={product.descrizione}>{product.descrizione}</span><span className="block truncate text-xs text-slate-400">{product.codice}</span></td><td className="w-1/5 py-3 pr-2 text-right tabular-nums">{formatNum(product.pezzi)}</td><td className="w-[30%] py-3 text-right font-semibold tabular-nums">{formatEur(product.importo)}</td></tr>
                ))}
              </tbody>
            </table>
            {!sales?.prodotti?.length && <div className="py-8 text-center text-sm text-slate-500">Nessuna vendita nel periodo.</div>}
          </div>
        </Card>

        <Card className="p-5">
          <h2 className="font-heading text-lg font-black">Pagamenti</h2>
          <div className="mt-3 space-y-1">
            {(sales?.pagamenti || []).map((payment) => (
              <div key={payment.nome} className="flex items-center justify-between gap-3 border-b border-slate-100 py-3 last:border-0"><div><div className="font-semibold text-slate-800">{payment.nome}</div><div className="text-xs text-slate-500">{formatNum(payment.pezzi)} pezzi</div></div><div className="font-semibold tabular-nums text-slate-900">{formatEur(payment.importo)}</div></div>
            ))}
            {!sales?.pagamenti?.length && <div className="py-8 text-center text-sm text-slate-500">Nessun pagamento disponibile.</div>}
          </div>
        </Card>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard label="Vendite oggi" value={formatEur(todaySales.importo)} tone="success" />
        <KpiCard label="Pezzi venduti oggi" value={formatNum(todaySales.pezzi)} />
        <KpiCard label="Saldo cassa" value={formatEur(daily?.dashboard?.saldo_cassa)} tone={(daily?.dashboard?.saldo_cassa || 0) < 0 ? "danger" : "default"} />
        <KpiCard label="Auto-Order" value={formatEur(ao.totale)} tone="warning" />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <h2 className="mb-3 font-heading text-lg font-black">Stato dati</h2>
          <dl className="grid grid-cols-2 gap-3 text-sm"><dt className="text-slate-500">Ultima vendita</dt><dd className="font-semibold">{status.latest_sales_day || "—"}</dd><dt className="text-slate-500">Ritardo dati</dt><dd className="font-semibold">{status.sales_data_delay_days ?? "—"} gg</dd><dt className="text-slate-500">Prodotti</dt><dd className="font-semibold">{formatNum(status.counts?.prodotti)}</dd><dt className="text-slate-500">Vendite storiche</dt><dd className="font-semibold">{formatNum(status.counts?.db_storico_vend)}</dd><dt className="text-slate-500">Ultimo import</dt><dd className="font-semibold">{status.latest_import?.file || "—"}</dd><dt className="text-slate-500">Ultimo backup</dt><dd className="font-semibold">{status.latest_backup ? new Date(status.latest_backup.created_at).toLocaleString("it-IT") : "—"}</dd></dl>
        </Card>
        <Card className="p-5">
          <h2 className="mb-3 font-heading text-lg font-black">Controlli operativi</h2>
          <dl className="grid grid-cols-2 gap-3 text-sm"><dt className="text-slate-500">Anomalie alte</dt><dd className="font-semibold text-red-700">{formatNum(daily?.anomalie?.alta)}</dd><dt className="text-slate-500">Anomalie medie</dt><dd className="font-semibold text-amber-700">{formatNum(daily?.anomalie?.media)}</dd><dt className="text-slate-500">Righe da ordinare</dt><dd className="font-semibold">{formatNum(ao.righe)}</dd><dt className="text-slate-500">Da controllare</dt><dd className="font-semibold">{formatNum(ao.da_controllare)}</dd><dt className="text-slate-500">Vending da caricare</dt><dd className="font-semibold">{formatNum(daily?.dashboard?.vending_da_caricare)} / {formatNum(daily?.dashboard?.vending_totale)}</dd><dt className="text-slate-500">Prodotti da riordinare</dt><dd className="font-semibold">{formatNum(k.da_riordinare)}</dd></dl>
        </Card>
      </div>
    </Layout>
  );
}
