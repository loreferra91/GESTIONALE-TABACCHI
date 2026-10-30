import { useEffect, useMemo, useState } from "react";
import Layout from "../components/Layout";
import { api, apiErrorMessage } from "../lib/api";
import { Card, KpiCard, Badge, formatEur, formatNum } from "../components/UI";
import { toast } from "sonner";
import { CheckCircle, Clock, FileXls, Plus, Trash, Warning, Lightning } from "@phosphor-icons/react";
import ConfirmDialog from "../components/ConfirmDialog";
import { defaultLotForCategory } from "../lib/categories";

export default function Carico() {
  const [prodotti, setProdotti] = useState([]);
  const [smartVenueCodes, setSmartVenueCodes] = useState(new Set());
  const [smartVenueBarcodes, setSmartVenueBarcodes] = useState(new Map());
  const today = new Date().toISOString().slice(0, 10);
  const [data, setData] = useState(today);
  const [sorgente, setSorgente] = useState("carico manuale");
  const [righe, setRighe] = useState([]); // {codice, descrizione, quantita, prezzo}
  const [q, setQ] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importPreview, setImportPreview] = useState(null);
  const [lastReceipt, setLastReceipt] = useState(null);
  const [recentReceipts, setRecentReceipts] = useState([]);
  const [confirmingReceipt, setConfirmingReceipt] = useState(null);
  const [idempotencyKey, setIdempotencyKey] = useState(() => createIdempotencyKey());

  useEffect(() => {
    Promise.all([
      api.get("/prodotti", { params: { limit: 5000 } }),
      api.get("/ordini", { params: { limit: 100 } }),
      api.get("/smart-venue"),
    ]).then(([productsResponse, receiptsResponse, smartVenueResponse]) => {
      const smartVenueRows = smartVenueResponse.data || [];
      setProdotti(productsResponse.data || []);
      setRecentReceipts((receiptsResponse.data || []).filter(row => row.smart_venue_applicabile));
      setSmartVenueCodes(new Set(smartVenueRows.map(row => row.codice)));
      setSmartVenueBarcodes(new Map(smartVenueRows.map(row => [
        row.codice,
        String(row.barcode || row.barcodes?.[0] || "").trim(),
      ])));
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const reloadRecentReceipts = async () => {
    const response = await api.get("/ordini", { params: { limit: 100 } });
    setRecentReceipts((response.data || []).filter(row => row.smart_venue_applicabile));
  };

  const prodMap = useMemo(() => {
    const m = new Map();
    prodotti.forEach(p => m.set(p.codice, p));
    return m;
  }, [prodotti]);

  const addProd = (p) => {
    setRighe(r => {
      const existing = r.find(x => x.codice === p.codice);
      const lotto = defaultLotForCategory(p.categoria);
      if (existing) return r.map(x => x.codice === p.codice ? { ...x, quantita: (x.quantita || 0) + lotto } : x);
      return [...r, { _uid: `c-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, codice: p.codice, descrizione: p.descrizione, quantita: lotto, prezzo: p.prezzo || 0 }];
    });
    setQ("");
  };

  const addEmpty = () => setRighe(r => [...r, { _uid: `c-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, codice: "", descrizione: "", quantita: 0, prezzo: 0 }]);
  const updateRow = (i, field, val) => setRighe(r => r.map((x, idx) => idx === i ? { ...x, [field]: val } : x));
  const removeRow = (i) => setRighe(r => r.filter((_, idx) => idx !== i));

  const onCodiceBlur = (i) => {
    const codice = (righe[i]?.codice || "").trim();
    if (!codice) return;
    const p = prodMap.get(codice);
    if (p) updateRow(i, "descrizione", righe[i].descrizione || p.descrizione), updateRow(i, "prezzo", righe[i].prezzo || p.prezzo);
  };

  const importFromAO = async () => {
    const r = await api.get("/auto-order");
    const nuove = (r.data.righe || []).map((x, idx) => ({
      _uid: `c-ao-${Date.now()}-${idx}`,
      codice: x.codice, descrizione: x.descrizione, quantita: x.qta_da_ordinare, prezzo: x.prezzo,
    }));
    setRighe(nuove);
    setSorgente("da auto-order (modificato)");
    setImportPreview(null);
    setLastReceipt(null);
    toast.success(`${nuove.length} righe importate dall'Auto-Order — modifica le quantità e conferma`);
  };

  const importAutoOrderExcel = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      toast.error("Seleziona il file .xlsx esportato da Auto-Order");
      return;
    }
    setImporting(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await api.post("/ordini/import-auto-order/preview", formData);
      const preview = response.data;
      const importedRows = (preview.righe || []).map((row, index) => {
        const product = prodMap.get(row.codice);
        return {
          _uid: `c-xlsx-${Date.now()}-${index}`,
          codice: row.codice,
          descrizione: row.descrizione || product?.descrizione || row.codice,
          quantita: row.quantita,
          prezzo: row.prezzo || product?.prezzo || 0,
        };
      });
      setRighe(importedRows);
      setSorgente(`Auto-Order Excel · ${preview.file}`);
      setImportPreview(preview);
      setLastReceipt(null);
      setIdempotencyKey(createIdempotencyKey());
      toast.success(`${importedRows.length} prodotti importati · controlla il riepilogo e conferma l'ordine`);
    } catch (error) {
      toast.error(apiErrorMessage(error, "Impossibile leggere l'Excel Auto-Order"));
    } finally {
      setImporting(false);
    }
  };

  const totale = righe.reduce((s, r) => s + (r.quantita || 0) * (r.prezzo || 0), 0);
  const pezziTot = righe.reduce((s, r) => s + (r.quantita || 0), 0);
  const nonRiconosciute = righe.filter(r => r.codice && !prodMap.has(r.codice.trim())).length;

  const filtered = useMemo(() => {
    if (!q) return [];
    const lc = q.toLowerCase();
    return prodotti.filter(p => p.codice.toLowerCase().includes(lc) || (p.descrizione || "").toLowerCase().includes(lc)).slice(0, 8);
  }, [q, prodotti]);

  const carica = async () => {
    const valide = righe.filter(r => r.codice && r.quantita > 0);
    if (!valide.length) return toast.error("Nessuna riga valida");
    setSaving(true);
    try {
      const r = await api.post("/ordini/bulk", {
        file_sorgente: sorgente, data: new Date(data).toISOString(),
        idempotency_key: idempotencyKey,
        righe: valide,
      });
      toast.success(r.data.gia_registrato
        ? "Carico già registrato: nessuna quantità duplicata"
        : `${r.data.caricate} righe caricate · ${r.data.prodotti_nuovi} prodotti nuovi creati`);
      setLastReceipt({
        ...r.data,
        file: importPreview?.file || "",
        fornitore: importPreview?.fornitore || "",
        sorgente,
        prodotti: valide.length,
        pezzi: valide.reduce((sum, row) => sum + (row.quantita || 0), 0),
        totale: valide.reduce((sum, row) => sum + (row.quantita || 0) * (row.prezzo || 0), 0),
      });
      setRighe([]);
      setImportPreview(null);
      setIdempotencyKey(createIdempotencyKey());
      setConfirmOpen(false);
      await reloadRecentReceipts();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Errore durante il carico"));
    } finally {
      setSaving(false);
    }
  };

  const confirmSmartVenueReceipt = async receipt => {
    setConfirmingReceipt(receipt.id);
    try {
      const response = await api.post(`/ordini/${encodeURIComponent(receipt.id)}/conferma-smart-venue`);
      setRecentReceipts(current => current.map(item => item.id === receipt.id ? response.data : item));
      toast.success(response.data.gia_confermato ? "Carico già confermato" : "Caricamento esterno Smart Venue confermato");
    } catch (error) {
      toast.error(apiErrorMessage(error, "Impossibile confermare il caricamento Smart Venue"));
    } finally {
      setConfirmingReceipt(null);
    }
  };

  return (
    <Layout title="Carico Merce" subtitle="registra il ricevuto dal fornitore">
      <Card className="p-5 mb-6 bg-slate-50 border-slate-200">
        <div className="flex items-start gap-3">
          <Warning size={20} className="text-slate-600 mt-0.5" />
          <div className="text-sm text-slate-700 leading-relaxed">
            <strong>Come funziona:</strong> costruisci qui la lista di ciò che hai <b>ricevuto realmente</b> dal fornitore (può essere diverso dall'Auto-Order). Alla conferma:
            <ul className="list-disc list-inside mt-1 space-y-0.5 text-slate-600">
              <li>ogni riga viene registrata in <b>Storico ordini</b> con la data indicata,</li>
              <li>la <b>giacenza negozio</b> e i totali <b>acquistati</b> vengono incrementati,</li>
              <li>per i prodotti Smart Venue viene creato un <b>carico da inserire</b>, senza modificare subito il saldo esterno,</li>
              <li>la conferma dell'inserimento nel software esterno aggiorna il saldo Smart Venue una sola volta,</li>
              <li>codici sconosciuti creano un nuovo prodotto automaticamente.</li>
            </ul>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <KpiCard label="Righe carico" value={formatNum(righe.length)} />
        <KpiCard label="Pezzi totali" value={formatNum(pezziTot)} />
        <KpiCard label="Codici sconosciuti" value={formatNum(nonRiconosciute)} tone={nonRiconosciute ? "warning" : "default"} />
        <KpiCard label="Totale carico" value={formatEur(totale)} tone="success" />
      </div>

      <Card className="p-4 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-5 gap-3 mb-4">
          <div>
            <label className="text-xs font-bold uppercase tracking-wider text-slate-500 block mb-1">Data carico</label>
            <input data-testid="carico-data" type="date" value={data} onChange={e => setData(e.target.value)} className="border rounded-md px-3 py-2 text-sm w-full" />
          </div>
          <div className="md:col-span-2">
            <label className="text-xs font-bold uppercase tracking-wider text-slate-500 block mb-1">Sorgente / Bolla</label>
            <input data-testid="carico-sorgente" value={sorgente} onChange={e => setSorgente(e.target.value)} placeholder="es. ORDINE TABACCHI 18-02.xlsx / Bolla n.123" className="border rounded-md px-3 py-2 text-sm w-full" />
          </div>
          <div className="md:col-span-2 flex flex-wrap items-end gap-2">
            <label data-testid="carico-import-excel-label" className={`flex cursor-pointer items-center gap-2 rounded-md border border-emerald-700 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-800 transition-colors hover:bg-emerald-100 ${importing ? "pointer-events-none opacity-50" : ""}`}>
              <FileXls size={17} weight="bold" /> {importing ? "Lettura Excel…" : "Importa Excel Auto-Order"}
              <input data-testid="carico-import-excel" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={importAutoOrderExcel} className="sr-only" disabled={importing} />
            </label>
            <button data-testid="carico-import-ao" onClick={importFromAO} className="flex items-center gap-2 bg-white border border-slate-300 text-slate-900 rounded-md px-3 py-2 text-sm hover:bg-slate-50 transition-colors">
              <Lightning size={16} /> Precompila da Auto-Order
            </button>
            <button data-testid="carico-add-empty" onClick={addEmpty} className="flex items-center gap-2 bg-white border border-slate-300 text-slate-900 rounded-md px-3 py-2 text-sm hover:bg-slate-50 transition-colors">
              <Plus size={16} /> Riga vuota
            </button>
          </div>
        </div>

        {importPreview && (
          <div data-testid="carico-import-summary" className="mb-4 rounded-md border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-950">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="font-bold">Excel Auto-Order pronto per il carico</div>
                <div className="mt-1 text-emerald-800">{importPreview.file}{importPreview.fornitore ? ` · ${importPreview.fornitore}` : ""}{importPreview.data_ordine ? ` · ordine del ${formatImportDate(importPreview.data_ordine)}` : ""}</div>
              </div>
              <Badge tone="ok">DA CONFERMARE</Badge>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <div><span className="block text-xs uppercase text-emerald-700">Prodotti</span><b>{formatNum(righe.length)}</b></div>
              <div><span className="block text-xs uppercase text-emerald-700">Pezzi</span><b>{formatNum(pezziTot)}</b></div>
              <div><span className="block text-xs uppercase text-emerald-700">Valore</span><b>{formatEur(totale)}</b></div>
              <div><span className="block text-xs uppercase text-emerald-700">Anomalie</span><b>{formatNum(nonRiconosciute + (importPreview.avvisi?.length || 0))}</b></div>
            </div>
            {importPreview.avvisi?.length > 0 && <ul className="mt-3 list-disc pl-5 text-xs text-amber-800">{importPreview.avvisi.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul>}
          </div>
        )}

        <div className="relative">
          <label className="text-xs font-bold uppercase tracking-wider text-slate-500 block mb-1">Aggiungi prodotto (cerca per codice o descrizione)</label>
          <input data-testid="carico-search" value={q} onChange={e => setQ(e.target.value)} placeholder="TEREA, MARLBORO, ELFBAR…" className="border rounded-md px-3 py-2 text-sm w-full" />
          {filtered.length > 0 && (
            <div className="absolute left-0 right-0 top-full mt-1 bg-white border border-slate-200 rounded-md shadow-lg z-20 max-h-72 overflow-auto">
              {filtered.map(p => (
                <button key={p.id} data-testid={`carico-add-${p.codice}`} onClick={() => addProd(p)} className="w-full text-left px-3 py-2 hover:bg-slate-50 border-b border-slate-100 last:border-b-0 flex justify-between items-center transition-colors">
                  <div>
                    <div className="text-sm font-medium">{p.descrizione}</div>
                    <div className="font-mono text-xs text-slate-500">{p.codice} · {p.categoria}</div>
                  </div>
                  <span className="font-mono text-sm font-semibold">{formatEur(p.prezzo)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </Card>

      <Card className="overflow-hidden mb-6">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <th>Codice</th><th>Descrizione</th>
                <th className="text-right">Quantità</th>
                <th className="text-right">Prezzo</th>
                <th className="text-right">Totale</th>
                <th>Stato</th><th></th>
              </tr>
            </thead>
            <tbody>
              {righe.length === 0 && <tr><td colSpan={7} className="text-center py-12 text-slate-400">Nessuna riga — importa l'Excel Auto-Order, cerca un prodotto o aggiungi una riga vuota.</td></tr>}
              {righe.map((r, i) => {
                const known = r.codice && prodMap.has(r.codice.trim());
                return (
                  <tr key={r._uid || i} className={!known && r.codice ? "bg-red-50" : ""} data-testid={`carico-row-${i}`}>
                    <td>
                      <input value={r.codice} onChange={e => updateRow(i, "codice", e.target.value)} onBlur={() => onCodiceBlur(i)} className="border rounded-md px-2 py-1 text-sm font-mono w-28" data-testid={`carico-row-${i}-codice`} />
                    </td>
                    <td>
                      <input value={r.descrizione} onChange={e => updateRow(i, "descrizione", e.target.value)} className="border rounded-md px-2 py-1 text-sm w-full" />
                    </td>
                    <td className="text-right">
                      <input type="number" min={0} value={r.quantita} onChange={e => updateRow(i, "quantita", parseInt(e.target.value) || 0)} className="border rounded-md px-2 py-1 text-sm font-mono w-20 text-right" data-testid={`carico-row-${i}-qta`} />
                    </td>
                    <td className="text-right">
                      <input type="number" step="0.01" min={0} value={r.prezzo} onChange={e => updateRow(i, "prezzo", parseFloat(e.target.value) || 0)} className="border rounded-md px-2 py-1 text-sm font-mono w-24 text-right" />
                    </td>
                    <td className="text-right font-mono font-semibold">{formatEur((r.quantita || 0) * (r.prezzo || 0))}</td>
                    <td>
                      {r.codice ? (known
                        ? <Badge tone={smartVenueCodes.has(r.codice.trim()) ? "warning" : "ok"}>{smartVenueCodes.has(r.codice.trim()) ? "DA INSERIRE SV" : "NON APPLICABILE"}</Badge>
                        : <Badge tone="error">NUOVO</Badge>)
                       : <span className="text-xs text-slate-400">—</span>}
                    </td>
                    <td className="text-right">
                      <button onClick={() => removeRow(i)} className="text-red-600 hover:text-red-800"><Trash size={14} /></button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="flex justify-end gap-3">
        <button data-testid="carico-clear" onClick={() => { setRighe([]); setImportPreview(null); }} disabled={!righe.length} className="border border-slate-300 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors disabled:opacity-40">
          Svuota
        </button>
        <button data-testid="carico-conferma" onClick={() => setConfirmOpen(true)} disabled={saving || righe.filter(r => r.codice && r.quantita > 0).length === 0} className="bg-emerald-600 hover:bg-emerald-700 text-white rounded-md px-6 py-2 text-sm font-bold transition-colors disabled:opacity-40">
          {saving ? "Carico in corso…" : `Conferma ordine e carico (${formatEur(totale)})`}
        </button>
      </div>

      {lastReceipt && (
        <div data-testid="carico-success-summary">
          <Card className="mt-6 border-emerald-200 bg-emerald-50 p-5">
            <div className="flex items-start gap-3">
              <CheckCircle size={26} weight="fill" className="shrink-0 text-emerald-700" />
              <div className="min-w-0 flex-1">
                <h2 className="font-heading text-lg font-black text-emerald-950">Carico confermato</h2>
                <p className="mt-1 text-sm text-emerald-800">{lastReceipt.file || lastReceipt.sorgente}{lastReceipt.fornitore ? ` · ${lastReceipt.fornitore}` : ""}</p>
                <div className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                  <div><span className="block text-xs uppercase text-emerald-700">Prodotti</span><b>{formatNum(lastReceipt.prodotti)}</b></div>
                  <div><span className="block text-xs uppercase text-emerald-700">Pezzi caricati</span><b>{formatNum(lastReceipt.pezzi)}</b></div>
                  <div><span className="block text-xs uppercase text-emerald-700">Valore</span><b>{formatEur(lastReceipt.totale)}</b></div>
                  <div><span className="block text-xs uppercase text-emerald-700">Nuovi prodotti</span><b>{formatNum(lastReceipt.prodotti_nuovi || 0)}</b></div>
                </div>
                <p className="mt-3 break-all text-xs text-emerald-700">Ricezione {lastReceipt.batch_id} · backup di sicurezza {lastReceipt.backup_id}</p>
              </div>
            </div>
          </Card>
        </div>
      )}

      <Card className="mt-8 overflow-hidden">
        <div className="border-b border-slate-200 px-4 py-3">
          <h2 className="font-bold text-slate-900">Carichi da confermare su Smart Venue</h2>
          <p className="mt-1 text-xs text-slate-500">La conferma registra il carico nel saldo Smart Venue esterno; il fisico è già aggiornato al ricevimento.</p>
        </div>
        <div className="overflow-x-auto max-h-[50vh]">
          <table className="data-table w-full min-w-[900px]">
            <thead><tr><th>Data</th><th>Prodotto</th><th className="text-right">Quantità caricata</th><th>Sorgente</th><th>Barcode</th><th>Caricato su Smart Venue</th></tr></thead>
            <tbody>
              {recentReceipts.map(receipt => <tr key={receipt.id}>
                <td className="whitespace-nowrap font-mono text-xs">{new Date(receipt.data).toLocaleString("it-IT")}</td>
                <td><div className="font-semibold">{receipt.descrizione}</div><div className="font-mono text-xs text-slate-500">{receipt.codice}</div></td>
                <td className="text-right font-mono font-bold">{formatNum(receipt.quantita)}</td>
                <td className="text-xs text-slate-500">{receipt.file_sorgente}</td>
                <td data-testid={`carico-smart-barcode-${receipt.codice}`} className="whitespace-nowrap font-mono text-xs text-slate-700">{smartVenueBarcodes.get(receipt.codice) || "—"}</td>
                <td>
                  {receipt.caricato_smart_venue
                    ? <div><Badge tone="ok">Confermato</Badge><div className="mt-1 text-[11px] text-slate-500">{receipt.caricato_smart_venue_il ? new Date(receipt.caricato_smart_venue_il).toLocaleString("it-IT") : ""}</div></div>
                    : <button type="button" onClick={() => confirmSmartVenueReceipt(receipt)} disabled={confirmingReceipt === receipt.id} className="inline-flex items-center gap-2 rounded-md bg-emerald-600 px-3 py-2 text-xs font-bold text-white hover:bg-emerald-700 disabled:opacity-50">
                        {confirmingReceipt === receipt.id ? <Clock size={15} /> : <CheckCircle size={15} weight="bold" />} {confirmingReceipt === receipt.id ? "Conferma…" : "Conferma caricamento esterno"}
                      </button>}
                </td>
              </tr>)}
              {!recentReceipts.length && <tr><td colSpan="6" className="py-10 text-center text-slate-400">Nessun carico Smart Venue recente da mostrare.</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
      <ConfirmDialog
        open={confirmOpen}
        title="Confermare ordine e carico?"
        confirmLabel="Conferma e aggiorna magazzino"
        loading={saving}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={carica}
      >
        {importPreview && <p className="mb-3 rounded-md bg-slate-100 p-3 text-xs"><b>{importPreview.file}</b>{importPreview.fornitore ? ` · ${importPreview.fornitore}` : ""}<br/>{importPreview.selezione || "Tutte le categorie"}</p>}
        <div className="grid grid-cols-3 gap-2 rounded-md border border-slate-200 p-3 text-center">
          <div><span className="block text-xs text-slate-500">Prodotti</span><b>{formatNum(righe.filter(r => r.codice && r.quantita > 0).length)}</b></div>
          <div><span className="block text-xs text-slate-500">Pezzi</span><b>{formatNum(pezziTot)}</b></div>
          <div><span className="block text-xs text-slate-500">Valore</span><b>{formatEur(totale)}</b></div>
        </div>
        {nonRiconosciute > 0 && <p className="mt-3 font-semibold text-amber-700">Attenzione: {formatNum(nonRiconosciute)} codici non presenti saranno creati come nuovi prodotti.</p>}
        <p className="mt-2 text-slate-600">L'operazione aggiorna <b>Storico ordini</b>, <b>giacenza negozio</b> e <b>acquistati</b>.</p>
      </ConfirmDialog>
    </Layout>
  );
}

function formatImportDate(value) {
  const parsed = new Date(`${String(value).slice(0, 10)}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString("it-IT");
}

function createIdempotencyKey() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `carico-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
