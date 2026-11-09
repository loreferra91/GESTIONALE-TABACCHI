import { useCallback, useEffect, useRef, useState } from "react";
import Layout from "../components/Layout";
import { api, API, apiErrorMessage } from "../lib/api";
import { Card, KpiCard, Badge, formatEur, formatNum } from "../components/UI";
import { useSortSearch, Th, SearchBar } from "../lib/tableHooks";
import { toast } from "sonner";
import ConfirmDialog from "../components/ConfirmDialog";
import { Plus, Trash2 } from "lucide-react";

const receiptDateLabel = value => {
  const day = String(value || "").slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? day.split("-").reverse().join("/") : "data non disponibile";
};

export default function AutoOrder() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [categoria, setCategoria] = useState("");
  const [mostraEsclusi, setMostraEsclusi] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmedKeys, setConfirmedKeys] = useState(() => new Set());
  const [savedBatchKey, setSavedBatchKey] = useState("");
  const [savedConfirmationKey, setSavedConfirmationKey] = useState("");
  const [pendingOrders, setPendingOrders] = useState([]);
  const [receiptCandidates, setReceiptCandidates] = useState({});
  const [deliveryDrafts, setDeliveryDrafts] = useState({});
  const [supplierChanged, setSupplierChanged] = useState(false);
  const [supplierName, setSupplierName] = useState("");
  const [expectedDelivery, setExpectedDelivery] = useState("");
  const [draftRows, setDraftRows] = useState([]);
  const [selectedToAdd, setSelectedToAdd] = useState("");
  const [productSearch, setProductSearch] = useState("");
  const draftRowsRef = useRef([]);
  const originalDraftRef = useRef("[]");
  const allRighe = draftRows;
  const allEsclusi = data?.esclusi || [];
  const categorieDisponibili = Array.from(new Set([...allRighe, ...allEsclusi].map(r => r.categoria).filter(Boolean))).sort();
  const ordiniCategoria = categoria ? allRighe.filter(r => r.categoria === categoria) : allRighe;
  const draftCodes = new Set(allRighe.map(r => r.codice));
  const esclusiDisponibili = allEsclusi.filter(r => !draftCodes.has(r.codice));
  const esclusiCategoria = categoria ? esclusiDisponibili.filter(r => r.categoria === categoria) : esclusiDisponibili;
  const righeCategoria = mostraEsclusi ? [...ordiniCategoria, ...esclusiCategoria] : ordiniCategoria;
  const totaleCategoria = ordiniCategoria.reduce((totale, r) => totale + (r.totale || 0), 0);
  const missingCosts = ordiniCategoria.filter(row => row.costo_unitario_acquisto == null).length;
  const knownCost = ordiniCategoria.reduce((sum, row) => sum + (row.costo_unitario_acquisto == null ? 0 : Number(row.costo_unitario_acquisto) * Number(row.qta_da_ordinare || 0)), 0);
  const coperturaMin = data?.parametri?.GIORNI_COPERTURA_MIN || 7;
  const breveGg = data?.finestra_breve_gg || 10;
  const lungoGg = data?.finestra_lunga_gg || 30;
  const snapshotKey = data?.snapshot_key || "";
  const draftSignature = JSON.stringify(allRighe.map(r => [r.codice, r.qta_da_ordinare]).sort((a, b) => a[0].localeCompare(b[0])));
  const confirmationKey = `${snapshotKey}:${draftSignature}`;
  const alreadyConfirmed = Boolean(snapshotKey && confirmedKeys.has(confirmationKey));
  const invalidRows = allRighe.filter(row => !Number.isInteger(Number(row.qta_da_ordinare)) || Number(row.qta_da_ordinare) <= 0 || Number(row.qta_da_ordinare) % Math.max(1, Number(row.lotto_ordine) || 1) !== 0);
  const pendingPieces = [...(data?.righe || []), ...(data?.esclusi || [])].reduce((sum, row) => sum + (Number(row.quantita_in_arrivo) || 0) + (Number(row.quantita_senza_data) || 0), 0);
  const dataRiferimento = data?.data_riferimento_domanda
    ? new Date(`${data.data_riferimento_domanda}T00:00:00`).toLocaleDateString("it-IT")
    : null;
  const { rows: righe, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(righeCategoria, {
    initial: "totale", dir: "desc",
    searchFields: ["codice", "descrizione", "categoria", "stato", "motivo"],
  });

  const load = useCallback(async (refresh = false) => {
    if (refresh && JSON.stringify(draftRowsRef.current.map(row => [row.codice, row.qta_da_ordinare])) !== originalDraftRef.current) {
      if (!window.confirm("La bozza contiene modifiche non salvate. Ricalcolare e sostituire la bozza?")) return;
    }
    setLoading(true);
    setError("");
    try {
      const r = await api.get("/auto-order", { timeout: 20000 });
      setData(r.data);
      const nextRows = r.data.righe || [];
      draftRowsRef.current = nextRows;
      originalDraftRef.current = JSON.stringify(nextRows.map(row => [row.codice, row.qta_da_ordinare]));
      setDraftRows(nextRows);
      setSupplierChanged(false);
      try {
        const supplies = await api.get("/auto-order/forniture", { timeout: 20000 });
        setPendingOrders(Array.isArray(supplies.data) ? supplies.data : []);
      } catch {
        setPendingOrders([]);
      }
    } catch (err) {
      const timedOut = err.code === "ECONNABORTED";
      const message = timedOut
        ? "Il calcolo sta richiedendo troppo tempo. Riprova tra qualche secondo."
        : apiErrorMessage(err, "Impossibile calcolare gli ordini. Controlla la connessione e riprova.");
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const conferma = async () => {
    setConfirming(true);
    try {
      const r = await api.post("/auto-order/conferma", {
        snapshot_key: snapshotKey,
        fornitore: supplierName.trim() || null,
        data_consegna_prevista: expectedDelivery || null,
        righe: allRighe.map(row => ({ codice: row.codice, quantita: row.qta_da_ordinare })),
      });
      if (snapshotKey) setConfirmedKeys(keys => new Set(keys).add(confirmationKey));
      setSavedBatchKey(r.data.batch_key || "");
      setSavedConfirmationKey(confirmationKey);
      toast.success(`${r.data.ordinati} righe ordine fornitore ${r.data.duplicate ? "gia presenti" : "create"}`);
      setConfirmOpen(false);
    } catch (err) {
      toast.error(apiErrorMessage(err, "Errore durante la conferma Auto-Order"));
    } finally {
      setConfirming(false);
    }
  };

  const updateSupply = async (batch, state = null) => {
    try {
      const payload = { version: batch.version || 0 };
      if (state) payload.stato = state;
      const delivery = deliveryDrafts[batch.batch_key] ?? batch.data_consegna_prevista;
      if (delivery && !state) payload.data_consegna_prevista = delivery;
      const response = await api.patch(`/auto-order/forniture/${encodeURIComponent(batch.batch_key)}`, payload);
      setPendingOrders(rows => state === "ANNULLATO" ? rows.filter(row => row.batch_key !== batch.batch_key) : rows.map(row => row.batch_key === batch.batch_key ? { ...response.data, righe: row.righe } : row));
      setSupplierChanged(true);
      toast.success("Fornitura aggiornata. Ricalcola Auto-Order prima di confermare.");
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile aggiornare la fornitura"));
    }
  };

  const findExistingReceipts = async batch => {
    try {
      const response = await api.get(`/auto-order/forniture/${encodeURIComponent(batch.batch_key)}/carichi-esistenti`);
      setReceiptCandidates(current => ({ ...current, [batch.batch_key]: response.data.carichi || [] }));
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile verificare i carichi già registrati"));
    }
  };

  const closeWithExistingReceipt = async (batch, receipt) => {
    const missing = Object.entries(receipt.mancanti || {}).map(([code, qty]) => `${code}: ${qty}`).join(", ") || "nessuno";
    const extra = Object.entries(receipt.extra || {}).map(([code, qty]) => `${code}: ${qty}`).join(", ") || "nessuno";
    if (!window.confirm(`Ordine ${batch.batch_key} (${(batch.righe || []).reduce((sum, row) => sum + Number(row.quantita_ordinata ?? row.quantita ?? 0), 0)} pezzi)\nCarico ${receipt.file_sorgente || receipt.ricezione_id} del ${receiptDateLabel(receipt.data)} (${receipt.pezzi_caricati} pezzi)\nMancanti: ${missing}\nExtra: ${extra}\nConfermi che sono lo stesso ordine? Il magazzino non sarà caricato nuovamente.`)) return;
    try {
      await api.post(`/auto-order/forniture/${encodeURIComponent(batch.batch_key)}/chiudi-con-carico/${encodeURIComponent(receipt.ricezione_id)}`, {
        version: batch.version || 0,
        conferma_differenze: true,
      });
      setPendingOrders(rows => rows.filter(row => row.batch_key !== batch.batch_key));
      setSupplierChanged(true);
      toast.success("Carico esistente collegato; ordine chiuso senza nuovo carico merce. Ricalcola Auto-Order.");
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile riconciliare il carico"));
    }
  };

  const downloadOrder = (format) => {
    const label = format === "excel" ? "Excel" : "PDF";
    const fornitore = window.prompt(`Nome fornitore per il file ${label}:`, "Fornitore") || "Fornitore";
    const params = new URLSearchParams({ fornitore });
    if (categoria) params.set("categoria", categoria);
    if (savedBatchKey && savedConfirmationKey === confirmationKey) {
      params.set("batch_key", savedBatchKey);
    } else {
      params.set("snapshot_key", snapshotKey);
      params.set("selezione", JSON.stringify(allRighe.map(row => ({ codice: row.codice, quantita: row.qta_da_ordinare }))));
    }
    const url = `${API}/auto-order/${format}?${params.toString()}`;
    window.open(url, "_blank");
  };

  const changeQuantity = (codice, value) => {
    const quantity = value === "" ? "" : Number(value);
    setDraftRows(rows => {
      const next = rows.map(row => row.codice === codice
        ? { ...row, qta_da_ordinare: quantity, totale: Number.isFinite(quantity) ? Math.round(quantity * (row.prezzo || 0) * 100) / 100 : 0 }
        : row);
      draftRowsRef.current = next;
      return next;
    });
  };

  const removeRow = (codice) => {
    setDraftRows(rows => { const next = rows.filter(row => row.codice !== codice); draftRowsRef.current = next; return next; });
  };

  const addRow = (codice = selectedToAdd) => {
    const row = allEsclusi.find(item => item.codice === codice);
    if (!row || row.anomalia || row.anomalie_riconciliazione?.length || ["CONTROLLO MANUALE", "ORDINE IN ARRIVO"].includes(row.stato) || draftCodes.has(row.codice)) return;
    const quantity = Math.max(1, Number(row.lotto_ordine) || 1);
    setDraftRows(rows => { const next = [...rows, {
      ...row,
      stato: "ORDINA ORA",
      motivo: `AGGIUNTO MANUALMENTE · ${row.motivo}`,
      qta_da_ordinare: quantity,
      totale: Math.round(quantity * (row.prezzo || 0) * 100) / 100,
    }]; draftRowsRef.current = next; return next; });
    setSelectedToAdd("");
    setProductSearch("");
  };

  const addableRows = allEsclusi.filter(row => !row.anomalia && !(row.anomalie_riconciliazione || []).length && !["CONTROLLO MANUALE", "ORDINE IN ARRIVO"].includes(row.stato) && !draftCodes.has(row.codice));
  const productSearchResults = (() => {
    const search = productSearch.trim().toLocaleLowerCase("it");
    if (!search || selectedToAdd) return [];
    return addableRows
      .filter(row => [row.codice, row.descrizione, row.categoria]
        .some(value => String(value || "").toLocaleLowerCase("it").includes(search)))
      .slice(0, 8);
  })();

  const selectProduct = row => {
    setSelectedToAdd(row.codice);
    setProductSearch(`${row.codice} · ${row.descrizione}`);
  };

  const handleProductSearchKeyDown = event => {
    if (event.key === "Escape") {
      setProductSearch("");
      setSelectedToAdd("");
      return;
    }
    if (event.key !== "Enter") return;
    event.preventDefault();
    const code = selectedToAdd || productSearchResults[0]?.codice;
    if (code) addRow(code);
  };

  return (
    <Layout title="Auto-Order" subtitle="fabbisogno reale basato sulle vendite recenti">
      <div className="mb-5 grid grid-cols-1 gap-3 rounded-md border border-slate-200 bg-white p-4 text-sm md:grid-cols-4">
        <div><b>1. Vendite</b><br/><span className="text-slate-500">Registra le vendite in negozio e acquisisci il CSV vending.</span></div>
        <div><b>2. Giacenze</b><br/><span className="text-slate-500">Controlla le scorte operative e le anomalie.</span></div>
        <div><b>3. Ordine</b><br/><span className="text-slate-500">Salva l’ordine fornitore; PDF ed Excel sono facoltativi.</span></div>
        <div><b>4. Carico</b><br/><span className="text-slate-500">Collega la ricezione all’ordine e aggiorna il magazzino.</span></div>
      </div>
      {savedBatchKey && <p className="mb-4 rounded border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">Ordine fornitore confermato. Riferimento per Carico Merce: <code className="break-all select-all">{savedBatchKey}</code></p>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <KpiCard label={categoria ? "Ordini selezionati" : "Ordina ora"} value={formatNum(ordiniCategoria.length)} />
        <KpiCard label="Valore listino" value={formatEur(totaleCategoria)} tone="success" />
        <KpiCard label="Da controllare" value={formatNum((data?.riepilogo_stati?.["CONTROLLO MANUALE"] || 0) + (data?.n_anomalie_stock || 0))} tone="warning" />
        <KpiCard label="Pezzi già ordinati" value={formatNum(pendingPieces)} />
      </div>
      <p className="mb-4 text-sm text-slate-600">Costo acquisto stimato: <b>{missingCosts ? missingCosts === ordiniCategoria.length ? "non disponibile" : `${formatEur(knownCost)} (parziale)` : formatEur(knownCost)}</b>{missingCosts ? ` · ${missingCosts} articoli senza costo unitario` : ""}.</p>

      <div className="flex flex-wrap gap-3 mb-4">
        <button data-testid="ao-refresh" onClick={() => load(true)} disabled={loading} className="border border-slate-300 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors disabled:opacity-40">{loading ? "Calcolo…" : "Ricalcola"}</button>
        <button data-testid="ao-pdf" onClick={() => downloadOrder("pdf")} disabled={!ordiniCategoria.length || invalidRows.length > 0 || supplierChanged} className="border border-slate-900 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors disabled:opacity-40">
          Esporta PDF {categoria ? "selezione" : "fornitore"}
        </button>
        <button data-testid="ao-excel" onClick={() => downloadOrder("excel")} disabled={!ordiniCategoria.length || invalidRows.length > 0 || supplierChanged} className="border border-slate-900 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors disabled:opacity-40">
          Esporta Excel {categoria ? "selezione" : "fornitore"}
        </button>
        <button data-testid="ao-conferma" onClick={() => setConfirmOpen(true)} disabled={!allRighe.length || Boolean(categoria) || confirming || alreadyConfirmed || supplierChanged || invalidRows.length > 0 || data?.data_quality === "DATI_INCOERENTI"} title={categoria ? "Seleziona Tutte le categorie per confermare l'intero ordine" : alreadyConfirmed ? "Proposta gia confermata" : invalidRows.length ? "Correggi le quantità e i lotti" : supplierChanged ? "Ricalcola dopo la modifica delle forniture" : ""} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors disabled:opacity-40">
          {confirming ? "Salvataggio..." : alreadyConfirmed ? "Ordine fornitore salvato" : "Salva ordine fornitore"}
        </button>
        <button data-testid="ao-toggle-excluded" onClick={() => setMostraEsclusi(v => !v)} className="border border-slate-300 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors">
          {mostraEsclusi ? "Nascondi esclusi" : `Mostra esclusi (${esclusiCategoria.length})`}
        </button>
      </div>
      {invalidRows.length > 0 && <p role="alert" className="mb-4 text-sm font-semibold text-red-700">Quantità non valide: {invalidRows.map(row => `${row.codice} (lotto ${row.lotto_ordine || 1})`).join(", ")}. Inserisci multipli interi del lotto.</p>}
      {supplierChanged && <p role="alert" className="mb-4 text-sm font-semibold text-amber-800">Fornitura modificata: ricalcola Auto-Order per aggiornare la proposta.</p>}
      {data?.data_quality && <p className={`mb-4 rounded border p-3 text-sm ${data.data_quality === "DATI_AFFIDABILI" ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-amber-300 bg-amber-50 text-amber-900"}`}>Qualità dati: <b>{data.data_quality.replaceAll("_", " ")}</b>. {data.data_quality === "DATI_INCOERENTI" ? "Correggi le anomalie di forniture prima di confermare." : data.data_quality === "DATI_PARZIALI" ? "Verifica le vendite native e gli articoli segnalati prima di procedere." : "Vendite e giacenze operative disponibili nel database."}</p>}
      {data?.giorni_ritardo_dati > 1 && <p className="mb-4 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">Ultima vendita nativa registrata {data.giorni_ritardo_dati} giorni fa. Verifica che le vendite siano aggiornate prima di confermare.</p>}

      {pendingOrders.length > 0 && <Card className="mb-4 p-4">
        <h2 className="mb-2 text-base font-bold">Forniture pendenti ({pendingOrders.length})</h2>
        <p className="mb-3 text-xs text-slate-600">L’arrivo previsto indica solo una consegna futura. Se la merce è già arrivata, cerca il carico registrato: la sua data effettiva verrà conservata senza aggiornare di nuovo il magazzino.</p>
        <div className="space-y-3">
          {pendingOrders.map(batch => <div key={batch.batch_key} className="grid gap-2 rounded border border-slate-200 p-3 text-sm md:grid-cols-[minmax(0,1fr)_auto_auto_auto] md:items-center">
            <div className="min-w-0"><div className="font-mono text-xs break-all">{batch.batch_key}</div><div className="text-slate-500">{batch.stato} · {(batch.righe || []).reduce((sum, row) => sum + Math.max(0, Number(row.quantita_ordinata ?? row.quantita ?? 0) - Number(row.quantita_ricevuta || 0)), 0)} pezzi residui</div></div>
            <label className="text-xs text-slate-600">Arrivo previsto (futuro) <input type="date" aria-label={`Arrivo previsto ${batch.batch_key}`} value={deliveryDrafts[batch.batch_key] ?? batch.data_consegna_prevista ?? ""} onChange={event => setDeliveryDrafts(current => ({ ...current, [batch.batch_key]: event.target.value }))} className="ml-2 rounded border px-2 py-1 text-sm" /></label>
            <button type="button" onClick={() => updateSupply(batch)} disabled={!deliveryDrafts[batch.batch_key]} className="rounded border border-slate-300 px-3 py-2 disabled:opacity-40">Salva previsione</button>
            {batch.stato === "CONFERMATO" || batch.stato === "CREATO" ? <button type="button" onClick={() => updateSupply(batch, "INVIATO")} className="rounded bg-slate-900 px-3 py-2 text-white">Segna inviato</button> : <span />}
            <div className="flex flex-wrap gap-2 md:col-span-4">
              <button type="button" onClick={() => findExistingReceipts(batch)} className="rounded border border-slate-300 px-3 py-2">Merce già arrivata? Cerca carico</button>
              {["CREATO", "CONFERMATO", "INVIATO"].includes(batch.stato) && <button type="button" onClick={() => window.confirm(`Confermi che la fornitura ${batch.batch_key} non è da considerare pendente? Verifica prima che non sia stata effettivamente consegnata senza collegamento. Resterà nello storico come annullata.`) && updateSupply(batch, "ANNULLATO")} className="rounded border border-red-300 px-3 py-2 text-red-700">Annulla fornitura</button>}
              {(receiptCandidates[batch.batch_key] || []).map(receipt => <div key={receipt.ricezione_id} className="w-full rounded bg-amber-50 p-2 text-xs text-amber-950">
                <div><b>{receipt.file_sorgente || "Carico esistente"}</b> · ricevuto il {receiptDateLabel(receipt.data)} · {receipt.righe_coincidenti}/{receipt.righe_ordine} codici · {receipt.pezzi_caricati}/{receipt.pezzi_ordinati} pezzi</div>
                <div>Mancanti: {Object.entries(receipt.mancanti || {}).map(([code, qty]) => `${code} ${qty}`).join(", ") || "nessuno"} · Extra: {Object.entries(receipt.extra || {}).map(([code, qty]) => `${code} ${qty}`).join(", ") || "nessuno"}</div>
                <div>Verifica il documento fornitore prima di collegare: un carico simile può comparire su più ordini.</div>
                <button type="button" onClick={() => closeWithExistingReceipt(batch, receipt)} className="mt-1 rounded bg-amber-900 px-2 py-1 text-white">Collega e chiudi senza nuovo carico</button>
              </div>)}
              {receiptCandidates[batch.batch_key]?.length === 0 && <span className="text-xs text-slate-500">Nessun carico compatibile trovato.</span>}
            </div>
          </div>)}
        </div>
      </Card>}

      <Card className="mb-4 p-4">
        <div className="flex flex-col gap-2">
          <label htmlFor="ao-add-product" className="text-sm font-semibold text-slate-700">Cerca e aggiungi un prodotto</label>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
            <div className="relative min-w-0 flex-1">
              <input
                id="ao-add-product"
                data-testid="ao-add-product"
                type="search"
                role="combobox"
                aria-autocomplete="list"
                aria-expanded={productSearchResults.length > 0}
                aria-controls="ao-product-results"
                autoComplete="off"
                value={productSearch}
                onChange={event => {
                  setProductSearch(event.target.value);
                  setSelectedToAdd("");
                }}
                onKeyDown={handleProductSearchKeyDown}
                placeholder="Scrivi codice, descrizione o categoria…"
                className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200"
              />
              {productSearchResults.length > 0 ? (
                <div id="ao-product-results" role="listbox" data-testid="ao-product-results" className="absolute z-30 mt-1 max-h-80 w-full overflow-y-auto rounded-md border border-slate-200 bg-white p-1 shadow-lg">
                  {productSearchResults.map(row => (
                    <button
                      key={row.codice}
                      type="button"
                      role="option"
                      aria-selected="false"
                      data-testid={`ao-product-result-${row.codice}`}
                      onClick={() => selectProduct(row)}
                      className="flex w-full items-center justify-between gap-3 rounded px-3 py-2 text-left text-sm hover:bg-slate-100 focus:bg-slate-100 focus:outline-none"
                    >
                      <span className="min-w-0">
                        <span className="block font-mono font-bold text-slate-900">{row.codice}</span>
                        <span className="block truncate text-slate-600">{row.descrizione}</span>
                      </span>
                      <span className="shrink-0 text-xs text-slate-500">{row.categoria} · lotto {formatNum(row.lotto_ordine || 1)}</span>
                    </button>
                  ))}
                </div>
              ) : null}
              {productSearch.trim() && !selectedToAdd && productSearchResults.length === 0 ? (
                <p data-testid="ao-product-no-results" className="mt-2 text-xs text-slate-500">Nessun prodotto ordinabile trovato. Potrebbe essere già presente nell’ordine o avere uno stock anomalo.</p>
              ) : null}
            </div>
            <button type="button" data-testid="ao-add-product-btn" onClick={() => addRow()} disabled={!selectedToAdd} className="inline-flex items-center justify-center gap-2 rounded-md bg-slate-900 px-4 py-2 text-sm text-white hover:bg-slate-800 disabled:opacity-40">
              <Plus size={16} /> Aggiungi all’ordine
            </button>
          </div>
          <p className="text-xs text-slate-500">Puoi selezionare un risultato oppure premere Invio per aggiungere il primo prodotto trovato.</p>
        </div>
      </Card>

      {error && (
        <div data-testid="ao-error" role="alert" className="mb-4 flex items-center justify-between gap-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <span>{error}</span>
          <button onClick={load} disabled={loading} className="font-semibold underline disabled:opacity-40">Riprova</button>
        </div>
      )}

      <div data-testid="ao-calculation-note" className="mb-4 rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
        Domanda giornaliera: <b>{Math.round((data?.peso_breve ?? 0.7) * 100)}% ultimi {breveGg}gg + {Math.round((data?.peso_lungo ?? 0.3) * 100)}% ultimi {lungoGg}gg</b>{dataRiferimento ? <> fino al <b>{dataRiferimento}</b></> : null}.
        Il magazzino reale è la <b>giacenza negozio fisica</b> registrata nella WebApp. Il fabbisogno complessivo considera separatamente vending e forniture con data entro l'orizzonte. Ordina sotto {coperturaMin}gg verso il target di {data?.parametri?.GIORNI_COPERTURA_TARGET || 14}gg, con sicurezza ×{data?.fattore_sicurezza || 1.15}.
        Le forniture senza data futura affidabile richiedono verifica manuale. Il valore listino non rappresenta il costo di acquisto.
        Ogni articolo venduto negli ultimi {lungoGg}gg viene analizzato; senza vendite e con scorta sotto il lotto viene segnalato per controllo manuale.
      </div>

      <div className="flex flex-col sm:flex-row sm:items-end gap-3 mb-4">
        <SearchBar data-testid="ao-search" value={query} onChange={setQuery} placeholder="Cerca codice, articolo, tipo o motivo…" className="flex-1 max-w-md" />
        <label className="flex flex-col gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Articoli da visualizzare e stampare
          <select data-testid="ao-filter-cat" value={categoria} onChange={e => setCategoria(e.target.value)} className="min-w-56 border border-slate-300 bg-white text-slate-900 rounded-md px-3 py-2 text-sm font-normal normal-case tracking-normal">
            <option value="">Tutte le categorie</option>
            {categorieDisponibili.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <span className="text-sm text-slate-500 self-center sm:pb-2">{righe.length} risultati</span>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <Th sortKey="codice" currentKey={sortKey} dir={sortDir} onClick={toggle}>Codice</Th>
                <Th sortKey="descrizione" currentKey={sortKey} dir={sortDir} onClick={toggle}>Articolo</Th>
                <Th sortKey="categoria" currentKey={sortKey} dir={sortDir} onClick={toggle}>Tipo</Th>
                <Th sortKey="giacenza_negozio" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Riman. neg.</Th>
                <Th sortKey="venduti_vending" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Vend. vending</Th>
                <Th sortKey="giacenza_vending" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Giac. vend.</Th>
                <Th sortKey="magazzino_reale_lordo" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Mag. reale</Th>
                <Th sortKey="venduto_breve" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Vend. {breveGg}gg</Th>
                <Th sortKey="venduto_lungo" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Vend. {lungoGg}gg</Th>
                <Th sortKey="domanda_gg" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Dom./gg</Th>
                <Th sortKey="copertura_gg" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Cop. min. (gg)</Th>
                <Th sortKey="quantita_in_arrivo" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">In arrivo</Th>
                <Th sortKey="target_scorta" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Target</Th>
                <Th sortKey="lotto_ordine" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Lotto</Th>
                <Th sortKey="qta_da_ordinare" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Qta da ordinare</Th>
                <Th sortKey="totale" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Val. listino</Th>
                <Th sortKey="stato" currentKey={sortKey} dir={sortDir} onClick={toggle}>Stato</Th>
                <Th sortKey="motivo" currentKey={sortKey} dir={sortDir} onClick={toggle}>Calcolo</Th>
                <th className="w-20">Azioni</th>
              </tr>
            </thead>
            <tbody>
              {loading && !data && <tr><td colSpan={19} className="text-center py-8 text-slate-400">Elaborazione…</td></tr>}
              {righe.map(r => (
                <tr key={r.codice} data-testid={`ao-row-${r.codice}`}>
                  <td className="font-mono">{r.codice}</td>
                  <td className="max-w-xs truncate">{r.descrizione}</td>
                  <td><Badge tone="info">{r.categoria}</Badge></td>
                  <td className={`font-mono text-right ${(r.giacenza_negozio||0) <= 0 ? 'text-red-600 font-bold' : ''}`}>{formatNum(r.giacenza_negozio ?? 0)}</td>
                  <td className="font-mono text-right">{formatNum(r.venduti_vending ?? 0)}</td>
                  <td className="font-mono text-right">{formatNum(r.giacenza_vending ?? 0)}</td>
                  <td className={`font-mono text-right ${(r.magazzino_reale_lordo||0) < 0 ? 'text-red-600 font-bold' : (r.magazzino_reale_lordo||0) === 0 ? 'text-amber-700 font-bold' : ''}`}>{formatNum(r.magazzino_reale_lordo ?? 0)}</td>
                  <td className="font-mono text-right">{formatNum(r.venduto_breve ?? r.venduto_10gg)}</td>
                  <td className="font-mono text-right">{formatNum(r.venduto_lungo ?? r.venduto_30gg)}</td>
                  <td className="font-mono text-right">{formatNum(r.domanda_gg, 2)}</td>
                  <td className={`font-mono text-right ${r.copertura_gg !== null && r.copertura_gg < coperturaMin ? 'text-red-600 font-bold' : ''}`} title={`Negozio: ${r.copertura_negozio_gg ?? '∞'} gg · Vending: ${r.copertura_vending_gg ?? '∞'} gg`}>{r.copertura_gg === null ? '∞' : r.copertura_gg}</td>
                  <td className="font-mono text-right" title={r.quantita_senza_data ? `${r.quantita_senza_data} pezzi senza data futura affidabile` : ""}>{formatNum(r.quantita_in_arrivo || 0)}{r.quantita_senza_data ? " ⚠" : ""}</td>
                  <td className="font-mono text-right">{formatNum(r.target_scorta)}</td>
                  <td className="font-mono text-right">{r.lotto_ordine}</td>
                  <td className="text-right">
                    {draftCodes.has(r.codice) ? <input data-testid={`ao-quantity-${r.codice}`} type="number" min="1" step="1" value={r.qta_da_ordinare} onChange={e => changeQuantity(r.codice, e.target.value)} className="w-20 rounded border border-slate-300 px-2 py-1 text-right font-mono font-bold" aria-label={`Quantità ${r.descrizione}`} /> : "—"}
                  </td>
                  <td className="font-mono text-right font-semibold">{r.totale ? formatEur(r.totale) : "—"}</td>
                  <td className="space-x-1"><Badge tone={r.stato === "ORDINA ORA" ? "error" : r.stato === "MONITORA" || r.stato === "CONTROLLO MANUALE" ? "warning" : r.stato === "ANOMALIA" ? "error" : "info"}>{r.stato}</Badge>{r.anomalia && r.stato !== "ANOMALIA" ? <Badge tone="error">ANOMALIA STOCK</Badge> : null}</td>
                  <td className="min-w-64 text-xs">{r.motivo}</td>
                  <td className="text-right">
                    {draftCodes.has(r.codice) ? (
                      <button data-testid={`ao-remove-${r.codice}`} onClick={() => removeRow(r.codice)} className="rounded p-2 text-red-600 hover:bg-red-50" aria-label={`Rimuovi ${r.descrizione}`} title="Rimuovi dall'ordine"><Trash2 size={16} /></button>
                    ) : (
                      <button data-testid={`ao-add-${r.codice}`} onClick={() => addRow(r.codice)} disabled={r.anomalia || Boolean(r.anomalie_riconciliazione?.length)} className="rounded p-2 text-emerald-700 hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-30" aria-label={`Aggiungi ${r.descrizione}`} title={r.anomalia ? "Stock anomalo: articolo non ordinabile" : r.anomalie_riconciliazione?.length ? "Vendite da riconciliare" : "Aggiungi all'ordine"}><Plus size={16} /></button>
                    )}
                  </td>
                </tr>
              ))}
              {!loading && !error && righe.length === 0 && <tr><td colSpan={19} className="text-center py-8 text-slate-400">{categoria ? "Nessun articolo nella categoria selezionata." : "Nessun articolo nella bozza d'ordine."}</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
      <ConfirmDialog
        open={confirmOpen}
        title="Confermare Auto-Order?"
        confirmLabel="Crea ordine fornitore"
        loading={confirming}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={conferma}
      >
        <p>Verranno create <b>{formatNum(allRighe.length)}</b> righe ordine fornitore per un totale di <b>{formatEur(allRighe.reduce((sum, row) => sum + (row.totale || 0), 0))}</b>.</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-sm">Fornitore <input value={supplierName} onChange={event => setSupplierName(event.target.value)} maxLength={120} placeholder="Nome fornitore (facoltativo)" className="mt-1 w-full rounded border px-2 py-1" /></label>
          <label className="text-sm">Consegna prevista <input type="date" value={expectedDelivery} onChange={event => setExpectedDelivery(event.target.value)} className="mt-1 w-full rounded border px-2 py-1" /></label>
        </div>
        <p className="mt-2 text-slate-600">Questa azione non carica il magazzino: le giacenze aumentano solo dalla pagina <b>Carico Merce</b> quando la merce arriva fisicamente.</p>
      </ConfirmDialog>
    </Layout>
  );
}
