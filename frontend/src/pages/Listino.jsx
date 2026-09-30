import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api, apiErrorMessage } from "../lib/api";
import { Badge, Card, formatEur, formatNum } from "../components/UI";
import { useSortSearch, Th, SearchBar } from "../lib/tableHooks";
import { toast } from "sonner";

export default function Listino() {
  const [allItems, setAllItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [admStatus, setAdmStatus] = useState(null);
  const [categoria, setCategoria] = useState("");

  const filtered = categoria ? allItems.filter(item => item.categoria_adm === categoria) : allItems;
  const categorie = Array.from(new Set(allItems.map(item => item.categoria_adm).filter(Boolean))).sort();
  const { rows, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(filtered, {
    initial: "descrizione", dir: "asc",
    searchFields: ["codice", "adm_codice", "descrizione", "confezione", "categoria_adm"],
  });

  const load = async () => {
    setLoading(true);
    const r = await api.get("/listino", { params: { limit: 5000 } });
    setAllItems(r.data.items);
    setTotal(r.data.total);
    setLoading(false);
  };
  const loadAdmStatus = async () => {
    try {
      const r = await api.get("/adm/categories");
      setAdmStatus(r.data);
    } catch (err) {
      console.warn("adm status failed", err);
    }
  };
  useEffect(() => { load(); loadAdmStatus(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const syncAdm = async () => {
    setSyncing(true);
    try {
      const r = await api.post("/adm/sync", {}, { timeout: 180000 });
      toast.success(`ADM sincronizzato: ${formatNum(r.data.righe)} righe, ${formatNum(r.data.prodotti_aggiornati)} prodotti collegati`);
      await load();
      await loadAdmStatus();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Errore sincronizzazione ADM"));
    } finally {
      setSyncing(false);
    }
  };

  return (
    <Layout title="Listino ADM" subtitle={`${total} articoli ufficiali · ${rows.length} in vista`} actions={
      <button onClick={syncAdm} disabled={syncing} className="rounded-md bg-slate-900 px-4 py-2 text-sm font-bold text-white hover:bg-slate-800 disabled:opacity-40">
        {syncing ? "Sincronizzo..." : "Sincronizza ADM"}
      </button>
    }>
      <Card className="p-4 mb-4">
        <div className="flex flex-wrap items-center gap-2 text-sm text-slate-700">
          <Badge tone={admStatus?.last_sync ? "ok" : "warning"}>{admStatus?.last_sync ? "ADM collegato" : "ADM da sincronizzare"}</Badge>
          <span>Categorie trovate sul sito: <b>{formatNum(admStatus?.categories?.length)}</b></span>
          {admStatus?.last_sync && (
            <>
              <span>Ultimo sync: <b>{new Date(admStatus.last_sync.created_at).toLocaleString("it-IT")}</b></span>
              <span>Righe ADM: <b>{formatNum(admStatus.last_sync.righe)}</b></span>
              <span>Prodotti collegati: <b>{formatNum(admStatus.last_sync.prodotti_collegati ?? admStatus.last_sync.prodotti_aggiornati)}</b></span>
            </>
          )}
        </div>
      </Card>
      <div className="flex flex-col md:flex-row gap-3 mb-4">
        <SearchBar data-testid="listino-search" value={query} onChange={setQuery} placeholder="Cerca prodotto (es. TEREA, ELFBAR, MARLBORO…)" className="flex-1 max-w-lg" />
        <select value={categoria} onChange={e => setCategoria(e.target.value)} className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm">
          <option value="">Tutte le categorie ADM</option>
          {categorie.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      <Card className="overflow-hidden">
        <div className="overflow-x-auto max-h-[70vh]">
          <table className="data-table w-full">
            <thead>
              <tr>
                <Th sortKey="codice" currentKey={sortKey} dir={sortDir} onClick={toggle}>Codice</Th>
                <Th sortKey="adm_codice" currentKey={sortKey} dir={sortDir} onClick={toggle}>Cod. ADM</Th>
                <Th sortKey="descrizione" currentKey={sortKey} dir={sortDir} onClick={toggle}>Descrizione</Th>
                <Th sortKey="categoria_adm" currentKey={sortKey} dir={sortDir} onClick={toggle}>Categoria ADM</Th>
                <Th sortKey="confezione" currentKey={sortKey} dir={sortDir} onClick={toggle}>Confezione</Th>
                <Th sortKey="prezzo" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Prezzo</Th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={6} className="text-center py-8 text-slate-400">Caricamento…</td></tr>}
              {rows.map(r => (
                <tr key={r.id}>
                  <td className="font-mono">{r.codice}</td>
                  <td className="font-mono text-slate-500">{r.adm_codice || "—"}</td>
                  <td>{r.descrizione}</td>
                  <td>{r.categoria_adm ? <Badge tone="info">{r.categoria_adm}</Badge> : <span className="text-slate-400">—</span>}</td>
                  <td className="text-slate-500 text-xs">{r.confezione}</td>
                  <td className="font-mono text-right font-semibold">{formatEur(r.prezzo)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </Layout>
  );
}
