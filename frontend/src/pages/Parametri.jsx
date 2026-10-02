import { useEffect, useState, useRef } from "react";
import Layout from "../components/Layout";
import { api, API, apiErrorMessage } from "../lib/api";
import { Card, Badge, formatNum } from "../components/UI";
import { toast } from "sonner";
import { UploadSimple, CheckCircle, WarningCircle } from "@phosphor-icons/react";
import ConfirmDialog from "../components/ConfirmDialog";

const DAY_MS = 24 * 60 * 60 * 1000;

function isValidIsoDate(year, month, day) {
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

function openingDateToIso(value) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) return "";

  const compact = String(Math.trunc(numericValue));
  if (/^\d{8}$/.test(compact)) {
    const day = Number(compact.slice(0, 2));
    const month = Number(compact.slice(2, 4));
    const year = Number(compact.slice(4));
    if (isValidIsoDate(year, month, day)) {
      return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    }
  }

  // Excel serial dates use 30/12/1899 as day zero.
  if (Number.isInteger(numericValue) && numericValue > 0 && numericValue < 100000) {
    return new Date(Date.UTC(1899, 11, 30) + numericValue * DAY_MS).toISOString().slice(0, 10);
  }

  return "";
}

function isoDateToCompactNumber(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const [, year, month, day] = match;
  if (!isValidIsoDate(Number(year), Number(month), Number(day))) return null;
  return Number(`${day}${month}${year}`);
}

