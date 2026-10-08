import { useCallback, useEffect, useState, useRef, useMemo } from "react";
import Layout from "../components/Layout";
import { api, API, apiErrorMessage } from "../lib/api";
import { Card, Badge, formatEur, formatNum } from "../components/UI";
import { toast } from "sonner";
import { ArrowCounterClockwise, ArrowClockwise, Trash } from "@phosphor-icons/react";

function UndoLastPanel({ testId, title, createdAt, description, buttonLabel, busy, disabled, onUndo }) {
  return (
    <div data-testid={testId} className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 p-3">
      <div>
        <div className="text-sm font-bold text-amber-950">{title}</div>
        <div className="mt-0.5 text-xs text-amber-800">
          {createdAt ? `${new Date(createdAt).toLocaleString("it-IT")} · ` : ""}{description}
        </div>
      </div>
      <button
        type="button"
        data-testid={`${testId}-button`}
        onClick={onUndo}
        disabled={busy || disabled}
        aria-busy={busy}
        className="inline-flex items-center justify-center gap-2 rounded-md border border-red-300 bg-white px-4 py-2 text-sm font-bold text-red-700 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <ArrowCounterClockwise size={17} className={busy ? "animate-spin" : ""} aria-hidden="true" />
        {busy ? "Annullamento…" : buttonLabel}
      </button>
    </div>
  );
}

