import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api, apiErrorMessage } from "../lib/api";
import { Card, KpiCard, formatEur, formatNum } from "../components/UI";
import { toast } from "sonner";

export default function Report() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.get("/report/giornaliero");
      setData(r.data);
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile generare il report"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const k = data?.dashboard?.kpi || {};
  const vendite = data?.dashboard?.vendite_oggi || {};
  const ao = data?.auto_order || {};
  const status = data?.status || {};

  return (
    <Layout title="Report giornaliero" subtitle="riepilogo stampabile operativo" actions={
      <button onClick={() => window.print()} className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm hover:bg-slate-50">Stampa</button>
    }>
      <div className="flex justify-end mb-4">
        <button onClick={load} disabled={loading} className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm hover:bg-slate-50 disabled:opacity-40">
          {loading ? "Aggiorno..." : "Aggiorna report"}
        </button>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <KpiCard label="Vendite oggi" value={formatEur(vendite.importo)} tone="success" />
        <KpiCard label="Pezzi venduti oggi" value={formatNum(vendite.pezzi)} />
        <KpiCard label="Saldo cassa" value={formatEur(data?.dashboard?.saldo_cassa)} tone={(data?.dashboard?.saldo_cassa || 0) < 0 ? "danger" : "default"} />
        <KpiCard label="Auto-Order" value={formatEur(ao.totale)} tone="warning" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card className="p-5">
          <h2 className="font-heading text-lg font-black mb-3">Stato dati</h2>
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <dt className="text-slate-500">Ultima vendita</dt><dd className="font-semibold">{status.latest_sales_day || "—"}</dd>
            <dt className="text-slate-500">Ritardo dati</dt><dd className="font-semibold">{status.sales_data_delay_days ?? "—"} gg</dd>
            <dt className="text-slate-500">Prodotti</dt><dd className="font-semibold">{formatNum(status.counts?.prodotti)}</dd>
            <dt className="text-slate-500">Vendite storiche</dt><dd className="font-semibold">{formatNum(status.counts?.db_storico_vend)}</dd>
            <dt className="text-slate-500">Ultimo import</dt><dd className="font-semibold">{status.latest_import?.file || "—"}</dd>
            <dt className="text-slate-500">Ultimo backup</dt><dd className="font-semibold">{status.latest_backup ? new Date(status.latest_backup.created_at).toLocaleString("it-IT") : "—"}</dd>
          </dl>
        </Card>

        <Card className="p-5">
          <h2 className="font-heading text-lg font-black mb-3">Controlli operativi</h2>
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <dt className="text-slate-500">Anomalie alte</dt><dd className="font-semibold text-red-700">{formatNum(data?.anomalie?.alta)}</dd>
            <dt className="text-slate-500">Anomalie medie</dt><dd className="font-semibold text-amber-700">{formatNum(data?.anomalie?.media)}</dd>
            <dt className="text-slate-500">Righe da ordinare</dt><dd className="font-semibold">{formatNum(ao.righe)}</dd>
            <dt className="text-slate-500">Da controllare</dt><dd className="font-semibold">{formatNum(ao.da_controllare)}</dd>
            <dt className="text-slate-500">Vending da caricare</dt><dd className="font-semibold">{formatNum(data?.dashboard?.vending_da_caricare)} / {formatNum(data?.dashboard?.vending_totale)}</dd>
            <dt className="text-slate-500">Prodotti da riordinare</dt><dd className="font-semibold">{formatNum(k.da_riordinare)}</dd>
          </dl>
        </Card>
      </div>
    </Layout>
  );
}
