import { useEffect, useMemo, useState } from "react";
import { CheckCircle, FloppyDisk, MagnifyingGlass } from "@phosphor-icons/react";
import { toast } from "sonner";
import Layout from "../components/Layout";
import { Badge, Card, KpiCard, formatNum } from "../components/UI";
import { api, apiErrorMessage } from "../lib/api";
import { SearchBar, Th, useSortSearch } from "../lib/tableHooks";

const statusTone = status => ({
  OK: "ok",
  DIFFERENZA: "warning",
  "CODICE MANCANTE": "error",
  "ANOMALIA VENDING": "error",
}[status] || "info");

export default function SmartVenueControllo() {
  const [text, setText] = useState("");
  const [rows, setRows] = useState([]);
  const [errors, setErrors] = useState([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(null);

  useEffect(() => {
    api.get("/smart-venue")
      .then(response => setRows(response.data || []))
      .catch(error => toast.error(apiErrorMessage(error, "Impossibile caricare l'ultimo controllo")));
  }, []);

  const sortableRows = useMemo(() => rows.map(row => ({
    ...row,
    gestionale_totale: row.gestionale_totale ?? row.rimanenze,
    stato: row.stato || (row.differenza ? "DIFFERENZA" : "OK"),
    ricerca_barcode: (row.barcodes || []).join(" "),
  })), [rows]);

  const {
    rows: visibleRows,
    sortKey,
    sortDir,
    toggle,
    query,
    setQuery,
  } = useSortSearch(sortableRows, {
    searchFields: ["codice", "descrizione", "ricerca_barcode"],
  });

  const summary = useMemo(() => ({
    products: rows.length,
    smartVenue: rows.reduce((total, row) => total + Number(row.smart_venue || 0), 0),
    differences: rows.filter(row => row.stato !== "OK").length,
  }), [rows]);

  const analyze = async () => {
    if (!text.trim()) {
      toast.error("Incolla prima i dati copiati da SmartVenue");
      return;
    }
    setAnalyzing(true);
    try {
      const response = await api.post("/smart-venue/bulk/preview", { testo: text });
      setRows(response.data.righe || []);
      setErrors(response.data.errori || []);
      setSavedAt(null);
      if (response.data.errori?.length) toast.warning("Anteprima creata con alcune righe da correggere");
      else toast.success("Controllo SmartVenue completato");
    } catch (error) {
      toast.error(apiErrorMessage(error, "Impossibile analizzare i dati SmartVenue"));
    } finally {
      setAnalyzing(false);
    }
  };

  const save = async () => {
    if (!text.trim() || errors.length) {
      toast.error(errors.length ? "Correggi gli errori prima di salvare" : "Analizza prima i dati");
      return;
    }
    setSaving(true);
    try {
      const response = await api.post("/smart-venue/bulk/import", { testo: text });
      setRows(response.data.righe || []);
      setErrors([]);
      setSavedAt(response.data.created_at);
      toast.success(`${response.data.prodotti} prodotti SmartVenue salvati`);
    } catch (error) {
      toast.error(apiErrorMessage(error, "Impossibile salvare il controllo SmartVenue"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Layout title="Controllo SmartVenue" subtitle="confronto tra inventario fisico, negozio e Vending">
      <Card className="mb-6 p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="font-heading text-lg font-black text-slate-900">Incolla i dati dal sito SmartVenue</h2>
            <p className="mt-1 max-w-3xl text-sm text-slate-600">
              Il totale SmartVenue comprende negozio e distributore. Il controllo sottrae la giacenza fisica Vending e ricava la quantità attesa in negozio.
            </p>
          </div>
          {savedAt ? <span className="text-xs text-emerald-700">Salvato il {new Date(savedAt).toLocaleString("it-IT")}</span> : null}
        </div>
        <textarea
          data-testid="smart-venue-bulk-text"
          value={text}
          onChange={event => setText(event.target.value)}
          rows={10}
          placeholder={"Barcode - Descrizione - Codice ADM - Categoria\n5.50\n81\n445.50"}
          className="mt-4 w-full rounded-md border border-slate-300 px-3 py-3 font-mono text-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200"
        />
        <div className="mt-3 flex flex-wrap justify-end gap-3">
          <button
            type="button"
            data-testid="smart-venue-bulk-preview"
            onClick={analyze}
            disabled={analyzing || saving || !text.trim()}
            className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-bold text-slate-800 hover:bg-slate-50 disabled:opacity-50"
          >
            <MagnifyingGlass size={17} /> {analyzing ? "Analisi…" : "Analizza dati"}
          </button>
          <button
            type="button"
            data-testid="smart-venue-bulk-save"
            onClick={save}
            disabled={saving || analyzing || !rows.length || !text.trim() || errors.length > 0}
            className="inline-flex items-center gap-2 rounded-md bg-slate-900 px-4 py-2 text-sm font-bold text-white hover:bg-slate-800 disabled:opacity-50"
          >
            <FloppyDisk size={17} /> {saving ? "Salvataggio…" : "Salva controllo"}
          </button>
        </div>
        {errors.length > 0 && (
          <div className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
            <div className="font-bold">Errori da correggere: {errors.length}</div>
            <ul className="mt-1 list-disc pl-5 text-xs">
              {errors.slice(0, 20).map((error, index) => <li key={`${error.riga}-${index}`}>Riga {error.riga}: {error.errore}</li>)}
            </ul>
          </div>
        )}
      </Card>

      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <KpiCard label="Prodotti controllati" value={formatNum(summary.products)} />
        <KpiCard label="Pezzi SmartVenue" value={formatNum(summary.smartVenue)} />
        <KpiCard label="Righe da verificare" value={formatNum(summary.differences)} tone={summary.differences ? "warning" : "ok"} />
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <SearchBar
          data-testid="smart-venue-control-search"
          value={query}
          onChange={setQuery}
          placeholder="Cerca codice, descrizione o barcode…"
          className="max-w-lg flex-1"
        />
        <span className="text-sm text-slate-500">{visibleRows.length} risultati</span>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full min-w-[1120px]">
            <thead>
              <tr>
                <Th sortKey="codice" currentKey={sortKey} dir={sortDir} onClick={toggle}>Codice</Th>
                <Th sortKey="descrizione" currentKey={sortKey} dir={sortDir} onClick={toggle}>Descrizione</Th>
                <Th sortKey="smart_venue" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">SmartVenue reale</Th>
                <Th sortKey="giacenza_vending" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Vending reale</Th>
                <Th sortKey="giacenza_negozio" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Negozio registrato</Th>
                <Th sortKey="gestionale_totale" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Totale gestionale</Th>
                <Th sortKey="negozio_calcolato" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Negozio calcolato</Th>
                <Th sortKey="differenza" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Differenza</Th>
                <Th sortKey="stato" currentKey={sortKey} dir={sortDir} onClick={toggle}>Stato</Th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map(row => (
                <tr key={row.id || row.codice} data-testid={`smart-venue-control-row-${row.codice}`}>
                  <td className="font-mono font-semibold">{row.codice}</td>
                  <td className="max-w-sm truncate">{row.descrizione}</td>
                  <td className="text-right font-mono font-bold">{formatNum(row.smart_venue)}</td>
                  <td className="text-right font-mono">{formatNum(row.giacenza_vending)}</td>
                  <td className="text-right font-mono">{formatNum(row.giacenza_negozio)}</td>
                  <td className="text-right font-mono">{formatNum(row.gestionale_totale ?? row.rimanenze)}</td>
                  <td className={`text-right font-mono font-bold ${row.negozio_calcolato < 0 ? "text-red-700" : ""}`}>{formatNum(row.negozio_calcolato)}</td>
                  <td className={`text-right font-mono font-bold ${row.differenza > 0 ? "text-emerald-700" : row.differenza < 0 ? "text-red-700" : "text-slate-400"}`}>
                    {row.differenza > 0 ? "+" : ""}{formatNum(row.differenza)}
                  </td>
                  <td><Badge tone={statusTone(row.stato)}>{row.stato || (row.differenza ? "DIFFERENZA" : "OK")}</Badge></td>
                </tr>
              ))}
              {!visibleRows.length && (
                <tr><td colSpan="9" className="py-10 text-center text-slate-500">Incolla e analizza i dati SmartVenue per iniziare il controllo.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
      <div className="mt-4 flex items-center gap-2 text-xs text-slate-500">
        <CheckCircle size={15} className="text-emerald-600" /> La prima versione segnala le differenze senza modificare automaticamente la giacenza negozio.
      </div>
    </Layout>
  );
}
