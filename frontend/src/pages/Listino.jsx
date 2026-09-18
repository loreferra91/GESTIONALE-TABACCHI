import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api } from "../lib/api";
import { Card, formatEur } from "../components/UI";

export default function Listino() {
  const [data, setData] = useState({ items: [], total: 0 });
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    const r = await api.get("/listino", { params: { q, limit: 300 } });
    setData(r.data);
    setLoading(false);
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  return (
    <Layout title="Listino ADM" subtitle={`${data.total} articoli ufficiali`}>
      <div className="flex gap-3 mb-4">
        <input data-testid="listino-search" value={q} onChange={e => setQ(e.target.value)} placeholder="Cerca prodotto (es. TEREA, ELFBAR, MARLBORO…)" className="border rounded-md px-3 py-2 text-sm flex-1 max-w-lg" onKeyDown={e => e.key === "Enter" && load()} />
        <button data-testid="listino-search-btn" onClick={load} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors">Cerca</button>
      </div>
      <Card className="overflow-hidden">
        <div className="overflow-x-auto max-h-[70vh]">
          <table className="data-table w-full">
            <thead>
              <tr><th>Codice</th><th>Descrizione</th><th>Confezione</th><th className="text-right">Prezzo</th></tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={4} className="text-center py-8 text-slate-400">Caricamento…</td></tr>}
              {data.items.map(r => (
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
