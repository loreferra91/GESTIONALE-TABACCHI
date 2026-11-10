import { useCallback, useEffect, useRef, useState } from "react";
import Layout from "../components/Layout";
import { api, API, apiErrorMessage } from "../lib/api";
import { Card, KpiCard, Badge, formatEur, formatNum } from "../components/UI";
import { useSortSearch, Th, SearchBar } from "../lib/tableHooks";
import { toast } from "sonner";
import ConfirmDialog from "../components/ConfirmDialog";
import { Plus, Trash2 } from "lucide-react";
import { shortCategory } from "../lib/categories";

const receiptDateLabel = value => {
  const day = String(value || "").slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? day.split("-").reverse().join("/") : "data non disponibile";
};

export default function AutoOrder() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [comparison, setComparison] = useState(null);
  const [comparisonError, setComparisonError] = useState("");
  const [orderPolicy, setOrderPolicy] = useState("AUTO_ORDER_2");
  const orderPolicyRef = useRef(null);
  const [excludedCategories, setExcludedCategories] = useState([]);
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
  const [manualQuantity, setManualQuantity] = useState("");
  const [acknowledgePartial, setAcknowledgePartial] = useState(false);
  const [partialReason, setPartialReason] = useState("");
  const draftRowsRef = useRef([]);
  const originalDraftRef = useRef("[]");
  const allRighe = draftRows.filter(row => !excludedCategories.includes(row.categoria));
  const allEsclusi = data?.esclusi || [];
  const sourceRows = [...(data?.righe || []), ...allEsclusi];
  const categorieDisponibili = Array.from(new Set([...draftRows, ...allEsclusi].map(r => r.categoria).filter(Boolean))).sort();
  const ordiniCategoria = allRighe;
  const draftCodes = new Set(draftRows.map(r => r.codice));
  const esclusiDisponibili = allEsclusi.filter(r => !draftCodes.has(r.codice));
  const esclusiCategoria = esclusiDisponibili.filter(row => !excludedCategories.includes(row.categoria));
  const righeCategoria = mostraEsclusi ? [...ordiniCategoria, ...esclusiCategoria] : ordiniCategoria;
  const totaleCategoria = ordiniCategoria.reduce((totale, r) => totale + (r.totale || 0), 0);
  const missingCosts = ordiniCategoria.filter(row => row.costo_unitario_acquisto == null).length;
  const knownCost = ordiniCategoria.reduce((sum, row) => sum + (row.costo_unitario_acquisto == null ? 0 : Number(row.costo_unitario_acquisto) * Number(row.qta_da_ordinare || 0)), 0);
  const coperturaMin = data?.parametri?.GIORNI_COPERTURA_MIN || 7;
  const breveGg = data?.finestra_breve_gg || 10;
  const lungoGg = data?.finestra_lunga_gg || 30;
  const snapshotKey = data?.snapshot_key || "";
  const draftSignature = JSON.stringify(allRighe.map(r => [r.codice, r.qta_da_ordinare]).sort((a, b) => a[0].localeCompare(b[0])));
  const confirmationKey = `${orderPolicy}:${snapshotKey}:${orderPolicy === "AUTO_ORDER_3_1" ? comparison?.policy_snapshot_key || "" : ""}:${draftSignature}`;
  const alreadyConfirmed = Boolean(snapshotKey && confirmedKeys.has(confirmationKey));
  const invalidRows = allRighe.filter(row => !Number.isInteger(Number(row.qta_da_ordinare)) || Number(row.qta_da_ordinare) <= 0 || Number(row.qta_da_ordinare) % Math.max(1, Number(row.lotto_ordine) || 1) !== 0 || ((orderPolicy === "AUTO_ORDER_3_1" || Number(row.quantita_in_arrivo || 0) > 0) && Number(row.qta_da_ordinare) > Number(row.quantita_integrativa_massima || 0)));
  const pendingPieces = [...(data?.righe || []), ...(data?.esclusi || [])].reduce((sum, row) => sum + (Number(row.quantita_in_arrivo) || 0) + (Number(row.quantita_senza_data) || 0), 0);
  const dataRiferimento = data?.data_riferimento_domanda
    ? new Date(`${data.data_riferimento_domanda}T00:00:00`).toLocaleDateString("it-IT")
    : null;
  const { rows: righe, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(righeCategoria, {
    initial: "totale", dir: "desc",
    searchFields: ["codice", "descrizione", "categoria", "stato", "motivo"],
  });

  const make31Draft = (nativeData, forecast) => {
    const original = new Map([...(nativeData.righe || []), ...(nativeData.esclusi || [])].map(row => [row.codice, row]));
    return (forecast?.rows || [])
      .filter(row => Number(row.quantita_suggerita) > 0 && row.qualita_previsione === "OSSERVABILE" && !(row.anomalie || []).length)
      .map(row => {
        const source = original.get(row.codice);
        if (!source || source.anomalia || (source.anomalie_riconciliazione || []).length || (source.anomalie_forniture || []).length || Number(source.quantita_senza_data || 0) > 0) return null;
        const quantity = Number(row.quantita_suggerita);
        return {
          ...source, stato: "ORDINA ORA", qta_da_ordinare: quantity,
          totale: Math.round(Number(source.prezzo || 0) * quantity * 100) / 100,
          quantita_integrativa_massima: quantity, motivo: row.motivazione,
        };
      }).filter(Boolean);
  };

  const applyPolicy = (policy, forecast = comparison) => {
    if (!data) return;
    if (policy === "AUTO_ORDER_3_1" && !forecast?.operational_ready) {
      toast.error("3.1 non attivabile: controlla parametri, storico e qualità dei dati");
      return;
    }
    if (JSON.stringify(draftRows.map(row => [row.codice, row.qta_da_ordinare])) !== originalDraftRef.current &&
        !window.confirm("La bozza corrente ha modifiche non salvate. Sostituirla con le proposte della politica selezionata?")) return;
    const nextRows = policy === "AUTO_ORDER_3_1" ? make31Draft(data, forecast) : (data.righe || []);
    orderPolicyRef.current = policy;
    setOrderPolicy(policy);
    draftRowsRef.current = nextRows;
    originalDraftRef.current = JSON.stringify(nextRows.map(row => [row.codice, row.qta_da_ordinare]));
    setDraftRows(nextRows);
    setSavedBatchKey("");
    setSavedConfirmationKey("");
  };

  const load = useCallback(async (refresh = false) => {
    if (refresh && JSON.stringify(draftRowsRef.current.map(row => [row.codice, row.qta_da_ordinare])) !== originalDraftRef.current) {
      if (!window.confirm("La bozza contiene modifiche non salvate. Ricalcolare e sostituire la bozza?")) return;
    }
    setLoading(true);
    setError("");
    setComparisonError("");
    try {
      const r = await api.get("/auto-order", { timeout: 20000 });
      setData(r.data);
      const comparisonRequest = api.get("/auto-order/confronto", { timeout: 30000 })
        .then(response => response.data?.mode === "CONFRONTO_SENZA_CONFERMA" ? response.data : null)
        .catch(() => { setComparisonError("Previsione 3.1 non disponibile: continua a usare 2.0."); return null; });
      try {
        const supplies = await api.get("/auto-order/forniture", { timeout: 20000 });
        setPendingOrders(Array.isArray(supplies.data) ? supplies.data : []);
      } catch {
        setPendingOrders([]);
      }
      const forecast = await comparisonRequest;
      setComparison(forecast);
      const preferred = orderPolicyRef.current || (forecast?.operational_ready ? "AUTO_ORDER_3_1" : "AUTO_ORDER_2");
      const selected = preferred === "AUTO_ORDER_3_1" && forecast?.operational_ready
        ? "AUTO_ORDER_3_1" : "AUTO_ORDER_2";
      orderPolicyRef.current = selected;
      setOrderPolicy(selected);
      const nextRows = selected === "AUTO_ORDER_3_1" ? make31Draft(r.data, forecast) : (r.data.righe || []);
      draftRowsRef.current = nextRows;
      originalDraftRef.current = JSON.stringify(nextRows.map(row => [row.codice, row.qta_da_ordinare]));
      setDraftRows(nextRows);
      setSavedBatchKey("");
      setSavedConfirmationKey("");
      setSupplierChanged(false);
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
        policy_version: orderPolicy,
        policy_snapshot_key: orderPolicy === "AUTO_ORDER_3_1" ? comparison?.policy_snapshot_key : null,
        fornitore: supplierName.trim() || null,
        data_consegna_prevista: expectedDelivery || null,
        righe: allRighe.map(row => ({ codice: row.codice, quantita: row.qta_da_ordinare })),
        conferma_dati_parziali: acknowledgePartial,
        motivo_deroga: acknowledgePartial ? partialReason.trim() : null,
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
    if (savedBatchKey && savedConfirmationKey === confirmationKey) {
      params.set("batch_key", savedBatchKey);
    } else {
      if (orderPolicy === "AUTO_ORDER_3_1") {
        toast.error("Conferma prima l'ordine 3.1 per esportare un documento valido.");
        return;
      }
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

  const canManuallyAdd = row => orderPolicy === "AUTO_ORDER_3_1"
    ? Boolean(row && comparison?.operational_ready && !draftCodes.has(row.codice) &&
        comparison.rows.some(candidate => candidate.codice === row.codice &&
          candidate.quantita_suggerita > 0 && candidate.qualita_previsione === "OSSERVABILE" &&
          !(candidate.anomalie || []).length))
    : Boolean(row && !row.anomalia && !(row.anomalie_riconciliazione || []).length
    && (!Number(row.quantita_in_arrivo || 0) || (row.stato === "ORDINA ORA" && Number(row.quantita_integrativa_massima || 0) > 0))
    && !Number(row.quantita_senza_data || 0)
    && row.stato !== "ORDINE IN ARRIVO" && !excludedCategories.includes(row.categoria)
    && !draftCodes.has(row.codice));

  const addRow = (codice = selectedToAdd, requestedQuantity = manualQuantity) => {
    const row = sourceRows.find(item => item.codice === codice);
    if (!canManuallyAdd(row)) return;
    const quantity = Number(requestedQuantity);
    const lotto = Math.max(1, Number(row.lotto_ordine) || 1);
    if (!Number.isInteger(quantity) || quantity <= 0 || quantity % lotto !== 0) {
      toast.error(`Per ${row.descrizione} inserisci una quantità positiva multipla del lotto ${lotto}`);
      return;
    }
    const forecastMax = comparison?.rows?.find(item => item.codice === codice)?.quantita_suggerita;
    if (orderPolicy === "AUTO_ORDER_3_1" && (forecastMax == null || quantity > Number(forecastMax))) {
      toast.error(`La quantità massima 3.1 per ${row.descrizione} è ${forecastMax ?? 0}`);
      return;
    }
    if (orderPolicy !== "AUTO_ORDER_3_1" && Number(row.quantita_in_arrivo || 0) > 0 && quantity > Number(row.quantita_integrativa_massima || 0)) {
      toast.error(`La quantità integrativa massima per ${row.descrizione} è ${row.quantita_integrativa_massima}`);
      return;
    }
    setDraftRows(rows => { const next = [...rows, {
      ...row,
      stato: "ORDINA ORA",
      motivo: `AGGIUNTO MANUALMENTE · ${row.motivo}`,
      qta_da_ordinare: quantity,
      quantita_integrativa_massima: orderPolicy === "AUTO_ORDER_3_1" ? Number(forecastMax) : row.quantita_integrativa_massima,
      totale: Math.round(quantity * (row.prezzo || 0) * 100) / 100,
    }]; draftRowsRef.current = next; return next; });
    setSelectedToAdd("");
    setProductSearch("");
    setManualQuantity("");
  };

  const productSearchResults = (() => {
    const search = productSearch.trim().toLocaleLowerCase("it");
    if (!search || selectedToAdd) return [];
    return [...new Map([...sourceRows, ...draftRows].map(row => [row.codice, row])).values()]
      .filter(row => [row.codice, row.descrizione, row.categoria]
        .some(value => String(value || "").toLocaleLowerCase("it").includes(search)))
      .sort((a, b) => {
        const aCode = String(a.codice || "").toLocaleLowerCase("it");
        const bCode = String(b.codice || "").toLocaleLowerCase("it");
        return Number(bCode === search) - Number(aCode === search)
          || Number(bCode.startsWith(search)) - Number(aCode.startsWith(search))
          || String(a.descrizione || "").localeCompare(String(b.descrizione || ""), "it");
      })
      .slice(0, 12);
  })();

  const selectProduct = row => {
    if (!canManuallyAdd(row)) return;
    setSelectedToAdd(row.codice);
    setProductSearch(`${row.codice} · ${row.descrizione}`);
    setManualQuantity(String(Math.max(1, Number(row.lotto_ordine) || 1)));
  };

  const handleProductSearchKeyDown = event => {
    if (event.key === "Escape") {
      setProductSearch("");
      setSelectedToAdd("");
      setManualQuantity("");
      return;
    }
    if (event.key !== "Enter") return;
    event.preventDefault();
    if (selectedToAdd) addRow(selectedToAdd, manualQuantity);
    else if (productSearchResults[0]) selectProduct(productSearchResults[0]);
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
        <KpiCard label="Articoli nell’ordine" value={formatNum(ordiniCategoria.length)} />
        <KpiCard label="Valore listino" value={formatEur(totaleCategoria)} tone="success" />
        <KpiCard label="Da controllare" value={formatNum((data?.riepilogo_stati?.["CONTROLLO MANUALE"] || 0) + (data?.n_anomalie_stock || 0))} tone="warning" />
        <KpiCard label="Pezzi già ordinati" value={formatNum(pendingPieces)} />
      </div>
      <p className="mb-4 text-sm text-slate-600">Costo acquisto stimato: <b>{missingCosts ? missingCosts === ordiniCategoria.length ? "non disponibile" : `${formatEur(knownCost)} (parziale)` : formatEur(knownCost)}</b>{missingCosts ? ` · ${missingCosts} articoli senza costo unitario` : ""}.</p>

      <div className="flex flex-wrap gap-3 mb-4">
        <button data-testid="ao-refresh" onClick={() => load(true)} disabled={loading} className="border border-slate-300 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors disabled:opacity-40">{loading ? "Calcolo…" : "Ricalcola"}</button>
        <button data-testid="ao-pdf" onClick={() => downloadOrder("pdf")} disabled={!ordiniCategoria.length || invalidRows.length > 0 || supplierChanged || (orderPolicy === "AUTO_ORDER_3_1" && (!savedBatchKey || savedConfirmationKey !== confirmationKey))} className="border border-slate-900 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors disabled:opacity-40">
          Esporta PDF fornitore
        </button>
        <button data-testid="ao-excel" onClick={() => downloadOrder("excel")} disabled={!ordiniCategoria.length || invalidRows.length > 0 || supplierChanged || (orderPolicy === "AUTO_ORDER_3_1" && (!savedBatchKey || savedConfirmationKey !== confirmationKey))} className="border border-slate-900 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors disabled:opacity-40">
          Esporta Excel fornitore
        </button>
        <button data-testid="ao-conferma" onClick={() => setConfirmOpen(true)} disabled={!allRighe.length || confirming || alreadyConfirmed || supplierChanged || invalidRows.length > 0 || data?.data_quality === "DATI_INCOERENTI" || (orderPolicy === "AUTO_ORDER_3_1" && (!comparison?.operational_ready || !comparison?.policy_snapshot_key))} title={alreadyConfirmed ? "Proposta gia confermata" : invalidRows.length ? "Correggi le quantità e i lotti" : supplierChanged ? "Ricalcola dopo la modifica dei dati" : ""} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors disabled:opacity-40">
          {confirming ? "Salvataggio..." : alreadyConfirmed ? "Ordine fornitore salvato" : "Salva ordine fornitore"}
        </button>
        <button data-testid="ao-toggle-excluded" onClick={() => setMostraEsclusi(v => !v)} className="border border-slate-300 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors">
          {mostraEsclusi ? "Nascondi esclusi" : `Mostra esclusi (${esclusiCategoria.length})`}
        </button>
      </div>
      <Card className="mb-4 p-4">
        <div className="mb-2 text-sm font-semibold text-slate-700">Categorie da includere nell’ordine</div>
        <div className="flex flex-wrap gap-2" data-testid="ao-category-bar">
          {categorieDisponibili.map(category => {
            const included = !excludedCategories.includes(category);
            const count = draftRows.filter(row => row.categoria === category).length;
            return <button key={category} type="button" data-testid={`ao-category-${category}`} aria-pressed={included}
              onClick={() => setExcludedCategories(current => included ? [...current, category] : current.filter(item => item !== category))}
              className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${included ? "border-emerald-500 bg-emerald-50 text-emerald-900" : "border-slate-300 bg-white text-slate-500"}`}>
              {included ? "✓ " : "+ "}{shortCategory(category)} ({count})
            </button>;
          })}
        </div>
        <p className="mt-2 text-xs text-slate-500">Le categorie spente sono escluse dalla bozza, dal PDF e dall’Excel. Riattivale per ripristinare gli articoli proposti.</p>
      </Card>
      {invalidRows.length > 0 && <p role="alert" className="mb-4 text-sm font-semibold text-red-700">Quantità non valide: {invalidRows.map(row => `${row.codice} (lotto ${row.lotto_ordine || 1})`).join(", ")}. Inserisci multipli interi del lotto.</p>}
      {supplierChanged && <p role="alert" className="mb-4 text-sm font-semibold text-amber-800">Dati di pianificazione modificati: ricalcola Auto-Order per aggiornare la proposta.</p>}
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
                  setManualQuantity("");
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
                      disabled={!canManuallyAdd(row)}
                      className="flex w-full items-center justify-between gap-3 rounded px-3 py-2 text-left text-sm hover:bg-slate-100 focus:bg-slate-100 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      <span className="min-w-0">
                        <span className="block font-mono font-bold text-slate-900">{row.codice}</span>
                        <span className="block truncate text-slate-600">{row.descrizione}</span>
                      </span>
                      <span className="shrink-0 text-xs text-slate-500">{shortCategory(row.categoria)} · negozio {formatNum(row.giacenza_negozio || 0)} · {draftCodes.has(row.codice) ? "già nella bozza" : excludedCategories.includes(row.categoria) ? "categoria esclusa" : canManuallyAdd(row) ? `lotto ${formatNum(row.lotto_ordine || 1)}` : row.motivo || row.stato}</span>
                    </button>
                  ))}
                </div>
              ) : null}
              {productSearch.trim() && !selectedToAdd && productSearchResults.length === 0 ? (
                <p data-testid="ao-product-no-results" className="mt-2 text-xs text-slate-500">Nessun prodotto trovato nel catalogo ordinabile. Cerca il codice esatto o verifica il prodotto in Magazzino.</p>
              ) : null}
            </div>
            <label className="flex flex-col gap-1 text-xs font-semibold text-slate-600">Quantità
              <input data-testid="ao-manual-quantity" type="number" min="1" step="1" value={manualQuantity} onChange={event => setManualQuantity(event.target.value)} disabled={!selectedToAdd} className="w-28 rounded border border-slate-300 px-2 py-2 text-right font-mono text-sm disabled:bg-slate-50" />
            </label>
            <button type="button" data-testid="ao-add-product-btn" onClick={() => addRow()} disabled={!selectedToAdd} className="inline-flex items-center justify-center gap-2 rounded-md bg-slate-900 px-4 py-2 text-sm text-white hover:bg-slate-800 disabled:opacity-40">
              <Plus size={16} /> Aggiungi all’ordine
            </button>
          </div>
          <p className="text-xs text-slate-500">Seleziona il prodotto, indica una quantità multipla del lotto e aggiungilo. Una fornitura datata consente un ordine integrativo soltanto per il fabbisogno netto residuo; le forniture senza data affidabile richiedono verifica.</p>
        </div>
      </Card>

      {error && (
        <div data-testid="ao-error" role="alert" className="mb-4 flex items-center justify-between gap-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <span>{error}</span>
          <button onClick={load} disabled={loading} className="font-semibold underline disabled:opacity-40">Riprova</button>
        </div>
      )}

      <Card className="mb-4 p-4" data-testid="ao-planning-comparison">
        <h2 className="text-lg font-bold text-slate-900">Previsione e pianificazione</h2>
        <p className="mt-1 text-sm text-slate-600">L'algoritmo 3.1 usa lo storico nativo, le scorte reali, le consegne e le ricariche vending. Le conferme vengono ricalcolate dal backend. Nessun acquisto è automatico.</p>
        <div className="mt-3 flex flex-wrap items-center gap-2" data-testid="ao-policy-selector">
          <button type="button" data-testid="ao-use-3-1" aria-pressed={orderPolicy === "AUTO_ORDER_3_1"} disabled={!comparison?.operational_ready || loading} onClick={() => applyPolicy("AUTO_ORDER_3_1")} className={`rounded border px-3 py-2 text-sm font-semibold disabled:opacity-40 ${orderPolicy === "AUTO_ORDER_3_1" ? "border-emerald-700 bg-emerald-50 text-emerald-900" : "border-slate-300"}`}>Usa Auto-Order 3.1</button>
          <button type="button" data-testid="ao-use-2" aria-pressed={orderPolicy === "AUTO_ORDER_2"} disabled={loading} onClick={() => applyPolicy("AUTO_ORDER_2")} className={`rounded border px-3 py-2 text-sm font-semibold ${orderPolicy === "AUTO_ORDER_2" ? "border-slate-900 bg-slate-100" : "border-slate-300"}`}>Fallback 2.0</button>
          <span className="text-xs font-semibold text-slate-600">Bozza attiva: {orderPolicy === "AUTO_ORDER_3_1" ? "3.1 — conferma validata" : "2.0"}</span>
        </div>
        {comparison && !comparison.operational_ready && <div className="mt-2 rounded border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900" data-testid="ao-policy-blockers">
          <b>3.1 non ancora attivabile per gli ordini reali.</b>
          <ul className="mt-1 list-disc pl-4">{(comparison.operational_blockers || []).map((reason, i) => <li key={i}>{reason}</li>)}</ul>
          <p className="mt-1">Configura i parametri reali in Parametri; nel frattempo resta disponibile 2.0. Il passaggio a 3.1 non avviene mai silenziosamente durante una bozza già modificata.</p>
        </div>}
        {comparisonError && <p className="mt-2 text-sm text-amber-700">{comparisonError}</p>}
        {comparison && <>
          <div className="mt-3 grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
            <div>Rischio esaurimento <b>{comparison.summary.stockout_risk}</b></div>
            <div>Ordini integrativi <b>{comparison.summary.supplementary_orders}</b></div>
            <div>Già in arrivo <b>{comparison.summary.already_on_order}</b></div>
            <div>Da ricaricare vending <b>{comparison.summary.vending_refills}</b></div>
            <div>Quantità 2.0 <b>{formatNum(comparison.summary.pieces_2)}</b></div>
            <div>Quantità 3.1 <b>{formatNum(comparison.summary.pieces_3)}</b></div>
            {comparison.summary.simulated_stockouts_2 != null && <div>Rotture simulate 2.0 / 3.0 <b>{comparison.summary.simulated_stockouts_2} / {comparison.summary.simulated_stockouts_3}</b></div>}
            {comparison.summary.mean_positive_ending_stock_2 != null && <div>Giacenza finale media 2.0 / 3.0 <b>{formatNum(comparison.summary.mean_positive_ending_stock_2, 1)} / {formatNum(comparison.summary.mean_positive_ending_stock_3, 1)}</b></div>}
            <div>Dati da verificare <b>{comparison.summary.insufficient_data}</b></div>
            <div>Costo noto 3.1 <b>{formatEur(comparison.summary.known_purchase_cost)}</b>{comparison.summary.missing_purchase_costs ? ` · ${comparison.summary.missing_purchase_costs} senza costo` : ""}</div>
          </div>
          <p className="mt-2 text-xs text-slate-500">{comparison.native_history_days} giorni tra la prima e l’ultima evidenza nativa nella finestra di 90 giorni. Senza lead time configurato, la data di arrivo del nuovo ordine resta ignota.</p>
          <div className="mt-3 max-h-80 overflow-y-auto rounded border">
            {comparison.rows.map(row => <details key={row.codice} className="border-b px-3 py-2 text-sm last:border-b-0" data-testid={`ao-comparison-${row.codice}`}>
              <summary className="cursor-pointer"><span className="font-mono">{row.codice}</span> · {row.descrizione} · <b>2.0 {row.quantita_2}</b> / <b>3.0 {row.quantita_suggerita}</b> · {row.metodo_previsione}{row.data_rischio_esaurimento ? ` · rischio ${row.data_rischio_esaurimento}` : ""}</summary>
              <div className="mt-2 space-y-1 text-slate-600">
                <p>{row.motivazione}</p>
                <p>Negozio {formatNum(row.domanda_negozio_gg, 2)}/gg · Vending {formatNum(row.domanda_vending_gg, 2)}/gg · Sicurezza {row.scorta_sicurezza} · Posizione {row.inventory_position} · Trasferimento vending {row.planned_vending_transfer}</p>
                <p>Qualità: {row.qualita_previsione} · Classe: {row.classe_rotazione} · Modello negozio: {row.metodo_negozio} · Modello vending: {row.metodo_vending}</p>
                {row.simulazione_2 && <p>Fine orizzonte 2.0: {formatNum(row.simulazione_2.ending_stock, 1)} pezzi; 3.0: {formatNum(row.simulazione_3.ending_stock, 1)} pezzi. Rischio 2.0: {row.simulazione_2.stockout_date || "nessuno"}; 3.0: {row.simulazione_3.stockout_date || "nessuno"}.</p>}
                {Object.keys(row.backtest_negozio || {}).length > 0 && <p>Backtest negozio MAE: {Object.entries(row.backtest_negozio).map(([name, score]) => `${name} ${score.mae}`).join(" · ")}</p>}
              </div>
            </details>)}
          </div>
          {Object.keys(comparison.summary.forecast_errors_by_class || {}).length > 0 && <p className="mt-2 text-xs text-slate-500">MAE medio negozio per classe: {Object.entries(comparison.summary.forecast_errors_by_class).map(([name, score]) => `${name} ${score.mean_shop_mae} (${score.products})`).join(" · ")}</p>}
        </>}
      </Card>

      <div data-testid="ao-calculation-note" className="mb-4 rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
        Domanda giornaliera: <b>{Math.round((data?.peso_breve ?? 0.7) * 100)}% ultimi {breveGg}gg + {Math.round((data?.peso_lungo ?? 0.3) * 100)}% ultimi {lungoGg}gg</b>{dataRiferimento ? <> fino al <b>{dataRiferimento}</b></> : null}.
        Il magazzino reale è la <b>giacenza negozio fisica</b> registrata nella WebApp. Il fabbisogno complessivo considera separatamente vending e forniture con data entro l'orizzonte. Ordina sotto {coperturaMin}gg verso il target di {data?.parametri?.GIORNI_COPERTURA_TARGET || 14}gg, con sicurezza ×{data?.fattore_sicurezza || 1.15}.
        Le forniture senza data futura affidabile richiedono verifica manuale. Il valore listino non rappresenta il costo di acquisto.
        Ogni articolo venduto negli ultimi {lungoGg}gg viene analizzato; senza vendite e con scorta sotto il lotto viene segnalato per controllo manuale. La scorta minima facoltativa si gestisce in Magazzino → Prodotti.
      </div>

      <div className="flex flex-col sm:flex-row sm:items-end gap-3 mb-4">
        <SearchBar data-testid="ao-search" value={query} onChange={setQuery} placeholder="Cerca codice, articolo, tipo o motivo…" className="flex-1 max-w-md" />
        <span className="text-sm text-slate-500 self-center">{righe.length} risultati</span>
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
                      <button data-testid={`ao-add-${r.codice}`} onClick={() => addRow(r.codice, Math.max(1, Number(r.lotto_ordine) || 1))} disabled={!canManuallyAdd(r)} className="rounded p-2 text-emerald-700 hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-30" aria-label={`Aggiungi ${r.descrizione}`} title={canManuallyAdd(r) ? "Aggiungi all'ordine" : r.motivo || "Articolo da verificare"}><Plus size={16} /></button>
                    )}
                  </td>
                </tr>
              ))}
              {!loading && !error && righe.length === 0 && <tr><td colSpan={19} className="text-center py-8 text-slate-400">Nessun articolo nella bozza con le categorie selezionate.</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
      <ConfirmDialog
        open={confirmOpen}
        title={`Confermare Auto-Order ${orderPolicy === "AUTO_ORDER_3_1" ? "3.1" : "2.0"}?`}
        confirmLabel="Crea ordine fornitore"
        loading={confirming}
        confirmDisabled={data?.data_quality === "DATI_PARZIALI" && (!acknowledgePartial || partialReason.trim().length < 10)}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={conferma}
      >
        <p>Politica selezionata: <b>{orderPolicy === "AUTO_ORDER_3_1" ? "Auto-Order 3.1 (validazione server)" : "Auto-Order 2.0 (fallback)"}</b>. Verranno create <b>{formatNum(allRighe.length)}</b> righe ordine fornitore per un totale di <b>{formatEur(allRighe.reduce((sum, row) => sum + (row.totale || 0), 0))}</b>.</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-sm">Fornitore <input value={supplierName} onChange={event => setSupplierName(event.target.value)} maxLength={120} placeholder="Nome fornitore (facoltativo)" className="mt-1 w-full rounded border px-2 py-1" /></label>
          <label className="text-sm">Consegna prevista <input type="date" value={expectedDelivery} onChange={event => setExpectedDelivery(event.target.value)} className="mt-1 w-full rounded border px-2 py-1" /></label>
        </div>
        {data?.data_quality === "DATI_PARZIALI" && <div className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm">
          <p className="font-semibold">Fonti parziali: verifica i dati prima dell’ordine.</p>
          <label className="mt-2 flex items-center gap-2"><input type="checkbox" checked={acknowledgePartial} onChange={event => setAcknowledgePartial(event.target.checked)} /> Confermo di aver verificato le fonti</label>
          <label className="mt-2 block">Motivo della deroga<input data-testid="ao-partial-reason" value={partialReason} onChange={event => setPartialReason(event.target.value)} maxLength={500} className="mt-1 w-full rounded border px-2 py-1" placeholder="Indica la verifica svolta (almeno 10 caratteri)" /></label>
        </div>}
        <p className="mt-2 text-slate-600">Questa azione non carica il magazzino: le giacenze aumentano solo dalla pagina <b>Carico Merce</b> quando la merce arriva fisicamente.</p>
      </ConfirmDialog>
    </Layout>
  );
}