export default function Vendite() {
  const [rows, setRows] = useState([]);
  const [giorno, setGiorno] = useState(new Date().toISOString().slice(0, 10));
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = useState({ data: today, codice: "", descrizione: "", quantita: 1, importo: 0, canale: "NEGOZIO", pagamento: "CONTANTI", colonna: "" });
  const [vendingColumns, setVendingColumns] = useState([]);
  const [tab, setTab] = useState("manuale"); // manuale | bulk | csv
  const [bulkText, setBulkText] = useState("");
  const [bulkCanale, setBulkCanale] = useState("NEGOZIO");
  const [bulkPagamento, setBulkPagamento] = useState("CONTANTI");
  const [bulkData, setBulkData] = useState(today);
  const [bulkResult, setBulkResult] = useState(null);
  const [bulkRows, setBulkRows] = useState([]); // canonical editable rows for preview
  const [bulkImporting, setBulkImporting] = useState(false);
  const [lastBulkImport, setLastBulkImport] = useState(undefined);
  const [bulkUndoing, setBulkUndoing] = useState(false);
  const [lastManualSale, setLastManualSale] = useState(undefined);
  const [manualUndoing, setManualUndoing] = useState(false);
  const csvRef = useRef(null);
  const [csvResult, setCsvResult] = useState(null);
  const [csvPag, setCsvPag] = useState("CONTANTI");
  const [csvFile, setCsvFile] = useState(null);
  const [csvPreview, setCsvPreview] = useState(null);
  const [csvPreviewing, setCsvPreviewing] = useState(false);
  const [csvImporting, setCsvImporting] = useState(false);
  const [lastCsvImport, setLastCsvImport] = useState(undefined);
  const [csvUndoing, setCsvUndoing] = useState(false);
  const [prodMap, setProdMap] = useState(new Map());

  useEffect(() => {
    api.get("/prodotti", { params: { limit: 5000 } }).then(r => {
      const m = new Map();
      (r.data || []).forEach(p => m.set(p.codice, p));
      setProdMap(m);
    });
    api.get("/vending").then(r => setVendingColumns(r.data || []));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(async () => {
    const r = await api.get("/vendite", { params: { giorno } });
    setRows(r.data);
  }, [giorno]);
  useEffect(() => { load(); }, [load]);

  const loadLastBulkImport = useCallback(async () => {
    try {
      const r = await api.get("/vendite/bulk/ultimo");
      setLastBulkImport(r.data || null);
    } catch (error) {
      console.error("loadLastBulkImport failed:", error);
      setLastBulkImport(null);
    }
  }, []);
  useEffect(() => { loadLastBulkImport(); }, [loadLastBulkImport]);

  const loadLastManualSale = useCallback(async () => {
    try {
      const r = await api.get("/vendite/manuale/ultima");
      setLastManualSale(r.data || null);
    } catch (error) {
      console.error("loadLastManualSale failed:", error);
      setLastManualSale(null);
    }
  }, []);
  useEffect(() => { loadLastManualSale(); }, [loadLastManualSale]);

  const loadLastCsvImport = useCallback(async () => {
    try {
      const r = await api.get("/vendite/csv/ultimo");
      setLastCsvImport(r.data || null);
    } catch (error) {
      console.error("loadLastCsvImport failed:", error);
      setLastCsvImport(null);
    }
  }, []);
  useEffect(() => { loadLastCsvImport(); }, [loadLastCsvImport]);

  const save = async () => {
    if (manualUndoing) return;
    if (!form.codice || !form.importo) { toast.error("Compila codice e importo"); return; }
    if (form.canale === "VENDING" && !form.colonna) { toast.error("Seleziona la colonna Vending"); return; }
    const r = await api.post("/vendite", form);
    setLastManualSale(r.data);
    toast.success("Vendita registrata");
    setForm({ ...form, codice: "", descrizione: "", quantita: 1, importo: 0, colonna: "" });
    await load();
  };

  const undoLastManualSale = async () => {
    if (!lastManualSale?.id || manualUndoing) return;
    if (!window.confirm(`Annullare l'ultima vendita manuale (${lastManualSale.codice}, ${formatEur(lastManualSale.importo)})?`)) return;
    setManualUndoing(true);
    try {
      await api.post(`/vendite/manuale/${lastManualSale.id}/annulla`);
      await Promise.all([load(), loadLastManualSale()]);
      toast.success("Ultima vendita manuale annullata e scorte ripristinate");
    } catch (error) {
      toast.error(apiErrorMessage(error, "Impossibile annullare la vendita manuale"));
    } finally {
      setManualUndoing(false);
    }
  };

  const del = async (id) => {
    if (!window.confirm("Eliminare?")) return;
    await api.delete(`/vendite/${id}`);
    toast.success("Eliminata");
    load();
  };

  const lookupPrezzo = async () => {
    if (!form.codice) return;
    try {
      const r = await api.get("/prodotti", { params: { q: form.codice } });
      const p = (r.data || []).find(x => x.codice === form.codice);
      if (p) setForm(f => ({ ...f, descrizione: p.descrizione, importo: p.prezzo * (f.quantita || 1) }));
    } catch (err) {
      console.error("lookupPrezzo failed:", err);
    }
  };

  const tot = rows.reduce((s, r) => s + (r.importo || 0), 0);
  const pezzi = rows.reduce((s, r) => s + (r.quantita || 0), 0);

  const parseBulk = (text, defaultData) => {
    const raw = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    if (!raw.length) return [];

    // Rileva "formato verticale": ogni cella su una riga separata (Excel a volte incolla così se si copia una singola colonna alla volta o più colonne di una sola riga)
    // Se ogni riga contiene un solo campo (no TAB/;/,) e il numero totale è multiplo di 4 o 5, raggruppiamo.
    const singleField = raw.every(l => !/\t|;|,/.test(l));
    const groups = [];
    if (singleField && (raw.length % 4 === 0 || raw.length % 5 === 0)) {
      const step = raw.length % 5 === 0 ? 5 : 4;
      for (let i = 0; i < raw.length; i += step) groups.push(raw.slice(i, i + step));
    } else {
      // Formato orizzontale classico. Excel usa TAB e mantiene la virgola
      // decimale: scegliamo un solo delimitatore per non spezzare 6,00.
      for (const line of raw) {
        const delimiter = line.includes("\t") ? "\t" : (line.includes(";") ? ";" : ",");
        groups.push(line.split(delimiter).map(x => x.trim()));
      }
    }

    return groups.map((parts, idx) => {
      let data, codice, descrizione, qta, importo;
      if (parts.length >= 5) [data, codice, descrizione, qta, importo] = parts;
      else if (parts.length === 4) { [codice, descrizione, qta, importo] = parts; data = defaultData; }
      else if (parts.length === 3) { [codice, qta, importo] = parts; descrizione = ""; data = defaultData; }
      else return null;
      return {
        _uid: `bulk-${Date.now()}-${idx}-${Math.random().toString(36).slice(2, 8)}`,
        data: data || defaultData,
        codice: (codice || "").trim(),
        descrizione: (descrizione || "").trim(),
        quantita: parseInt(String(qta || "0").replace(/[^\d-]/g, "")) || 0,
        importo: parseFloat(String(importo || "0").replace(",", ".").replace(/[^\d.-]/g, "")) || 0,
      };
    }).filter(Boolean);
  };

  const submitBulk = async () => {
    if (bulkImporting || bulkUndoing) return;
    const righe = bulkRows.filter(r => r.codice && r.quantita > 0);
    if (!righe.length) return toast.error("Nessuna riga valida");
    setBulkImporting(true);
    try {
      const r = await api.post("/vendite/bulk", { canale: bulkCanale, pagamento: bulkPagamento, righe: righe.map(x => ({ data: x.data, codice: x.codice, descrizione: x.descrizione, quantita: x.quantita, importo: x.importo })) });
      setBulkResult(r.data);
      if (r.data.batch_id) setLastBulkImport({ id: r.data.batch_id, ...r.data, status: "active" });
      setBulkText("");
      setBulkRows([]);
      await load();
      toast.success(`Bulk: ${r.data.inseriti} inserite, ${r.data.saltati} saltate`);
    } catch (e) {
      toast.error(apiErrorMessage(e, "Errore bulk"));
    } finally {
      setBulkImporting(false);
    }
  };

  const undoLastBulkImport = async () => {
    if (!lastBulkImport?.id || bulkUndoing || bulkImporting) return;
    const count = lastBulkImport.inseriti || 0;
    if (!window.confirm(`Annullare l'ultimo caricamento di ${count} vendite? Le quantità di magazzino verranno ripristinate.`)) return;
    setBulkUndoing(true);
    try {
      const r = await api.post(`/vendite/bulk/${lastBulkImport.id}/annulla`);
      setBulkResult(null);
      await Promise.all([load(), loadLastBulkImport()]);
      toast.success(`Caricamento annullato: ${r.data.rimossi} vendite rimosse e scorte ripristinate`);
    } catch (error) {
      toast.error(apiErrorMessage(error, "Impossibile annullare il caricamento"));
    } finally {
      setBulkUndoing(false);
    }
  };

  // Ri-parsing automatico quando cambia testo o data default
  useEffect(() => {
    const parsed = parseBulk(bulkText, bulkData);
    setBulkRows(parsed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bulkText, bulkData]);

  // Deriva stato in tempo reale
  const rowsWithStatus = useMemo(() => bulkRows.map(r => {
    const trimmed = (r.codice || "").trim();
    const known = trimmed && prodMap.has(trimmed);
    const stato = !trimmed ? "vuoto" : !known ? "sconosciuto" : (r.quantita <= 0 ? "qta zero" : (r.importo <= 0 ? "importo zero" : "ok"));
    return { ...r, _known: known, _stato: stato };
  }), [bulkRows, prodMap]);
  const bulkTotals = useMemo(() => rowsWithStatus.reduce((acc, row) => {
    if (row.quantita > 0) acc.pezzi += row.quantita;
    if (row.importo > 0) acc.importo += row.importo;
    return acc;
  }, { pezzi: 0, importo: 0 }), [rowsWithStatus]);

  const updateBulkRow = (i, field, val) => {
    setBulkRows(rs => rs.map((r, idx) => idx === i ? { ...r, [field]: val } : r));
  };
  const removeBulkRow = (i) => {
    setBulkRows(rs => rs.filter((_, idx) => idx !== i));
  };
  const clearBulk = () => {
    setBulkRows([]);
    setBulkText("");
    setBulkResult(null);
  };

  const previewCsv = async (file, defaultPayment = csvPag) => {
    if (!file) return;
    setCsvPreviewing(true);
    setCsvPreview(null);
    setCsvResult(null);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await fetch(`${API}/vendite/preview-csv-vending?pagamento=${encodeURIComponent(defaultPayment)}`, { method: "POST", body: fd });
      if (!r.ok) throw new Error((await r.text()) || r.statusText);
      const j = await r.json();
      setCsvPreview(j);
      if (!j.righe) toast.info("Nessuna nuova vendita da importare");
    } catch (err) {
      setCsvFile(null);
      if (csvRef.current) csvRef.current.value = "";
      toast.error(apiErrorMessage(err, "Impossibile leggere il CSV"));
    } finally {
      setCsvPreviewing(false);
    }
  };

  const selectCsv = (e) => {
    const file = e.target.files?.[0] || null;
    setCsvFile(file);
    if (file) previewCsv(file);
  };

  const changeCsvDefaultPayment = (value) => {
    setCsvPag(value);
    if (csvFile) previewCsv(csvFile, value);
  };

  const clearCsv = () => {
    setCsvFile(null);
    setCsvPreview(null);
    if (csvRef.current) csvRef.current.value = "";
  };

  const uploadCsv = async () => {
    if (!csvFile || !csvPreview?.contabilita_csv?.righe || !csvPreview?.importabile || csvUndoing) return;
    setCsvImporting(true);
    const fd = new FormData();
    fd.append("file", csvFile);
    try {
      const r = await fetch(`${API}/vendite/import-csv-vending?pagamento=${encodeURIComponent(csvPag)}`, { method: "POST", body: fd });
      if (!r.ok) throw new Error((await r.text()) || r.statusText);
      const j = await r.json();
      setCsvResult(j);
      if (j.batch_id) setLastCsvImport({ id: j.batch_id, ...j, status: "active" });
      toast.success(j.inseriti
        ? `CSV vending: ${j.inseriti} vendite importate, ${j.righe_da_scalare || 0} applicate alla giacenza; contabilità allineata`
        : "Nessuna nuova vendita; contabilità vending allineata al CSV completo"
      );
      clearCsv();
      await load();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Errore import CSV"));
    } finally {
      setCsvImporting(false);
    }
  };

  const undoLastCsvImport = async () => {
    if (!lastCsvImport?.id || csvUndoing || csvImporting) return;
    const count = lastCsvImport.inseriti || 0;
    if (!window.confirm(`Annullare l'ultimo CSV di ${count} vendite? Verranno ripristinate scorte vending e cassa contanti.`)) return;
    setCsvUndoing(true);
    try {
      const r = await api.post(`/vendite/csv/${lastCsvImport.id}/annulla`);
      setCsvResult(null);
      await Promise.all([load(), loadLastCsvImport()]);
      toast.success(`CSV annullato: ${r.data.rimossi} vendite rimosse e valori ripristinati`);
    } catch (error) {
      toast.error(apiErrorMessage(error, "Impossibile annullare il CSV"));
    } finally {
      setCsvUndoing(false);
    }
  };

  return (
    <Layout title="Vendite giornaliere" subtitle={`Registrazioni del ${giorno}`}>
      {/* Tab switcher */}
      <div className="flex gap-1 border-b border-slate-200 mb-6">
        {[
          { k: "manuale", l: "Inserimento manuale" },
          { k: "bulk", l: "Bulk paste da Excel" },
          { k: "csv", l: "Importa CSV Vending" },
        ].map(t => (
          <button
            key={t.k}
            data-testid={`vend-tab-${t.k}`}
            onClick={() => setTab(t.k)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px ${
              tab === t.k ? "border-slate-900 text-slate-900" : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            {t.l}
          </button>
        ))}
      </div>

      {tab === "manuale" && (
      <Card className="p-4 mb-6">
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3">
          <input data-testid="vend-data" type="date" value={form.data} onChange={e => setForm({...form, data: e.target.value})} className="border rounded-md px-3 py-2 text-sm" />
          <input data-testid="vend-codice" placeholder="Codice" value={form.codice} onChange={e => setForm({...form, codice: e.target.value})} onBlur={lookupPrezzo} className="border rounded-md px-3 py-2 text-sm font-mono" />
          <input data-testid="vend-desc" placeholder="Descrizione" value={form.descrizione} onChange={e => setForm({...form, descrizione: e.target.value})} className="border rounded-md px-3 py-2 text-sm col-span-2" />
          <input data-testid="vend-qta" type="number" placeholder="Qta" value={form.quantita} onChange={e => setForm({...form, quantita: parseInt(e.target.value) || 1})} className="border rounded-md px-3 py-2 text-sm font-mono" />
          <input data-testid="vend-importo" type="number" step="0.01" placeholder="Importo €" value={form.importo} onChange={e => setForm({...form, importo: parseFloat(e.target.value) || 0})} className="border rounded-md px-3 py-2 text-sm font-mono" />
          <button data-testid="vend-save-btn" onClick={save} disabled={manualUndoing} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors disabled:opacity-60 col-span-2 md:col-span-1">Registra</button>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 md:max-w-2xl">
          <select data-testid="vend-canale" value={form.canale} onChange={e => setForm({...form, canale: e.target.value, colonna: e.target.value === "VENDING" ? form.colonna : ""})} className="border rounded-md px-3 py-2 text-sm">
            <option>NEGOZIO</option><option>VENDING</option>
          </select>
          <select data-testid="vend-pagamento" value={form.pagamento} onChange={e => setForm({...form, pagamento: e.target.value})} className="border rounded-md px-3 py-2 text-sm">
            <option>CONTANTI</option><option>CARTE</option><option>PAGOBANCOMAT</option><option>POS</option><option>SATISPAY</option><option>ALTRO</option>
          </select>
          {form.canale === "VENDING" && (
            <select
              data-testid="vend-colonna"
              value={form.colonna}
              onChange={e => {
                const column = vendingColumns.find(item => item.colonna === e.target.value);
                setForm(current => ({
                  ...current,
                  colonna: e.target.value,
                  codice: column?.codice || current.codice,
                  descrizione: column?.descrizione || current.descrizione,
                }));
              }}
              className="col-span-2 rounded-md border px-3 py-2 text-sm"
            >
              <option value="">Seleziona colonna Vending…</option>
              {vendingColumns.map(column => (
                <option key={column.id} value={column.colonna}>
                  {column.colonna} · {column.codice} · {column.descrizione} ({column.giacenza} pz)
                </option>
              ))}
            </select>
          )}
        </div>
        <UndoLastPanel
          testId="manual-last-sale"
          title={lastManualSale === undefined
            ? "Verifica dell'ultima vendita…"
            : lastManualSale
              ? `Ultima vendita manuale: ${lastManualSale.codice} · ${formatEur(lastManualSale.importo)}`
              : "Nessuna vendita manuale annullabile"}
          createdAt={lastManualSale?.created_at}
          description={lastManualSale
            ? "Rimuove solo questa vendita e ripristina la relativa scorta."
            : "Il comando sarà disponibile dopo la prossima vendita manuale."}
          buttonLabel="Annulla ultima vendita"
          busy={manualUndoing}
          disabled={!lastManualSale}
          onUndo={undoLastManualSale}
        />
      </Card>
      )}

      {tab === "bulk" && (
      <Card className="p-6 mb-6">
        <h2 className="font-heading font-black text-lg mb-1">Bulk paste da Excel/CSV</h2>
        <p className="text-sm text-slate-600 mb-4">
          Incolla righe copiate da Excel — 3 formati supportati:<br/>
          <span className="inline-block mt-1">1. Colonne su una riga (TAB / <code>;</code> / <code>,</code>): <code className="text-xs bg-slate-100 px-1.5 py-0.5 rounded">data · codice · descrizione · qtà · importo</code> o <code className="text-xs bg-slate-100 px-1.5 py-0.5 rounded">codice · descrizione · qtà · importo</code></span><br/>
          <span className="inline-block mt-1">2. <b>Formato verticale</b> (una cella per riga, blocchi da 4): <code className="text-xs bg-slate-100 px-1.5 py-0.5 rounded">codice ↵ descrizione ↵ qtà ↵ importo</code></span>
        </p>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-3">
          <input data-testid="bulk-data" type="date" value={bulkData} onChange={e => setBulkData(e.target.value)} className="border rounded-md px-3 py-2 text-sm" />
          <select data-testid="bulk-canale" value={bulkCanale} onChange={e => setBulkCanale(e.target.value)} className="border rounded-md px-3 py-2 text-sm">
            <option>NEGOZIO</option>
          </select>
          <select data-testid="bulk-pagamento" value={bulkPagamento} onChange={e => setBulkPagamento(e.target.value)} className="border rounded-md px-3 py-2 text-sm">
            <option>CONTANTI</option><option>POS</option><option>SATISPAY</option><option>ALTRO</option>
          </select>
          <button
            type="button"
            data-testid="bulk-submit"
            onClick={submitBulk}
            disabled={!bulkText.trim() || bulkImporting || bulkUndoing}
            aria-busy={bulkImporting}
            className="inline-flex items-center justify-center gap-2 bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors disabled:cursor-not-allowed disabled:opacity-60"
          >
            <ArrowClockwise size={17} className={bulkImporting ? "animate-spin" : ""} aria-hidden="true" />
            {bulkImporting ? "Aggiornamento…" : "Aggiorna vendite"}
          </button>
        </div>
        <textarea
          data-testid="bulk-text"
          value={bulkText}
          onChange={e => setBulkText(e.target.value)}
          rows={8}
          placeholder="AMMS338	WINSTON BLUE*AST20	5	29.00&#10;AMMS20659	TEREA AZURE	2	11.00&#10;..."
          className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm font-mono"
        />

        <div data-testid="bulk-live-total" className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3">
          <div>
            <div className="text-xs font-black uppercase tracking-wider text-emerald-800">Totale live</div>
            <div className="mt-0.5 text-xs text-emerald-700">Si aggiorna mentre modifichi quantità, importi o righe.</div>
          </div>
          <div className="flex items-baseline gap-4 tabular-nums">
            <span className="text-sm font-bold text-emerald-800">{formatNum(bulkTotals.pezzi)} PZ</span>
            <span className="font-heading text-2xl font-black text-emerald-900">{formatEur(bulkTotals.importo)}</span>
          </div>
        </div>

        {rowsWithStatus.length > 0 && (
          <div className="mt-4">
            <div className="flex items-baseline justify-between mb-2 gap-2 flex-wrap">
              <h3 className="font-heading font-black text-base">Anteprima <span className="text-slate-400 font-normal">({rowsWithStatus.length} righe)</span></h3>
              <div className="flex items-center gap-3">
                <div className="flex gap-2 text-xs">
                  <Badge tone="ok">OK {rowsWithStatus.filter(r => r._stato === "ok").length}</Badge>
                  <Badge tone="error">Sconosciuto {rowsWithStatus.filter(r => r._stato === "sconosciuto").length}</Badge>
                  <Badge tone="warning">Errori {rowsWithStatus.filter(r => ["qta zero", "importo zero", "vuoto"].includes(r._stato)).length}</Badge>
                </div>
                <button data-testid="bulk-clear-all" onClick={clearBulk} className="text-red-600 hover:text-red-800 flex items-center gap-1 text-xs font-bold uppercase tracking-wider">
                  <Trash size={14} /> Svuota tutto
                </button>
              </div>
            </div>
            <div className="border border-slate-200 rounded-md overflow-hidden max-h-[500px] overflow-y-auto">
              <table className="data-table w-full">
                <thead>
                  <tr>
                    <th>#</th><th>Data</th><th>Codice</th><th className="wrap">Descrizione</th>
                    <th className="text-right">Qtà</th><th className="text-right">Importo</th><th>Stato</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {rowsWithStatus.map((r, i) => {
                    const rowClass = r._stato === "sconosciuto" ? "bg-red-50" : r._stato === "ok" ? "" : "bg-amber-50";
                    const tone = r._stato === "ok" ? "ok" : r._stato === "sconosciuto" ? "error" : "warning";
                    const label = r._stato === "ok" ? "OK" : r._stato === "sconosciuto" ? "NON TROVATO" : r._stato.toUpperCase();
                    return (
                      <tr key={r._uid} className={rowClass} data-testid={`bulk-preview-row-${i}`}>
                        <td className="font-mono text-xs text-slate-400">{i + 1}</td>
                        <td className="font-mono text-xs">{r.data}</td>
                        <td>
                          <input
                            data-testid={`bulk-row-${i}-codice`}
                            value={r.codice}
                            onChange={e => updateBulkRow(i, "codice", e.target.value)}
                            className={`border rounded-md px-2 py-1 text-sm font-mono w-28 ${r._stato === "sconosciuto" ? "border-red-300 bg-white" : "border-slate-200 bg-white"}`}
                          />
                        </td>
                        <td className="wrap">
                          <input value={r.descrizione} onChange={e => updateBulkRow(i, "descrizione", e.target.value)} className="border border-slate-200 rounded-md px-2 py-1 text-sm w-full bg-white" />
                        </td>
                        <td className="text-right">
                          <input data-testid={`bulk-row-${i}-quantita`} type="number" min={0} value={r.quantita} onChange={e => updateBulkRow(i, "quantita", parseInt(e.target.value) || 0)} className="border border-slate-200 rounded-md px-2 py-1 text-sm font-mono w-16 text-right bg-white" />
                        </td>
                        <td className="text-right">
                          <input data-testid={`bulk-row-${i}-importo`} type="number" step="0.01" min={0} value={r.importo} onChange={e => updateBulkRow(i, "importo", parseFloat(e.target.value) || 0)} className="border border-slate-200 rounded-md px-2 py-1 text-sm font-mono w-20 text-right bg-white" />
                        </td>
                        <td><Badge tone={tone}>{label}</Badge></td>
                        <td className="text-right">
                          <button data-testid={`bulk-del-${i}`} onClick={() => removeBulkRow(i)} className="text-red-600 hover:text-red-800" title="Elimina riga">
                            <Trash size={14} />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-slate-500 mt-2">
              Puoi <b>modificare il codice</b> di una riga rossa e la validazione si aggiorna in tempo reale. Elimina singole righe con il cestino o svuota tutto in un click.
            </p>
          </div>
        )}

        {bulkResult && (
          <div className="mt-3 text-sm bg-slate-50 border border-slate-200 rounded-md p-3">
            <span className="font-bold text-emerald-700">{bulkResult.inseriti}</span> inserite · <span className="text-slate-600">{bulkResult.saltati}</span> saltate
            {bulkResult.errori?.length > 0 && (
              <details className="mt-2"><summary className="text-red-600 cursor-pointer">Errori ({bulkResult.errori.length})</summary>
                <ul className="text-xs mt-2">{bulkResult.errori.slice(0, 20).map((e, i) => <li key={`br-${e.riga}-${i}`}>Riga {e.riga}: {e.errore}</li>)}</ul>
              </details>
            )}
          </div>
        )}

        <UndoLastPanel
          testId="bulk-last-import"
          title={lastBulkImport === undefined
            ? "Verifica dell'ultimo caricamento…"
            : lastBulkImport
              ? `Ultimo caricamento: ${formatNum(lastBulkImport.inseriti || 0)} ${(lastBulkImport.inseriti || 0) === 1 ? "vendita" : "vendite"}`
              : "Nessun caricamento annullabile"}
          createdAt={lastBulkImport?.created_at}
          description={lastBulkImport
            ? "Rimuove solo questo invio e ripristina le scorte."
            : "Il comando sarà disponibile dopo il prossimo caricamento. I caricamenti precedenti all'aggiornamento non possono essere annullati in blocco in sicurezza."}
          buttonLabel="Annulla ultimo caricamento"
          busy={bulkUndoing}
          disabled={!lastBulkImport || bulkImporting}
          onUndo={undoLastBulkImport}
        />
      </Card>
      )}

      {tab === "csv" && (
      <Card className="p-6 mb-6">
        <h2 className="font-heading font-black text-lg mb-1">Importa CSV distributore vending</h2>
        <p className="text-sm text-slate-600 mb-4">
          Carica il file CSV esportato dal distributore. Colonne riconosciute (case-insensitive, separatore <code>,</code> <code>;</code> o TAB):<br/>
          <code className="text-xs bg-slate-100 px-1.5 py-0.5 rounded">data · nome prodotto · prezzo · colonna · codice AAMS · categoria · pagamento</code>
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-xs text-slate-500">
            Pagamento predefinito se assente
          </label>
          <select data-testid="csv-pag" value={csvPag} onChange={e => changeCsvDefaultPayment(e.target.value)} disabled={csvPreviewing || csvImporting || csvUndoing} className="border rounded-md px-3 py-2 text-sm">
            <option>CONTANTI</option><option>POS</option><option>SATISPAY</option><option>ALTRO</option>
          </select>
          <input ref={csvRef} data-testid="csv-file" type="file" accept=".csv,text/csv" onChange={selectCsv} disabled={csvPreviewing || csvImporting || csvUndoing} className="text-sm" />
          {csvPreviewing && <span className="text-sm text-slate-500">Analisi file…</span>}
        </div>
        {csvPreview && (
          <div data-testid="csv-preview" className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="font-bold text-slate-900">Nuove vendite riconosciute: {formatNum(csvPreview.righe)}</div>
                <div className="mt-1 text-sm text-slate-600">
                  {formatEur(csvPreview.totale)} · dal {csvPreview.data_da || "—"} al {csvPreview.data_a || "—"}
                </div>
                {csvPreview.ultima_vendita_excel && (
                  <div className="mt-1 text-xs text-slate-500">
                    Dopo l'ultima vendita Excel del {new Date(csvPreview.ultima_vendita_excel).toLocaleString("it-IT")} · {formatNum(csvPreview.righe_storico_excel || 0)} righe storiche escluse
                  </div>
                )}
                <div className="mt-1 text-xs text-slate-500">
                  Già importate da CSV: {formatNum(csvPreview.righe_csv_gia_importate || 0)} · già comprese nella giacenza rilevata: {formatNum(csvPreview.righe_gia_comprese_nella_giacenza || 0)} · da scalare: {formatNum(csvPreview.righe_da_scalare || 0)}
                </div>
              </div>
              <div className="flex gap-2">
                <button data-testid="csv-cancel" onClick={clearCsv} disabled={csvImporting || csvUndoing} className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm hover:bg-slate-100 disabled:opacity-40">Annulla</button>
                <button data-testid="csv-confirm" onClick={uploadCsv} disabled={csvImporting || csvUndoing || !csvPreview.contabilita_csv?.righe || !csvPreview.importabile} className="rounded-md bg-slate-900 px-4 py-2 text-sm font-bold text-white hover:bg-slate-800 disabled:opacity-40">
                  {csvImporting ? "Aggiornamento…" : csvPreview.righe ? "Conferma vendite e contabilità" : "Allinea solo contabilità"}
                </button>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
              <span className="font-bold uppercase tracking-wider text-slate-500">Pagamenti rilevati</span>
              {Object.entries(csvPreview.pagamenti || {}).map(([payment, count]) => (
                <Badge key={payment} tone={payment === "CONTANTI" ? "ok" : "info"}>
                  {payment}: {formatNum(count)} · {formatEur(csvPreview.importi_pagamenti?.[payment] || 0)}
                </Badge>
              ))}
            </div>
            <div className="mt-3 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900">
              Contabilità completa del CSV: {formatNum(csvPreview.contabilita_csv?.righe || 0)} movimenti · {formatEur(csvPreview.contabilita_csv?.totale || 0)}.
              Verrà aggiornata insieme alle vendite, usando i totali completi del file.
            </div>
            {csvPreview.vendite?.length > 0 && (
              <div className="mt-4 max-h-80 overflow-auto rounded-md border border-slate-200 bg-white">
                <table className="w-full min-w-[760px] text-sm">
                  <thead className="sticky top-0 bg-slate-100 text-left text-xs uppercase tracking-wider text-slate-500">
                    <tr>
                      <th className="px-3 py-2">Data e ora</th>
                      <th className="px-3 py-2">Prodotto</th>
                      <th className="px-3 py-2">Codice</th>
                      <th className="px-3 py-2">Colonna</th>
                      <th className="px-3 py-2 text-right">Prezzo</th>
                      <th className="px-3 py-2">Pagamento</th>
                      <th className="px-3 py-2">Effetto giacenza</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {csvPreview.vendite.map((sale, index) => (
                      <tr key={`${sale.riga}-${index}`} data-testid={`csv-preview-row-${index}`}>
                        <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{new Date(sale.data).toLocaleString("it-IT")}</td>
                        <td className="px-3 py-2">{sale.nome || "—"}</td>
                        <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{sale.codice || "—"}</td>
                        <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{sale.colonna || "—"}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{formatEur(sale.prezzo)}</td>
                        <td className="whitespace-nowrap px-3 py-2">{sale.pagamento}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-xs">
                          {sale.scala_giacenza ? "Scala 1 pezzo" : sale.giacenza_motivo === "GIA_COMPRESA_NELLA_GIACENZA" ? "Già compresa" : "Da verificare"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {csvPreview.righe === 0 && (
              <div className="mt-4 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
                Nessuna nuova vendita: tutte le righe sono già presenti nello storico Excel o in un precedente CSV.
              </div>
            )}
            {!csvPreview.importabile && (
              <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                Import bloccato per proteggere la giacenza.
                {csvPreview.colonne_senza_riferimento_giacenza?.length > 0 && <> Manca la data della rilevazione per: {csvPreview.colonne_senza_riferimento_giacenza.join(", ")}.</>}
                {csvPreview.errori_giacenza?.length > 0 && <> Alcune vendite successive superano i pezzi disponibili.</>}
              </div>
            )}
            {csvPreview.errori?.length > 0 && (
              <div className="mt-3 text-xs text-red-700">{csvPreview.errori.length} righe con errori non verranno importate.</div>
            )}
          </div>
        )}
        {csvResult && (
          <div className="mt-3 text-sm bg-slate-50 border border-slate-200 rounded-md p-3">
            <span className="font-bold text-emerald-700">{csvResult.inseriti}</span> righe · saltate {csvResult.saltati} · delimitatore <code>{csvResult.delimitatore}</code>
            <div className="mt-2 flex flex-wrap gap-2">
              {Object.entries(csvResult.pagamenti || {}).map(([payment, count]) => (
                <Badge key={payment} tone="info">
                  {payment}: {formatNum(count)} · {formatEur(csvResult.importi_pagamenti?.[payment] || 0)}
                </Badge>
              ))}
            </div>
            {csvResult.errori?.length > 0 && (
              <details className="mt-2"><summary className="text-red-600 cursor-pointer">Errori ({csvResult.errori.length})</summary>
                <ul className="text-xs mt-2">{csvResult.errori.slice(0, 20).map((e, i) => <li key={`cr-${e.riga}-${i}`}>Riga {e.riga}: {e.errore}</li>)}</ul>
              </details>
            )}
          </div>
        )}
        <UndoLastPanel
          testId="csv-last-import"
          title={lastCsvImport === undefined
            ? "Verifica dell'ultimo caricamento…"
            : lastCsvImport
              ? `Ultimo CSV Vending: ${formatNum(lastCsvImport.inseriti || 0)} ${(lastCsvImport.inseriti || 0) === 1 ? "vendita" : "vendite"}`
              : "Nessun caricamento CSV annullabile"}
          createdAt={lastCsvImport?.created_at}
          description={lastCsvImport
            ? "Ripristina vendite, scorte vending e cassa contanti di questo file."
            : "Il comando sarà disponibile dopo il prossimo CSV. I caricamenti precedenti all'aggiornamento non possono essere annullati in blocco in sicurezza."}
          buttonLabel="Annulla ultimo caricamento"
          busy={csvUndoing}
          disabled={!lastCsvImport || csvImporting}
          onUndo={undoLastCsvImport}
        />
      </Card>
      )}

      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <label className="text-sm text-slate-600">Filtra giorno:</label>
          <input data-testid="vend-giorno-filter" type="date" value={giorno} onChange={e => setGiorno(e.target.value)} className="border rounded-md px-3 py-2 text-sm" />
        </div>
        <div className="flex gap-2 items-baseline">
          <Badge tone="info">{formatNum(pezzi)} pz</Badge>
          <span className="font-heading font-black text-xl text-slate-900">{formatEur(tot)}</span>
        </div>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr><th>Ora</th><th>Codice</th><th>Descrizione</th><th className="text-right">Qta</th><th className="text-right">Importo</th><th>Canale</th><th>Pagamento</th><th></th></tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id} data-testid={`vend-row-${r.id}`}>
                  <td className="font-mono text-xs text-slate-500">{r.data.slice(11, 16) || "—"}</td>
                  <td className="font-mono">{r.codice}</td>
                  <td className="max-w-sm truncate">{r.descrizione}</td>
                  <td className="font-mono text-right">{r.quantita}</td>
                  <td className="font-mono text-right font-semibold">{formatEur(r.importo)}</td>
                  <td><Badge tone={r.canale === "VENDING" ? "warning" : "info"}>{r.canale}</Badge></td>
                  <td className="text-xs">{r.pagamento}</td>
                  <td className="text-right"><button onClick={() => del(r.id)} className="text-red-600 hover:text-red-800 text-xs">✕</button></td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={8} className="text-center py-8 text-slate-400">Nessuna vendita per {giorno}</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </Layout>
  );
}
