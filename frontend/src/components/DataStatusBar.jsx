import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { Badge, formatNum } from "./UI";

export default function DataStatusBar({ compact = false }) {
  const [status, setStatus] = useState(null);
  const backupWasInProgress = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let timer;
    const loadStatus = () => {
      api.get("/data-status").then(r => {
        if (cancelled) return;
        const next = r.data;
        setStatus(next);
        if (backupWasInProgress.current && next.daily_backup && !next.daily_backup.in_progress) {
          window.dispatchEvent(new Event("backup-daily-ready"));
        }
        backupWasInProgress.current = Boolean(next.daily_backup?.in_progress);
        if (next.daily_backup?.in_progress) {
          timer = window.setTimeout(loadStatus, 2500);
        }
      }).catch(() => {});
    };
    loadStatus();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  if (!status) return null;
  const delay = status.sales_data_delay_days;
  const stale = delay !== null && delay !== undefined && delay > 2;
  const latestImport = status.latest_import;
  return (
    <div className={`mb-5 rounded-md border px-4 py-3 text-sm ${stale ? "border-amber-300 bg-amber-50 text-amber-950" : "border-slate-200 bg-white text-slate-700"}`} data-testid="data-status-bar">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={stale ? "warning" : "ok"}>{stale ? "Dati da aggiornare" : "Dati aggiornati"}</Badge>
        {status.daily_backup_error ? (
          <Badge tone="warning">Backup giornaliero da verificare</Badge>
        ) : status.daily_backup?.in_progress ? (
          <Badge tone="neutral">Backup giornaliero in corso</Badge>
        ) : status.daily_backup ? (
          <Badge tone="ok">Backup giornaliero attivo</Badge>
        ) : null}
        <span>Ultima vendita: <b>{status.latest_sales_day || "non disponibile"}</b></span>
        {!compact && delay !== null && delay !== undefined && <span>Ritardo: <b>{delay} gg</b></span>}
        {!compact && <span>Prodotti: <b>{formatNum(status.counts?.prodotti)}</b></span>}
        {!compact && <span>Vendite storiche: <b>{formatNum(status.counts?.db_storico_vend)}</b></span>}
        {latestImport && <span>Ultimo import: <b>{latestImport.file}</b></span>}
        {status.latest_backup && <span>Ultimo checkpoint: <b>{new Date(status.latest_backup.created_at).toLocaleString("it-IT")}</b></span>}
      </div>
    </div>
  );
}
