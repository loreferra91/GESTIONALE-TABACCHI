import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api } from "../lib/api";
import { Card, formatEur } from "../components/UI";
import { useSortSearch, Th, SearchBar } from "../lib/tableHooks";

export default function Listino() {
  const [allItems, setAllItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const { rows, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(allItems, {
    initial: "descrizione", dir: "asc",
    searchFields: ["codice", "descrizione", "confezione"],
  });

  const load = async () => {
    setLoading(true);
    const r = await api.get("/listino", { params: { limit: 5000 } });
    setAllItems(r.data.items);
    setTotal(r.data.total);
    setLoading(false);
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Layout title="Listino ADM" subtitle={`${total} articoli ufficiali · ${rows.length} in vista`}>
      <div className="flex gap-3 mb-4">
        <SearchBar data-testid="listino-search" value={query} onChange={setQuery} placeholder="Cerca prodotto (es. TEREA, ELFBAR, MARLBORO…)" className="flex-1 max-w-lg" />
      </div>
      <Card className="overflow-hidden">
        <div className="overflow-x-auto max-h-[70vh]">
          <table className="data-table w-full">
            <thead>
              <tr>
                <Th sortKey="codice" currentKey={sortKey} dir={sortDir} onClick={toggle}>Codice</Th>
                <Th sortKey="descrizione" currentKey={sortKey} dir={sortDir} onClick={toggle}>Descrizione</Th>
                <Th sortKey="confezione" currentKey={sortKey} dir={sortDir} onClick={toggle}>Confezione</Th>
                <Th sortKey="prezzo" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Prezzo</Th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={4} className="text-center py-8 text-slate-400">Caricamento…</td></tr>}
              {rows.map(r => (
                <tr key={r.id}>
                  <td className="font-mono">{r.codice}</td>
                  <td>{r.descrizione}</td>
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
