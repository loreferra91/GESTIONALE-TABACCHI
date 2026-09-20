import { useEffect, useState, useRef } from "react";
import Layout from "../components/Layout";
import { api, API } from "../lib/api";
import { Card, Badge, formatNum } from "../components/UI";
import { toast } from "sonner";
import { UploadSimple, CheckCircle, WarningCircle } from "@phosphor-icons/react";

export default function Parametri() {
  const [rows, setRows] = useState([]);
  const [importing, setImporting] = useState(false);
  const [importReport, setImportReport] = useState(null);
  const fileRef = useRef(null);

  const load = async () => {
    const r = await api.get("/parametri");
    setRows(r.data);
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const update = async (nome, valore) => {
    try {
      await api.put(`/parametri/${nome}`, { valore: parseFloat(valore) });
      toast.success(`${nome} aggiornato`);
      load();
    } catch (err) {
      const msg = err?.response?.data?.detail || "Errore aggiornamento";
      toast.error(msg);
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
      toast.success(`Import completato: ${tot} righe aggiornate su ${j.fogli_trovati.length} fogli`);
      load();
    } catch (err) {
      toast.error(`Errore import: ${err.message || "sconosciuto"}`);
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = "";
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
              <b> RIEP_VENDITA, LISTINO ADM, RICARICA VENDING, STORICO_ORDINI, PARAMETRI</b> e allinea il DB con l'Excel (Excel = fonte di verità).
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
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
              <ReportCard label="Prodotti nuovi" value={T.prodotti_inseriti} tone="ok" />
              <ReportCard label="Prodotti aggiornati" value={T.prodotti_aggiornati} tone="info" />
              <ReportCard label="Listino ADM nuovi" value={T.listino_inseriti} tone="ok" />
              <ReportCard label="Listino ADM aggiornati" value={T.listino_aggiornati} tone="info" />
              <ReportCard label="Vending nuove col." value={T.vending_inseriti} tone="ok" />
              <ReportCard label="Vending aggiornate" value={T.vending_aggiornati} tone="info" />
              <ReportCard label="Storico ordini" value={T.storico_ricreato} tone="info" />
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
                  </td>
                  <td></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
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
