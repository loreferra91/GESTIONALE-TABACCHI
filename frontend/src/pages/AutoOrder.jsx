import { useCallback, useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api, API, apiErrorMessage } from "../lib/api";
import { Card, KpiCard, Badge, formatEur, formatNum } from "../components/UI";
import { useSortSearch, Th, SearchBar } from "../lib/tableHooks";
import { toast } from "sonner";
import ConfirmDialog from "../components/ConfirmDialog";

export default function AutoOrder() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [categoria, setCategoria] = useState("");
  const [mostraEsclusi, setMostraEsclusi] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmedKeys, setConfirmedKeys] = useState(() => new Set());
  const allRighe = data?.righe || [];
  const allEsclusi = data?.esclusi || [];
  const categorieDisponibili = Array.from(new Set([...allRighe, ...allEsclusi].map(r => r.categoria).filter(Boolean))).sort();
  const ordiniCategoria = categoria ? allRighe.filter(r => r.categoria === categoria) : allRighe;
  const esclusiCategoria = categoria ? allEsclusi.filter(r => r.categoria === categoria) : allEsclusi;
  const righeCategoria = mostraEsclusi ? [...ordiniCategoria, ...esclusiCategoria] : ordiniCategoria;
  const totaleCategoria = ordiniCategoria.reduce((totale, r) => totale + (r.totale || 0), 0);
  const coperturaMin = data?.parametri?.GIORNI_COPERTURA_MIN || 7;
  const breveGg = data?.finestra_breve_gg || 10;
  const lungoGg = data?.finestra_lunga_gg || 30;
  const snapshotKey = data?.snapshot_key || "";
  const alreadyConfirmed = Boolean(snapshotKey && confirmedKeys.has(snapshotKey));
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
      const r = await api.post("/auto-order/conferma", { idempotency_key: snapshotKey });
      if (snapshotKey) setConfirmedKeys(keys => new Set(keys).add(snapshotKey));
      toast.success(`${r.data.ordinati} righe ordine fornitore ${r.data.duplicate ? "gia presenti" : "create"}`);
      setConfirmOpen(false);
      load();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Errore durante la conferma Auto-Order"));
    } finally {
      setConfirming(false);
    }
  };

  const downloadPdf = async () => {
    const fornitore = window.prompt("Nome fornitore per il PDF:", "Fornitore") || "Fornitore";
    const params = new URLSearchParams({ fornitore });
    if (categoria) params.set("categoria", categoria);
    const url = `${API}/auto-order/pdf?${params.toString()}`;
    window.open(url, "_blank");
  };

  return (
    <Layout title="Auto-Order" subtitle="fabbisogno reale basato sulle vendite recenti">
      <div className="mb-5 grid grid-cols-1 gap-3 rounded-md border border-slate-200 bg-white p-4 text-sm md:grid-cols-4">
        <div><b>1. Import Excel</b><br/><span className="text-slate-500">Aggiorna dati in Parametri.</span></div>
        <div><b>2. Anomalie</b><br/><span className="text-slate-500">Controlla stock e codici.</span></div>
        <div><b>3. Ordine</b><br/><span className="text-slate-500">PDF o conferma fornitore.</span></div>
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
        <button data-testid="ao-pdf" onClick={downloadPdf} disabled={!ordiniCategoria.length} className="border border-slate-900 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors disabled:opacity-40">
          Esporta PDF {categoria ? "selezione" : "fornitore"}
        </button>
        <button data-testid="ao-conferma" onClick={() => setConfirmOpen(true)} disabled={!allRighe.length || Boolean(categoria) || confirming || alreadyConfirmed} title={categoria ? "Seleziona Tutte le categorie per confermare l'intero ordine" : alreadyConfirmed ? "Proposta gia confermata" : ""} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors disabled:opacity-40">
          {confirming ? "Conferma..." : alreadyConfirmed ? "Ordine confermato" : "Conferma tutti gli ordini"}
        </button>
        <button data-testid="ao-toggle-excluded" onClick={() => setMostraEsclusi(v => !v)} className="border border-slate-300 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors">
          {mostraEsclusi ? "Nascondi esclusi" : `Mostra esclusi (${esclusiCategoria.length})`}
        </button>
      </div>

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
              </tr>
            </thead>
            <tbody>
              {loading && !data && <tr><td colSpan={17} className="text-center py-8 text-slate-400">Elaborazione…</td></tr>}
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
                  <td className="font-mono text-right font-bold">{r.qta_da_ordinare || "—"}</td>
                  <td className="font-mono text-right font-semibold">{r.totale ? formatEur(r.totale) : "—"}</td>
                  <td className="space-x-1"><Badge tone={r.stato === "ORDINA ORA" ? "error" : r.stato === "MONITORA" || r.stato === "CONTROLLO MANUALE" ? "warning" : r.stato === "ANOMALIA" ? "error" : "info"}>{r.stato}</Badge>{r.anomalia && r.stato !== "ANOMALIA" ? <Badge tone="error">ANOMALIA STOCK</Badge> : null}</td>
                  <td className="min-w-64 text-xs">{r.motivo}</td>
                </tr>
              ))}
              {!loading && !error && righe.length === 0 && <tr><td colSpan={17} className="text-center py-8 text-slate-400">{categoria ? "Nessun ordine necessario per la categoria selezionata." : "Nessun ordine necessario in base alle vendite recenti."}</td></tr>}
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
        <p>Verranno create <b>{formatNum(data?.n_righe)}</b> righe ordine fornitore per un totale di <b>{formatEur(data?.totale)}</b>.</p>
        <p className="mt-2 text-slate-600">Questa azione non carica il magazzino: le giacenze aumentano solo dalla pagina <b>Carico Merce</b> quando la merce arriva fisicamente.</p>
      </ConfirmDialog>
    </Layout>
  );
}
