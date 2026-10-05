import { useCallback, useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api, API, apiErrorMessage } from "../lib/api";
import { Card, KpiCard, Badge, formatEur, formatNum } from "../components/UI";
import { useSortSearch, Th, SearchBar } from "../lib/tableHooks";
import { toast } from "sonner";
import ConfirmDialog from "../components/ConfirmDialog";
import { Plus, Trash2 } from "lucide-react";

export default function AutoOrder() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [categoria, setCategoria] = useState("");
  const [mostraEsclusi, setMostraEsclusi] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmedKeys, setConfirmedKeys] = useState(() => new Set());
  const [draftRows, setDraftRows] = useState([]);
  const [selectedToAdd, setSelectedToAdd] = useState("");
  const allRighe = draftRows;
  const allEsclusi = data?.esclusi || [];
  const categorieDisponibili = Array.from(new Set([...allRighe, ...allEsclusi].map(r => r.categoria).filter(Boolean))).sort();
  const ordiniCategoria = categoria ? allRighe.filter(r => r.categoria === categoria) : allRighe;
  const draftCodes = new Set(allRighe.map(r => r.codice));
  const esclusiDisponibili = allEsclusi.filter(r => !draftCodes.has(r.codice));
  const esclusiCategoria = categoria ? esclusiDisponibili.filter(r => r.categoria === categoria) : esclusiDisponibili;
  const righeCategoria = mostraEsclusi ? [...ordiniCategoria, ...esclusiCategoria] : ordiniCategoria;
  const totaleCategoria = ordiniCategoria.reduce((totale, r) => totale + (r.totale || 0), 0);
  const coperturaMin = data?.parametri?.GIORNI_COPERTURA_MIN || 7;
  const breveGg = data?.finestra_breve_gg || 10;
  const lungoGg = data?.finestra_lunga_gg || 30;
  const snapshotKey = data?.snapshot_key || "";
  const draftSignature = JSON.stringify(allRighe.map(r => [r.codice, r.qta_da_ordinare]).sort((a, b) => a[0].localeCompare(b[0])));
  const confirmationKey = `${snapshotKey}:${draftSignature}`;
  const alreadyConfirmed = Boolean(snapshotKey && confirmedKeys.has(confirmationKey));
  const dataRiferimento = data?.data_riferimento_domanda
    ? new Date(`${data.data_riferimento_domanda}T00:00:00`).toLocaleDateString("it-IT")
    : null;
  const { rows: righe, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(righeCategoria, {
    initial: "totale", dir: "desc",
    searchFields: ["codice", "descrizione", "categoria", "stato", "motivo"],
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const r = await api.get("/auto-order", { timeout: 20000 });
      setData(r.data);
      setDraftRows(r.data.righe || []);
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
        righe: allRighe.map(row => ({ codice: row.codice, quantita: row.qta_da_ordinare })),
      });
      if (snapshotKey) setConfirmedKeys(keys => new Set(keys).add(confirmationKey));
      toast.success(`${r.data.ordinati} righe ordine fornitore ${r.data.duplicate ? "gia presenti" : "create"}`);
      setConfirmOpen(false);
    } catch (err) {
      toast.error(apiErrorMessage(err, "Errore durante la conferma Auto-Order"));
    } finally {
      setConfirming(false);
    }
  };

  const downloadOrder = (format) => {
    const label = format === "excel" ? "Excel" : "PDF";
    const fornitore = window.prompt(`Nome fornitore per il file ${label}:`, "Fornitore") || "Fornitore";
    const params = new URLSearchParams({ fornitore });
    if (categoria) params.set("categoria", categoria);
    params.set("selezione", JSON.stringify(allRighe.map(row => ({ codice: row.codice, quantita: row.qta_da_ordinare }))));
    const url = `${API}/auto-order/${format}?${params.toString()}`;
    window.open(url, "_blank");
  };

  const changeQuantity = (codice, value) => {
    const quantity = Math.max(1, Math.floor(Number(value) || 1));
    setDraftRows(rows => rows.map(row => row.codice === codice
      ? { ...row, qta_da_ordinare: quantity, totale: Math.round(quantity * (row.prezzo || 0) * 100) / 100 }
      : row));
  };

  const removeRow = (codice) => {
    setDraftRows(rows => rows.filter(row => row.codice !== codice));
  };

  const addRow = (codice = selectedToAdd) => {
    const row = allEsclusi.find(item => item.codice === codice);
    if (!row || row.anomalia || draftCodes.has(row.codice)) return;
    const quantity = Math.max(1, Number(row.lotto_ordine) || 1);
    setDraftRows(rows => [...rows, {
      ...row,
      stato: "ORDINA ORA",
      motivo: `AGGIUNTO MANUALMENTE · ${row.motivo}`,
      qta_da_ordinare: quantity,
      totale: Math.round(quantity * (row.prezzo || 0) * 100) / 100,
    }]);
    setSelectedToAdd("");
  };

  const addableRows = allEsclusi.filter(row => !row.anomalia && !draftCodes.has(row.codice));

  return (
    <Layout title="Auto-Order" subtitle="fabbisogno reale basato sulle vendite recenti">
      <div className="mb-5 grid grid-cols-1 gap-3 rounded-md border border-slate-200 bg-white p-4 text-sm md:grid-cols-4">
        <div><b>1. Import Excel</b><br/><span className="text-slate-500">Aggiorna dati in Parametri.</span></div>
        <div><b>2. Anomalie</b><br/><span className="text-slate-500">Controlla stock e codici.</span></div>
        <div><b>3. Ordine</b><br/><span className="text-slate-500">PDF, Excel o conferma fornitore.</span></div>
        <div><b>4. Carico</b><br/><span className="text-slate-500">Aggiorna magazzino quando arriva.</span></div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <KpiCard label={categoria ? "Ordini selezionati" : "Ordina ora"} value={formatNum(ordiniCategoria.length)} />
        <KpiCard label={categoria ? "Totale selezione" : "Totale ordine"} value={formatEur(totaleCategoria)} tone="success" />
        <KpiCard label="Da controllare" value={formatNum((data?.riepilogo_stati?.["CONTROLLO MANUALE"] || 0) + (data?.n_anomalie_stock || 0))} tone="warning" />
        <KpiCard label="Domanda ponderata" value={`${breveGg}/${lungoGg}gg`} />
      </div>

      <div className="flex flex-wrap gap-3 mb-4">
        <button data-testid="ao-refresh" onClick={load} disabled={loading} className="border border-slate-300 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors disabled:opacity-40">{loading ? "Calcolo…" : "Ricalcola"}</button>
        <button data-testid="ao-pdf" onClick={() => downloadOrder("pdf")} disabled={!ordiniCategoria.length} className="border border-slate-900 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors disabled:opacity-40">
          Esporta PDF {categoria ? "selezione" : "fornitore"}
        </button>
        <button data-testid="ao-excel" onClick={() => downloadOrder("excel")} disabled={!ordiniCategoria.length} className="border border-slate-900 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors disabled:opacity-40">
          Esporta Excel {categoria ? "selezione" : "fornitore"}
        </button>
        <button data-testid="ao-conferma" onClick={() => setConfirmOpen(true)} disabled={!allRighe.length || Boolean(categoria) || confirming || alreadyConfirmed} title={categoria ? "Seleziona Tutte le categorie per confermare l'intero ordine" : alreadyConfirmed ? "Proposta gia confermata" : ""} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors disabled:opacity-40">
          {confirming ? "Conferma..." : alreadyConfirmed ? "Ordine confermato" : "Conferma tutti gli ordini"}
        </button>
        <button data-testid="ao-toggle-excluded" onClick={() => setMostraEsclusi(v => !v)} className="border border-slate-300 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors">
          {mostraEsclusi ? "Nascondi esclusi" : `Mostra esclusi (${esclusiCategoria.length})`}
        </button>
      </div>

      <Card className="mb-4 p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <label htmlFor="ao-add-product" className="text-sm font-semibold text-slate-700">Aggiungi articolo</label>
          <select id="ao-add-product" data-testid="ao-add-product" value={selectedToAdd} onChange={e => setSelectedToAdd(e.target.value)} className="min-w-0 flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm">
            <option value="">Seleziona un articolo escluso dal calcolo…</option>
            {addableRows.map(row => <option key={row.codice} value={row.codice}>{row.codice} · {row.descrizione}</option>)}
          </select>
          <button data-testid="ao-add-product-btn" onClick={() => addRow()} disabled={!selectedToAdd} className="inline-flex items-center justify-center gap-2 rounded-md bg-slate-900 px-4 py-2 text-sm text-white hover:bg-slate-800 disabled:opacity-40">
            <Plus size={16} /> Aggiungi
          </button>
        </div>
      </Card>

      {error && (
        <div data-testid="ao-error" role="alert" className="mb-4 flex items-center justify-between gap-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <span>{error}</span>
          <button onClick={load} disabled={loading} className="font-semibold underline disabled:opacity-40">Riprova</button>
        </div>
      )}

      <div data-testid="ao-calculation-note" className={`mb-4 rounded-md border px-4 py-3 text-sm ${data?.giorni_ritardo_dati > 2 ? "border-amber-300 bg-amber-50 text-amber-900" : "border-slate-200 bg-slate-50 text-slate-600"}`}>
        Domanda giornaliera: <b>{Math.round((data?.peso_breve ?? 0.7) * 100)}% ultimi {breveGg}gg + {Math.round((data?.peso_lungo ?? 0.3) * 100)}% ultimi {lungoGg}gg</b>{dataRiferimento ? <> fino al <b>{dataRiferimento}</b></> : null}.
        Il magazzino reale è la <b>giacenza negozio fisica libera</b> normalizzata dall'import. Ordina sotto {coperturaMin}gg verso il target di {data?.parametri?.GIORNI_COPERTURA_TARGET || 14}gg, con sicurezza ×{data?.fattore_sicurezza || 1.15}.
        {data?.giorni_ritardo_dati > 2 ? <span className="block mt-1 font-semibold">Attenzione: l'ultima vendita importata risale a {data.giorni_ritardo_dati} giorni fa. Importa il file Excel aggiornato per un ordine più preciso.</span> : null}
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
                <Th sortKey="copertura_gg" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Cop. (gg)</Th>
                <Th sortKey="target_scorta" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Target</Th>
                <Th sortKey="lotto_ordine" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Lotto</Th>
                <Th sortKey="qta_da_ordinare" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Qta da ordinare</Th>
                <Th sortKey="totale" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Totale</Th>
                <Th sortKey="stato" currentKey={sortKey} dir={sortDir} onClick={toggle}>Stato</Th>
                <Th sortKey="motivo" currentKey={sortKey} dir={sortDir} onClick={toggle}>Calcolo</Th>
                <th className="w-20">Azioni</th>
              </tr>
            </thead>
            <tbody>
              {loading && !data && <tr><td colSpan={18} className="text-center py-8 text-slate-400">Elaborazione…</td></tr>}
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
                  <td className={`font-mono text-right ${r.copertura_gg !== null && r.copertura_gg < coperturaMin ? 'text-red-600 font-bold' : ''}`}>{r.copertura_gg === null ? '∞' : r.copertura_gg}</td>
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
                      <button data-testid={`ao-add-${r.codice}`} onClick={() => addRow(r.codice)} disabled={r.anomalia} className="rounded p-2 text-emerald-700 hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-30" aria-label={`Aggiungi ${r.descrizione}`} title={r.anomalia ? "Stock anomalo: articolo non ordinabile" : "Aggiungi all'ordine"}><Plus size={16} /></button>
                    )}
                  </td>
                </tr>
              ))}
              {!loading && !error && righe.length === 0 && <tr><td colSpan={18} className="text-center py-8 text-slate-400">{categoria ? "Nessun articolo nella categoria selezionata." : "Nessun articolo nella bozza d'ordine."}</td></tr>}
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
        <p className="mt-2 text-slate-600">Questa azione non carica il magazzino: le giacenze aumentano solo dalla pagina <b>Carico Merce</b> quando la merce arriva fisicamente.</p>
      </ConfirmDialog>
    </Layout>
  );
}
