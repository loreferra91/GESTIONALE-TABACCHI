import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Badge, formatNum } from "./UI";

export default function DataStatusBar({ compact = false }) {
  const [status, setStatus] = useState(null);

  useEffect(() => {
    api.get("/data-status").then(r => setStatus(r.data)).catch(() => {});
  }, []);

  if (!status) return null;
  const delay = status.sales_data_delay_days;
  const stale = delay !== null && delay !== undefined && delay > 2;
  const latestImport = status.latest_import;
  return (
    <div className={`mb-5 rounded-md border px-4 py-3 text-sm ${stale ? "border-amber-300 bg-amber-50 text-amber-950" : "border-slate-200 bg-white text-slate-700"}`} data-testid="data-status-bar">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={stale ? "warning" : "ok"}>{stale ? "Dati da aggiornare" : "Dati aggiornati"}</Badge>
        <span>Ultima vendita: <b>{status.latest_sales_day || "non disponibile"}</b></span>
        {!compact && delay !== null && delay !== undefined && <span>Ritardo: <b>{delay} gg</b></span>}
        {!compact && <span>Prodotti: <b>{formatNum(status.counts?.prodotti)}</b></span>}
        {!compact && <span>Vendite storiche: <b>{formatNum(status.counts?.db_storico_vend)}</b></span>}
        {latestImport && <span>Ultimo import: <b>{latestImport.file}</b></span>}
        {status.latest_backup && <span>Backup: <b>{new Date(status.latest_backup.created_at).toLocaleString("it-IT")}</b></span>}
      </div>
    </div>
  );
}
