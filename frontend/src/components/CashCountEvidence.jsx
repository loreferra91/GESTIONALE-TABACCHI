import { useRef, useState } from "react";
import { api, apiErrorMessage } from "../lib/api";
import { clearIdempotencyKey, idempotencyKeyFor } from "../lib/idempotency";

const DENOMINATIONS = [
  ["500", 50000], ["200", 20000], ["100", 10000], ["50", 5000],
  ["20", 2000], ["10", 1000], ["5", 500], ["2", 200],
  ["1", 100], ["0.50", 50], ["0.20", 20], ["0.10", 10],
  ["0.05", 5], ["0.02", 2], ["0.01", 1],
];
const EMPTY = Object.fromEntries(DENOMINATIONS.map(([name]) => [name, "0"]));
const SCOPE = "cash-physical-count-evidence-staging";

const euro = cents =>
  `${Math.trunc(cents / 100).toLocaleString("it-IT")},${String(cents % 100).padStart(2, "0")} €`;

export default function CashCountEvidence() {
  const [expanded, setExpanded] = useState(false);
  const [counts, setCounts] = useState({ ...EMPTY });
  const [kind, setKind] = useState("spot");
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [locked, setLocked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const payloadRef = useRef(null);
  const totalCents = DENOMINATIONS.reduce((acc, [name, cents]) => {
    const value = Number(counts[name]);
    return acc + (Number.isInteger(value) && value >= 0 ? value * cents : 0);
  }, 0);
  const valid = reference.trim().length >= 4 &&
    DENOMINATIONS.every(([name]) => {
      const n = Number(counts[name]);
      return Number.isSafeInteger(n) && n >= 0 && n <= 10000;
    });

  const save = async () => {
    if (busy) return;
    if (!valid && !payloadRef.current) {
      setError("Indica un riferimento al conteggio firmato e quantità intere valide.");
      return;
    }
    if (!payloadRef.current) {
      const source = {
        kind, denominations: Object.fromEntries(
          DENOMINATIONS.map(([name]) => [name, Number(counts[name])])
        ), source_reference: reference.trim(), note: note.trim(),
      };
      payloadRef.current = {
        ...source,
        observed_at: new Date().toISOString(),
        idempotency_key: idempotencyKeyFor(SCOPE, source),
      };
    }
    setBusy(true);
    setLocked(true);
    setError("");
    try {
      const response = await api.post("/contabilita/conteggi-fisici", payloadRef.current);
      setResult(response.data);
      clearIdempotencyKey(SCOPE);
      payloadRef.current = null;
      setLocked(false);
      setCounts({ ...EMPTY });
      setReference("");
      setNote("");
    } catch (failure) {
      setError(apiErrorMessage(failure, "Conteggio non confermato: ripeti con lo stesso identificativo."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-5 border-t border-slate-200 pt-4">
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}
        className="text-sm font-semibold text-slate-700 underline">
        {expanded ? "Nascondi conteggio fisico" : "Registra conteggio fisico (solo staging)"}
      </button>
      {expanded && <div className="mt-3 space-y-3">
        <p className="text-sm text-slate-600">
          Registra una prova di conteggio, senza modificare i saldi di cassa o gli incassi.
          Conserva separatamente il foglio firmato; l’importo non è una baseline certificata.
        </p>
        <label className="block text-sm font-semibold text-slate-700">Tipo di conteggio
          <select aria-label="Tipo conteggio fisico" value={kind} disabled={locked || busy}
            onChange={event => setKind(event.target.value)}
            className="mt-1 block rounded border border-slate-300 p-2 font-normal">
            <option value="spot">Verifica intermedia</option>
            <option value="opening">Apertura</option>
            <option value="closing">Chiusura</option>
          </select>
        </label>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {DENOMINATIONS.map(([denomination]) => (
            <label key={denomination} className="text-xs font-semibold text-slate-600">
              {denomination} €
              <input type="number" inputMode="numeric" min="0" max="10000" step="1"
                value={counts[denomination]} disabled={locked || busy}
                aria-label={`Quantità ${denomination} euro`}
                onChange={event => setCounts(current => ({
                  ...current, [denomination]: event.target.value,
                }))}
                className="mt-1 w-full rounded border border-slate-300 p-2 text-sm font-normal"
              />
            </label>
          ))}
        </div>
        <p className="text-sm font-semibold" data-testid="cash-count-total">
          Totale contato: {euro(totalCents)}
        </p>
        <label className="block text-xs font-semibold text-slate-600">
          Riferimento al conteggio firmato (obbligatorio)
          <input value={reference} maxLength={120} disabled={locked || busy}
            onChange={event => setReference(event.target.value)}
            placeholder="Es. verbale cassa 2026-11-19"
            className="mt-1 w-full rounded border border-slate-300 p-2 text-sm font-normal"
          />
        </label>
        <label className="block text-xs font-semibold text-slate-600">
          Nota (facoltativa)
          <input value={note} maxLength={500} disabled={locked || busy}
            onChange={event => setNote(event.target.value)}
            className="mt-1 w-full rounded border border-slate-300 p-2 text-sm font-normal"
          />
        </label>
        <button type="button" onClick={save} disabled={busy || (!locked && !valid)}
          className="rounded-md border border-slate-300 px-3 py-2 text-sm font-semibold disabled:opacity-50">
          {busy ? "Registrazione…" : locked ? "Riprova identico invio" : "Registra evidenza"}
        </button>
        {locked && <p className="text-xs text-amber-800">
          Invio incerto: i valori restano bloccati per evitare doppie registrazioni.
          Ripeti lo stesso invio prima di creare un altro conteggio.
        </p>}
        {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
        {result && <p role="status" className="text-sm text-slate-700">
          Conteggio {result.replayed ? "già registrato" : "registrato"}:
          {" "}{euro(result.total_cents)} · riferimento {result.source_reference}.
          Saldo contabile invariato.
        </p>}
      </div>}
    </section>
  );
}
