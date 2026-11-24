import { useEffect, useMemo, useState } from "react";
import Layout from "../components/Layout";
import { Card, formatEur, formatNum } from "../components/UI";
import { api, apiErrorMessage } from "../lib/api";
import { clearIdempotencyKey, idempotencyKeyFor } from "../lib/idempotency";
import { toast } from "sonner";

const blankProduct = { codice: "", descrizione: "", prezzo: "", quantita_magazzino: "" };

function italianDay() {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Rome", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

const moneyFromCents = cents => formatEur((Number(cents) || 0) / 100);
const positiveInteger = text => /^\d+$/.test(String(text).trim()) && Number(text) > 0 && Number.isSafeInteger(Number(text));

export default function GrattaVinci() {
  const [tab, setTab] = useState("magazzino");
  const [reportMode, setReportMode] = useState("giorno");
  const [giorno, setGiorno] = useState(italianDay);
  const [report, setReport] = useState({ righe: [], totale_biglietti: 0, totale_centesimi: 0 });
  const [query, setQuery] = useState("");
  const [form, setForm] = useState(blankProduct);
  const [editing, setEditing] = useState(null);
  const [quantities, setQuantities] = useState({});
  const [busy, setBusy] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    setReport({ righe: [], totale_biglietti: 0, totale_centesimi: 0 });
    api.get("/gratta-e-vinci/riepilogo", {
      params: reportMode === "giorno" ? { giorno } : {},
    }).then(response => {
      if (alive) setReport(response.data);
    }).catch(err => {
      if (alive) setError(apiErrorMessage(err, "Impossibile caricare il magazzino Gratta e Vinci"));
    }).finally(() => {
      if (alive) setLoading(false);
    });
    return () => { alive = false; };
  }, [giorno, reportMode, revision]);

  const rows = useMemo(() => {
    const text = query.trim().toLocaleLowerCase("it-IT");
    return (report.righe || []).filter(row =>
      !text || [row.codice, row.descrizione].some(value => String(value || "").toLocaleLowerCase("it-IT").includes(text))
    );
  }, [report.righe, query]);

  const refresh = () => setRevision(current => current + 1);

  const reset = () => {
    setEditing(null);
    setForm(blankProduct);
  };

  const saveProduct = async event => {
    event.preventDefault();
    if (!form.codice.trim() || !form.descrizione.trim() ||
      !/^\d+(?:[.,]\d{1,2})?$/.test(form.prezzo.trim()) ||
      (!editing && !/^\d+$/.test(form.quantita_magazzino.trim()))) {
      toast.error("Inserisci codice, descrizione, prezzo valido e quantità intera non negativa");
      return;
    }
    setBusy("product");
    try {
      if (editing) {
        await api.put(`/gratta-e-vinci/prodotti/${encodeURIComponent(editing.id)}`, {
          descrizione: form.descrizione.trim(),
          prezzo: form.prezzo.replace(",", "."),
          version: editing.version,
        });
        toast.success("Gratta e Vinci aggiornato");
      } else {
        await api.post("/gratta-e-vinci/prodotti", {
          codice: form.codice.trim(),
          descrizione: form.descrizione.trim(),
          prezzo: form.prezzo.replace(",", "."),
          quantita_magazzino: Number(form.quantita_magazzino),
        });
        toast.success("Gratta e Vinci aggiunto al magazzino separato");
      }
      reset();
      refresh();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile salvare il prodotto"));
      if (err?.response?.status === 409) refresh();
    } finally {
      setBusy(null);
    }
  };

  const startEdit = row => {
    setEditing(row);
    setForm({
      codice: row.codice,
      descrizione: row.descrizione,
      prezzo: (row.prezzo_centesimi / 100).toFixed(2),
      quantita_magazzino: String(row.quantita_magazzino),
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const moveStock = async (row, kind) => {
    const raw = quantities[`${kind}:${row.codice}`] ?? "1";
    if (!positiveInteger(raw) || Number(raw) > 1000000) {
      toast.error("Indica una quantità intera positiva (massimo 1.000.000)");
      return;
    }
    const quantita = Number(raw);
    const payload = { codice: row.codice, quantita };
    const scope = `gratta-vinci:${kind}:${row.codice}`;
    const idempotency_key = idempotencyKeyFor(scope, payload);
    setBusy(`${kind}:${row.codice}`);
    try {
      const endpoint = kind === "vendita" ? "vendite" : "carichi";
      const response = await api.post(`/gratta-e-vinci/${endpoint}`, { ...payload, idempotency_key });
      clearIdempotencyKey(scope);
      setQuantities(previous => ({ ...previous, [`${kind}:${row.codice}`]: "1" }));
      toast.success(response.data.gia_registrato
        ? "Operazione già registrata: nessun doppio movimento"
        : kind === "vendita" ? "Vendita registrata e giacenza scaricata" : "Carico magazzino registrato");
      refresh();
    } catch (err) {
      // Preserve the idempotency key on network errors/uncertain commits.
      toast.error(apiErrorMessage(err, "Operazione non confermata: riprova senza cambiare quantità"));
      if (err?.response?.status === 409) refresh();
    } finally {
      setBusy(null);
    }
  };

  return (
    <Layout title="Gratta e Vinci" subtitle="Magazzino indipendente e vendite">
      <div className="mb-5 flex gap-2 border-b border-slate-200" role="tablist" aria-label="Gratta e Vinci">
        {[
          ["magazzino", "Magazzino"],
          ["vendite", "Registrazione vendite"],
        ].map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`px-4 py-3 text-sm font-semibold border-b-2 ${tab === key
              ? "border-slate-900 text-slate-900" : "border-transparent text-slate-500 hover:text-slate-900"}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === "magazzino" && (
        <Card className="p-4 mb-5">
          <h2 className="mb-3 text-sm font-bold text-slate-800">
            {editing ? `Modifica ${editing.codice}` : "Registra nuovo Gratta e Vinci"}
          </h2>
          <form onSubmit={saveProduct} className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <input data-testid="gv-code" aria-label="Codice" placeholder="Codice" required maxLength={64}
              disabled={Boolean(editing) || Boolean(busy)}
              className="border rounded-md px-3 py-2 text-sm font-mono"
              value={form.codice} onChange={event => setForm(old => ({ ...old, codice: event.target.value }))} />
            <input data-testid="gv-description" aria-label="Descrizione" placeholder="Descrizione" required maxLength={200}
              disabled={Boolean(busy)} className="border rounded-md px-3 py-2 text-sm sm:col-span-1"
              value={form.descrizione} onChange={event => setForm(old => ({ ...old, descrizione: event.target.value }))} />
            <input data-testid="gv-price" aria-label="Prezzo in euro" placeholder="Prezzo (€)" inputMode="decimal" required
              disabled={Boolean(busy)} className="border rounded-md px-3 py-2 text-sm font-mono"
              value={form.prezzo} onChange={event => setForm(old => ({ ...old, prezzo: event.target.value }))} />
            <input data-testid="gv-stock" aria-label="Quantità iniziale in magazzino"
              placeholder={editing ? "Giacenza (solo lettura)" : "Quantità in magazzino"}
              type="number" min="0" max="1000000" step="1" required={!editing}
              disabled={Boolean(editing) || Boolean(busy)}
              className="border rounded-md px-3 py-2 text-sm font-mono"
              value={form.quantita_magazzino} onChange={event => setForm(old => ({ ...old, quantita_magazzino: event.target.value }))} />
            <div className="flex gap-2">
              <button data-testid="gv-save" type="submit" disabled={Boolean(busy)}
                className="rounded-md bg-slate-900 px-4 py-2 text-sm font-semibold text-white flex-1 disabled:opacity-50">
                {busy === "product" ? "Salvataggio…" : editing ? "Aggiorna" : "Aggiungi"}
              </button>
              {editing && (
                <button type="button" onClick={reset} disabled={Boolean(busy)}
                  className="border rounded-md px-3 py-2 text-sm">Annulla</button>
              )}
            </div>
          </form>
          <p className="mt-3 text-xs text-slate-500">
            Per aumentare la quantità di un prodotto esistente usa Carica. Le vendite e i carichi sono registrati nello storico.
          </p>
        </Card>
      )}

      {tab === "vendite" && (
        <Card className="p-4 mb-5">
          <div className="flex flex-wrap gap-3 items-center justify-between">
            <div>
              <h2 className="font-semibold text-slate-900">Vendite Gratta e Vinci</h2>
              <p className="text-xs text-slate-500 mt-1">Conteggio biglietti venduti e valore nominale delle vendite registrate; non è un saldo cassa.</p>
            </div>
            <div className="flex flex-wrap gap-2 items-center">
              <select data-testid="gv-report-mode" aria-label="Periodo vendite"
                className="border rounded-md px-3 py-2 text-sm"
                value={reportMode} onChange={event => setReportMode(event.target.value)}>
                <option value="giorno">Giorno</option>
                <option value="tutto">Tutto lo storico</option>
              </select>
              {reportMode === "giorno" && (
                <input data-testid="gv-report-day" type="date" aria-label="Giorno vendite"
                  className="border rounded-md px-3 py-2 text-sm"
                  value={giorno} onChange={event => setGiorno(event.target.value)} />
              )}
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-5 text-sm">
            <span>Biglietti venduti: <strong data-testid="gv-sold-total">{formatNum(report.totale_biglietti)}</strong></span>
            <span>Totale: <strong data-testid="gv-revenue-total">{moneyFromCents(report.totale_centesimi)}</strong></span>
          </div>
        </Card>
      )}

      <div className="mb-3 flex flex-wrap items-center gap-3">
        <input data-testid="gv-search" aria-label="Cerca Gratta e Vinci"
          placeholder="Cerca codice o descrizione…" value={query}
          onChange={event => setQuery(event.target.value)}
          className="w-full sm:w-96 border rounded-md px-3 py-2 text-sm" />
        <span className="text-sm text-slate-500">{rows.length} prodotti</span>
        <button type="button" onClick={refresh} disabled={Boolean(busy) || loading}
          className="rounded border px-3 py-2 text-xs text-slate-700 disabled:opacity-50">Aggiorna</button>
      </div>

      {error && (
        <div role="alert" className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}
      <Card className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-left text-slate-600">
            <tr>
              <th scope="col" className="p-3">Codice</th>
              <th scope="col" className="p-3">Descrizione</th>
              {tab === "magazzino" ? (
                <>
                  <th scope="col" className="p-3 text-right">Prezzo</th>
                  <th scope="col" className="p-3 text-right">Quantità disponibile</th>
                  <th scope="col" className="p-3">Carico / modifica</th>
                </>
              ) : (
                <>
                  <th scope="col" className="p-3 text-right">Biglietti venduti</th>
                  <th scope="col" className="p-3 text-right">Totale</th>
                  <th scope="col" className="p-3 text-right">Disponibili</th>
                  <th scope="col" className="p-3">Registra vendita</th>
                </>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map(row => {
              const kind = tab === "magazzino" ? "carico" : "vendita";
              const busyKey = `${kind}:${row.codice}`;
              return (
                <tr key={row.id} data-testid={`gv-row-${row.codice}`} className="hover:bg-slate-50">
                  <td className="p-3 font-mono text-slate-700">{row.codice}</td>
                  <td className="p-3 font-medium">{row.descrizione}</td>
                  {tab === "magazzino" ? (
                    <>
                      <td className="p-3 text-right whitespace-nowrap">{moneyFromCents(row.prezzo_centesimi)}</td>
                      <td className="p-3 text-right font-mono">{formatNum(row.quantita_magazzino)}</td>
                    </>
                  ) : (
                    <>
                      <td className="p-3 text-right font-mono">{formatNum(row.venduti)}</td>
                      <td className="p-3 text-right font-semibold whitespace-nowrap">{moneyFromCents(row.totale_centesimi)}</td>
                      <td className="p-3 text-right font-mono">{formatNum(row.quantita_magazzino)}</td>
                    </>
                  )}
                  <td className="p-3">
                    <div className="flex items-center gap-2">
                      <input data-testid={`gv-qty-${busyKey}`} aria-label={`Quantità ${kind} ${row.codice}`}
                        type="number" min="1" max="1000000" step="1" inputMode="numeric"
                        className="w-20 rounded-md border px-2 py-1.5 text-sm"
                        value={quantities[busyKey] ?? "1"}
                        onChange={event => setQuantities(previous => ({ ...previous, [busyKey]: event.target.value }))}
                        disabled={Boolean(busy)} />
                      <button type="button" data-testid={`gv-action-${busyKey}`}
                        onClick={() => moveStock(row, kind)} disabled={Boolean(busy) || loading || Boolean(error) ||
                          (kind === "vendita" && row.quantita_magazzino <= 0)}
                        className="rounded-md bg-slate-900 text-white px-3 py-1.5 text-xs font-semibold disabled:opacity-40">
                        {busy === busyKey ? "Registro…" : kind === "vendita" ? "Vendi" : "Carica"}
                      </button>
                      {tab === "magazzino" && (
                        <button type="button" onClick={() => startEdit(row)} disabled={Boolean(busy)}
                          className="text-xs text-slate-600 hover:underline">Modifica</button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
            {!loading && rows.length === 0 && (
              <tr><td colSpan={7} className="p-6 text-center text-slate-500">Nessun Gratta e Vinci presente.</td></tr>
            )}
            {loading && <tr><td colSpan={7} className="p-6 text-center text-slate-500">Caricamento…</td></tr>}
          </tbody>
        </table>
      </Card>
    </Layout>
  );
}
