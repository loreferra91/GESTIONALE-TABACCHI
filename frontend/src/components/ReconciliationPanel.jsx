import { useState } from "react";
import { api, apiErrorMessage } from "../lib/api";
import { Card } from "./UI";

const STATE_OPTIONS = [
  ["aperta", "Aperta"],
  ["in_verifica", "In verifica"],
  ["risolta", "Risolta"],
  ["ignorata", "Ignorata"],
];

function FindingRow({ item, onUpdated }) {
  const [state, setState] = useState(item.stato || "aperta");
  const [note, setNote] = useState(item.nota || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const save = async () => {
    if (saving) return;
    setSaving(true);
    setError("");
    try {
      const response = await api.put(`/contabilita/riconciliazione/segnalazioni/${item.fingerprint}`, {
        stato: state,
        nota: note,
        tipo: item.tipo,
        raccolta: item.raccolta,
        riferimento: item.riferimento,
      });
      onUpdated(item.fingerprint, item.stato || "aperta", response.data);
    } catch (failure) {
      setError(apiErrorMessage(failure, "Aggiornamento della segnalazione non riuscito."));
    } finally {
      setSaving(false);
    }
  };

  return <li className="rounded border border-amber-200 bg-amber-50 p-3">
    <b>{item.raccolta} · {item.riferimento}</b>: {item.messaggio}
    {item.tipo === "IMPORTO_PAGAMENTO_CSV" && <div>
      Vendita: {item.registrato} € ({item.pagamento_vendita || "non indicato"}); registro: {item.atteso} € ({item.pagamento_registro || "non indicato"}).
    </div>}
    {item.tipo === "FRAZIONI_CENTESIMO" && <div>Valore: {item.valore}; ai centesimi: {item.normalizzato} €.</div>}
    <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(9rem,0.35fr)_1fr_auto] sm:items-end">
      <label className="text-xs font-semibold text-slate-600">Stato
        <select aria-label={`Stato ${item.riferimento}`} value={state} onChange={event => setState(event.target.value)} className="mt-1 block w-full rounded border border-slate-300 bg-white px-2 py-1.5 text-sm font-normal">
          {STATE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label className="text-xs font-semibold text-slate-600">Nota operativa
        <input aria-label={`Nota ${item.riferimento}`} value={note} maxLength={500} onChange={event => setNote(event.target.value)} placeholder="Aggiungi un riscontro" className="mt-1 block w-full rounded border border-slate-300 bg-white px-2 py-1.5 text-sm font-normal" />
      </label>
      <button type="button" onClick={save} disabled={saving} className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold disabled:opacity-50">
        {saving ? "Salvataggio…" : "Salva stato"}
      </button>
    </div>
    {item.aggiornato_il ? <p className="mt-2 text-xs text-slate-500">Aggiornata {new Date(item.aggiornato_il).toLocaleString("it-IT")} da {item.operatore || "operatore"}.</p> : null}
    {error ? <p role="alert" className="mt-2 text-sm text-red-700">{error}</p> : null}
  </li>;
}

export default function ReconciliationPanel() {
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const run = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    setReport(null);
    try {
      const params = {};
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      const response = await api.get("/contabilita/riconciliazione", { params });
      setReport(response.data);
    } catch (failure) {
      setError(apiErrorMessage(failure, "Controllo non riuscito. Riprovare."));
    } finally {
      setBusy(false);
    }
  };

  const updateFinding = (fingerprint, previousState, saved) => {
    setReport(current => {
      if (!current) return current;
      const nextCounts = { ...(current.conteggi_stato || {}) };
      nextCounts[previousState] = Math.max(0, (nextCounts[previousState] || 0) - 1);
      nextCounts[saved.stato] = (nextCounts[saved.stato] || 0) + 1;
      return {
        ...current,
        conteggi_stato: nextCounts,
        totale_da_gestire: (nextCounts.aperta || 0) + (nextCounts.in_verifica || 0),
        dettagli: current.dettagli.map(item => item.fingerprint === fingerprint
          ? { ...item, ...saved, aggiornato_il: saved.updated_at }
          : item),
      };
    });
  };

  return (
    <Card className="mb-5 p-5">
      <h2 className="font-heading text-lg font-black">Controllo di riconciliazione</h2>
      <p className="my-2 text-sm text-slate-600">
        Verifica importi, giacenze vending e corrispondenze CSV senza modificare i dati.
        Eseguire a operazioni ferme. Non sostituisce il conteggio fisico o una verifica contabile completa.
      </p>
      <button type="button" onClick={run} disabled={busy}
        className="rounded-md border border-slate-300 px-3 py-2 text-sm font-semibold disabled:opacity-50">
        {busy ? "Controllo in corso…" : "Esegui controllo"}
      </button>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="text-xs font-semibold text-slate-600">Da<input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="mt-1 block rounded border px-2 py-1 text-sm font-normal" /></label>
        <label className="text-xs font-semibold text-slate-600">A<input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} className="mt-1 block rounded border px-2 py-1 text-sm font-normal" /></label>
        {report && <button type="button" onClick={() => {
          const rows = report.dettagli || [];
          const header = ["tipo", "raccolta", "riferimento", "messaggio"];
          const csv = [header, ...rows.map(row => header.map(field => String(row[field] || "").replaceAll('"', '""')))]
            .map(row => row.map(value => `"${value}"`).join(",")).join("\n");
          const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
          const link = document.createElement("a"); link.href = url; link.download = "riconciliazione.csv"; link.click(); URL.revokeObjectURL(url);
        }} className="rounded-md border border-slate-300 px-3 py-2 text-sm font-semibold">Esporta dettagli CSV</button>}
      </div>
      {error && <p role="alert" className="mt-3 text-red-700">{error}</p>}
      {report && <div className="mt-4" aria-live="polite">
        <p className="font-semibold">
          {report.totale_segnalazioni
            ? `${report.totale_segnalazioni} segnalazioni da verificare`
            : "Nessuna discrepanza rilevata nei controlli eseguiti"}
        </p>
        {report.totale_segnalazioni > 0 ? <p className="mt-1 text-sm font-medium text-slate-700">
          Da gestire: {report.totale_da_gestire ?? report.totale_segnalazioni} · In verifica: {report.conteggi_stato?.in_verifica || 0} · Risolte: {report.conteggi_stato?.risolta || 0} · Ignorate: {report.conteggi_stato?.ignorata || 0}
        </p> : null}
        <p className="my-2 text-sm text-slate-600">
          {new Date(report.generated_at).toLocaleString("it-IT")} · {report.prodotti_confrontati} prodotti confrontati · {report.vendite_csv_confrontate} vendite CSV confrontate · {report.vendite_csv_non_confrontabili} non confrontabili (non necessariamente errate).
        </p>
        <ul className="space-y-2 text-sm">
          {report.dettagli.map(item => <FindingRow key={`${item.fingerprint}-${item.aggiornato_il || "new"}`} item={item} onUpdated={updateFinding} />)}
        </ul>
        {report.dettagli_omessi > 0 && <p className="mt-2 text-sm">Altre {report.dettagli_omessi} segnalazioni non mostrate. Il conteggio comprende tutti i documenti esaminati.</p>}
        <details className="mt-3 text-sm text-slate-600">
          <summary className="cursor-pointer">Copertura e limiti del controllo</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {[...(report.copertura || []), ...report.limiti].map((text) => <li key={text}>{text}</li>)}
          </ul>
        </details>
      </div>}
    </Card>
  );
}
