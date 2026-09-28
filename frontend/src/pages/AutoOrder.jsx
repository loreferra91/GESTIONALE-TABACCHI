import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api, API } from "../lib/api";
import { Card, KpiCard, Badge, formatEur, formatNum } from "../components/UI";
import { useSortSearch, Th, SearchBar } from "../lib/tableHooks";
import { toast } from "sonner";

export default function AutoOrder() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [categoria, setCategoria] = useState("");
  const allRighe = data?.righe || [];
  const righeCategoria = categoria ? allRighe.filter(r => r.categoria === categoria) : allRighe;
  const totaleCategoria = righeCategoria.reduce((totale, r) => totale + (r.totale || 0), 0);
  const coperturaMin = data?.parametri?.GIORNI_COPERTURA_MIN || 7;
  const dataRiferimento = data?.data_riferimento_domanda
    ? new Date(`${data.data_riferimento_domanda}T00:00:00`).toLocaleDateString("it-IT")
    : null;
  const { rows: righe, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(righeCategoria, {
    initial: "totale", dir: "desc",
    searchFields: ["codice", "descrizione", "categoria", "motivo"],
  });

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const r = await api.get("/auto-order", { timeout: 20000 });
      setData(r.data);
    } catch (err) {
      const timedOut = err.code === "ECONNABORTED";
      const message = timedOut
        ? "Il calcolo sta richiedendo troppo tempo. Riprova tra qualche secondo."
        : "Impossibile calcolare gli ordini. Controlla la connessione e riprova.";
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const conferma = async () => {
    if (!window.confirm(`Confermi ${data.n_righe} ordini per un totale di € ${data.totale}?`)) return;
    const r = await api.post("/auto-order/conferma");
    toast.success(`${r.data.ordinati} ordini creati`);
    load();
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
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <KpiCard label={categoria ? "Righe selezionate" : "Righe proposte"} value={formatNum(righeCategoria.length)} />
        <KpiCard label={categoria ? "Totale selezione" : "Totale ordine"} value={formatEur(totaleCategoria)} tone="success" />
        <KpiCard label="Copertura minima" value={`${coperturaMin}gg`} />
        <KpiCard label="Vendite considerate" value={`${data?.finestra_domanda_gg || 10}gg`} />
      </div>

      <div className="flex gap-3 mb-4">
        <button data-testid="ao-refresh" onClick={load} disabled={loading} className="border border-slate-300 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors disabled:opacity-40">{loading ? "Calcolo…" : "Ricalcola"}</button>
        <button data-testid="ao-pdf" onClick={downloadPdf} disabled={!righeCategoria.length} className="border border-slate-900 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors disabled:opacity-40">
          Esporta PDF {categoria ? "selezione" : "fornitore"}
        </button>
        <button data-testid="ao-conferma" onClick={conferma} disabled={!allRighe.length || Boolean(categoria)} title={categoria ? "Seleziona Tutte le categorie per confermare l'intero ordine" : ""} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors disabled:opacity-40">
          Conferma tutti gli ordini
        </button>
      </div>

      {error && (
        <div data-testid="ao-error" role="alert" className="mb-4 flex items-center justify-between gap-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <span>{error}</span>
          <button onClick={load} disabled={loading} className="font-semibold underline disabled:opacity-40">Riprova</button>
        </div>
      )}

      <div data-testid="ao-calculation-note" className={`mb-4 rounded-md border px-4 py-3 text-sm ${data?.giorni_ritardo_dati > 2 ? "border-amber-300 bg-amber-50 text-amber-900" : "border-slate-200 bg-slate-50 text-slate-600"}`}>
        Mostra solo articoli venduti nei <b>{data?.finestra_domanda_gg || 10} giorni disponibili</b>{dataRiferimento ? <> fino al <b>{dataRiferimento}</b></> : null} con copertura insufficiente.
        Il magazzino reale è calcolato come <b>giacenza negozio − giacenza vending</b>.
        {data?.giorni_ritardo_dati > 2 ? <span className="block mt-1 font-semibold">Attenzione: l'ultima vendita importata risale a {data.giorni_ritardo_dati} giorni fa. Importa il file Excel aggiornato per un ordine più preciso.</span> : null}
      </div>

      <div className="flex flex-col sm:flex-row sm:items-end gap-3 mb-4">
        <SearchBar data-testid="ao-search" value={query} onChange={setQuery} placeholder="Cerca codice, articolo, tipo o motivo…" className="flex-1 max-w-md" />
        <label className="flex flex-col gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Articoli da visualizzare e stampare
          <select data-testid="ao-filter-cat" value={categoria} onChange={e => setCategoria(e.target.value)} className="min-w-56 border border-slate-300 bg-white text-slate-900 rounded-md px-3 py-2 text-sm font-normal normal-case tracking-normal">
            <option value="">Tutte le categorie</option>
            <option value="SIGARETTE">Solo sigarette</option>
            <option value="SIGARETTE ELETTRONICHE">Solo sigarette elettroniche</option>
            <option value="ACCESSORI">Solo accessori</option>
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
                <Th sortKey="giacenza_negozio" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Giac. negozio</Th>
                <Th sortKey="giacenza_vending" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Giac. vend.</Th>
                <Th sortKey="magazzino_reale" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Mag. reale</Th>
                <Th sortKey="venduto_periodo" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Venduto {data?.finestra_domanda_gg || 10}gg</Th>
                <Th sortKey="copertura_gg" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Cop. (gg)</Th>
                <Th sortKey="lotto_ordine" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Lotto</Th>
                <Th sortKey="qta_da_ordinare" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Qta da ordinare</Th>
                <Th sortKey="prezzo" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Prezzo</Th>
                <Th sortKey="totale" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Totale</Th>
                <Th sortKey="motivo" currentKey={sortKey} dir={sortDir} onClick={toggle}>Motivo</Th>
              </tr>
            </thead>
            <tbody>
              {loading && !data && <tr><td colSpan={13} className="text-center py-8 text-slate-400">Elaborazione…</td></tr>}
              {righe.map(r => (
                <tr key={r.codice} data-testid={`ao-row-${r.codice}`}>
                  <td className="font-mono">{r.codice}</td>
                  <td className="max-w-xs truncate">{r.descrizione}</td>
                  <td><Badge tone="info">{r.categoria}</Badge></td>
                  <td className={`font-mono text-right ${(r.giacenza_negozio||0) <= 0 ? 'text-red-600 font-bold' : ''}`}>{formatNum(r.giacenza_negozio ?? 0)}</td>
                  <td className="font-mono text-right">{formatNum(r.giacenza_vending ?? 0)}</td>
                  <td className={`font-mono text-right ${(r.magazzino_reale||0) <= 0 ? 'text-red-600 font-bold' : ''}`}>{formatNum(r.magazzino_reale ?? 0)}</td>
                  <td className="font-mono text-right">{formatNum(r.venduto_periodo)}</td>
                  <td className={`font-mono text-right ${r.copertura_gg !== null && r.copertura_gg < coperturaMin ? 'text-red-600 font-bold' : ''}`}>{r.copertura_gg === null ? '∞' : r.copertura_gg}</td>
                  <td className="font-mono text-right">{r.lotto_ordine}</td>
                  <td className="font-mono text-right font-bold">{r.qta_da_ordinare}</td>
                  <td className="font-mono text-right">{formatEur(r.prezzo)}</td>
                  <td className="font-mono text-right font-semibold">{formatEur(r.totale)}</td>
                  <td><Badge tone={r.motivo?.includes("FAST") ? "warning" : "info"}>{r.motivo}</Badge></td>
                </tr>
              ))}
              {!loading && !error && righe.length === 0 && <tr><td colSpan={13} className="text-center py-8 text-slate-400">{categoria ? "Nessun ordine necessario per la categoria selezionata." : "Nessun ordine necessario in base alle vendite recenti."}</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </Layout>
  );
}