export default function Parametri() {
  const [rows, setRows] = useState([]);
  const [importing, setImporting] = useState(false);
  const [importReport, setImportReport] = useState(null);
  const [history, setHistory] = useState([]);
  const [backups, setBackups] = useState([]);
  const [restoreTarget, setRestoreTarget] = useState(null);
  const [workingBackup, setWorkingBackup] = useState(false);
  const fileRef = useRef(null);

  const load = async () => {
    const r = await api.get("/parametri");
    setRows(r.data);
  };
  const loadAudit = async () => {
    const [h, b] = await Promise.all([
      api.get("/import/history"),
      api.get("/backup"),
    ]);
    setHistory(h.data || []);
    setBackups(b.data || []);
  };
  useEffect(() => { load(); loadAudit(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const update = async (nome, valore) => {
    try {
      await api.put(`/parametri/${nome}`, { valore: parseFloat(valore) });
      toast.success(`${nome} aggiornato`);
      load();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Errore aggiornamento"));
      load();
    }
  };

  const upload = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setImporting(true);
    setImportReport(null);
    const fd = new FormData();
    fd.append("file", f);
    try {
      const r = await fetch(`${API}/import/excel-full`, { method: "POST", body: fd });
      if (!r.ok) {
        const err = await r.text();
        throw new Error(err || r.statusText);
      }
      const j = await r.json();
      setImportReport(j);
      const t = j.totali;
      const tot = (t.prodotti_inseriti || 0) + (t.prodotti_aggiornati || 0) + (t.listino_inseriti || 0) + (t.listino_aggiornati || 0) + (t.vending_inseriti || 0) + (t.vending_aggiornati || 0);
      toast.success(`Import completato: ${tot} righe aggiornate su ${j.fogli_trovati.length} fogli. Backup creato.`);
      load();
      loadAudit();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Errore import Excel"));
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const createBackup = async () => {
    setWorkingBackup(true);
    try {
      await api.post("/backup/create", { label: "Backup manuale", reason: "manuale" });
      toast.success("Backup manuale creato");
      loadAudit();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Errore creazione backup"));
    } finally {
      setWorkingBackup(false);
    }
  };

  const restoreBackup = async () => {
    if (!restoreTarget) return;
    setWorkingBackup(true);
    try {
      const r = await api.post(`/backup/${restoreTarget.id}/restore`);
      toast.success(`Backup ripristinato. Backup pre-ripristino: ${r.data.pre_restore_backup}`);
      setRestoreTarget(null);
      load();
      loadAudit();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Errore ripristino backup"));
    } finally {
      setWorkingBackup(false);
    }
  };

  const T = importReport?.totali || {};

  return (
    <Layout title="Parametri di sistema" subtitle="configurazione motore & sincronizzazione Excel">
      <Card className="p-6 mb-6">
        <div className="flex items-start gap-4">
          <div className="w-12 h-12 bg-slate-900 text-white rounded-md flex items-center justify-center flex-shrink-0">
            <UploadSimple size={22} weight="bold" />
          </div>
          <div className="flex-1">
            <h2 className="font-heading font-black text-lg mb-1">Sincronizzazione da Excel</h2>
            <p className="text-sm text-slate-600 mb-3">
              Carica un file <code>.xlsm/.xlsx</code>: la web app legge automaticamente i fogli
              <b> RIEP_VENDITA, LISTINO ADM, RICARICA VENDING, STORICO_ORDINI, DB_STORICO_VEND, PARAMETRI</b> e allinea il DB con l'Excel (Excel = fonte di verità).
              <br/><span className="text-xs text-slate-500">Le righe presenti nel DB ma NON nell'Excel vengono conservate. I parametri custom (es. AGGIO_PCT) sono preservati.</span>
            </p>
            <div className="flex items-center gap-3">
              <input
                ref={fileRef}
                data-testid="import-file"
                type="file"
                accept=".xlsx,.xlsm"
                onChange={upload}
                disabled={importing}
                className="text-sm block file:mr-3 file:py-2 file:px-4 file:rounded-md file:border-0 file:bg-slate-900 file:text-white file:font-medium file:cursor-pointer hover:file:bg-slate-800 disabled:opacity-40"
              />
              {importing && <span className="text-sm text-slate-500 flex items-center gap-2"><div className="w-3 h-3 border-2 border-slate-400 border-t-slate-900 rounded-full animate-spin" /> Elaborazione…</span>}
            </div>
          </div>
        </div>

            {importReport && (
          <div className="mt-6 border-t border-slate-200 pt-5" data-testid="import-report">
            <div className="flex items-center gap-2 mb-3">
              <CheckCircle size={20} weight="fill" className="text-emerald-600" />
              <span className="font-heading font-black text-base">Report Import — {importReport.file}</span>
            </div>
            {importReport.backup_id && (
              <div className="mb-3 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-800">
                Backup automatico creato prima dell'import: <b>{importReport.backup_id}</b>
              </div>
            )}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
              <ReportCard label="Prodotti nuovi" value={T.prodotti_inseriti} tone="ok" />
              <ReportCard label="Prodotti aggiornati" value={T.prodotti_aggiornati} tone="info" />
              <ReportCard label="Listino ADM nuovi" value={T.listino_inseriti} tone="ok" />
              <ReportCard label="Listino ADM aggiornati" value={T.listino_aggiornati} tone="info" />
              <ReportCard label="Vending nuove col." value={T.vending_inseriti} tone="ok" />
              <ReportCard label="Vending aggiornate" value={T.vending_aggiornati} tone="info" />
              <ReportCard label="Storico ordini" value={T.storico_ricreato} tone="info" />
              <ReportCard label="Vendite storiche" value={T.db_storico_vend_righe} tone="info" />
              <ReportCard label="Parametri aggiornati" value={T.parametri_aggiornati} tone="info" />
            </div>

            {T.parametri_saltati > 0 && (
              <div className="text-xs bg-amber-50 border border-amber-200 rounded-md p-3 mb-3 flex items-start gap-2">
                <WarningCircle size={16} className="text-amber-700 flex-shrink-0 mt-0.5" />
                <div>
                  <b>{T.parametri_saltati}</b> parametro/i nel foglio Excel <b>non</b> sono stati importati perché non presenti nel DB.
                  Se vuoi aggiungerli, usa l'inserimento manuale qui sotto.
                </div>
              </div>
            )}

            <details className="text-xs text-slate-600">
              <summary className="cursor-pointer font-bold uppercase tracking-wider text-slate-500">Dettaglio per foglio</summary>
              <div className="mt-2 space-y-1 font-mono">
                {importReport.fogli_trovati.map(f => (
                  <div key={f} className="flex gap-2"><Badge tone="ok">{f}</Badge>
                    <span>{JSON.stringify(importReport.dettaglio[f])}</span>
                  </div>
                ))}
                {importReport.fogli_mancanti.map(f => (
                  <div key={f} className="flex gap-2"><Badge tone="warning">{f}</Badge><span className="text-slate-500">non presente nell'Excel</span></div>
                ))}
              </div>
            </details>
          </div>
        )}
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
        <Card className="p-5">
          <div className="flex items-start justify-between gap-3 mb-4">
            <div>
              <h2 className="font-heading font-black text-lg">Backup e ripristino</h2>
              <p className="text-sm text-slate-600">Ogni import completo crea un backup automatico. Puoi crearne uno manuale prima di prove importanti.</p>
            </div>
            <button onClick={createBackup} disabled={workingBackup} className="rounded-md bg-slate-900 px-4 py-2 text-sm font-bold text-white hover:bg-slate-800 disabled:opacity-40">
              Backup ora
            </button>
          </div>
          <div className="space-y-2">
            {backups.slice(0, 6).map(b => (
              <div key={b.id} className="flex items-center justify-between gap-3 rounded-md border border-slate-200 px-3 py-2 text-sm">
                <div className="min-w-0">
                  <div className="font-semibold truncate">{b.label}</div>
                  <div className="text-xs text-slate-500">{new Date(b.created_at).toLocaleString("it-IT")} · {formatNum(b.total_docs)} documenti · {b.reason}</div>
                </div>
                <button onClick={() => setRestoreTarget(b)} className="rounded-md border border-red-200 px-3 py-1.5 text-xs font-bold text-red-700 hover:bg-red-50">Ripristina</button>
              </div>
            ))}
            {!backups.length && <div className="text-sm text-slate-400">Nessun backup registrato.</div>}
          </div>
        </Card>

        <Card className="p-5">
          <h2 className="font-heading font-black text-lg mb-4">Storico import</h2>
          <div className="space-y-2">
            {history.slice(0, 8).map(h => (
              <div key={h.id} className="rounded-md border border-slate-200 px-3 py-2 text-sm">
                <div className="flex justify-between gap-3">
                  <span className="font-semibold truncate">{h.file}</span>
                  <span className="text-xs text-slate-500">{new Date(h.created_at).toLocaleString("it-IT")}</span>
                </div>
                <div className="mt-1 text-xs text-slate-600">
                  Prodotti {formatNum((h.totali?.prodotti_inseriti || 0) + (h.totali?.prodotti_aggiornati || 0))} · Vendite storiche {formatNum(h.totali?.db_storico_vend_righe)} · Errori {formatNum(h.errori)}
                </div>
              </div>
            ))}
            {!history.length && <div className="text-sm text-slate-400">Nessun import registrato.</div>}
          </div>
        </Card>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead><tr><th>Parametro</th><th>Descrizione</th><th className="text-right">Valore</th><th></th></tr></thead>
            <tbody>
              {rows.map(p => (
                <tr key={p.nome} data-testid={`param-row-${p.nome}`}>
                  <td className="font-mono font-semibold">{p.nome}</td>
                  <td className="text-slate-600">{p.descrizione}</td>
                  <td className="font-mono text-right">
                    {p.nome === "DATA_APERTURA" ? (
                      <input
                        data-testid={`param-input-${p.nome}`}
                        type="date"
                        defaultValue={openingDateToIso(p.valore)}
                        aria-label="Data apertura attività"
                        className="border rounded-md px-3 py-1 text-sm font-mono w-40 text-right"
                        onBlur={(e) => {
                          const v = isoDateToCompactNumber(e.target.value);
                          if (v !== null && e.target.value !== openingDateToIso(p.valore)) update(p.nome, v);
                        }}
                      />
                    ) : (
                      <input
                        data-testid={`param-input-${p.nome}`}
                        type="number"
                        step="0.01"
                        defaultValue={p.valore}
                        className="border rounded-md px-3 py-1 text-sm font-mono w-32 text-right"
                        onBlur={(e) => {
                          const v = parseFloat(e.target.value);
                          if (!Number.isNaN(v) && v !== Number(p.valore)) update(p.nome, v);
                        }}
                      />
                    )}
                  </td>
                  <td></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <ConfirmDialog
        open={Boolean(restoreTarget)}
        title="Ripristinare questo backup?"
        confirmLabel="Ripristina backup"
        danger
        loading={workingBackup}
        onCancel={() => setRestoreTarget(null)}
        onConfirm={restoreBackup}
      >
        <p>Il database operativo verrà riportato allo stato del backup <b>{restoreTarget?.label}</b>.</p>
        <p className="mt-2 text-slate-600">Prima del ripristino verrà creato automaticamente un nuovo backup dello stato attuale.</p>
      </ConfirmDialog>
    </Layout>
  );
}

function ReportCard({ label, value, tone }) {
  const toneColor = { ok: "text-emerald-700 bg-emerald-50 border-emerald-200", info: "text-slate-900 bg-white border-slate-200" }[tone] || "";
  return (
    <div className={`border rounded-md p-3 ${toneColor}`}>
      <div className="overline">{label}</div>
      <div className="font-heading font-black text-2xl mt-1 tabular-nums">{formatNum(value || 0)}</div>
    </div>
  );
}
