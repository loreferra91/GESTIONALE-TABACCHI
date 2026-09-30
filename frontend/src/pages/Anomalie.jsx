import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api, apiErrorMessage } from "../lib/api";
import { Badge, Card, KpiCard, formatNum } from "../components/UI";
import { useSortSearch, SearchBar, Th } from "../lib/tableHooks";
import { toast } from "sonner";

const toneBySeverity = { alta: "danger", media: "warning", bassa: "default" };
const badgeBySeverity = { alta: "error", media: "warning", bassa: "info" };

export default function Anomalie() {
  const [data, setData] = useState({ summary: {}, items: [] });
  const [loading, setLoading] = useState(true);
  const [severity, setSeverity] = useState("");
  const [type, setType] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.get("/anomalie");
      setData(r.data);
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile caricare le anomalie"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const filtered = (data.items || []).filter(item => (!severity || item.severita === severity) && (!type || item.tipo === type));
  const types = Array.from(new Set((data.items || []).map(i => i.tipo))).sort();
  const { rows, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(filtered, {
    initial: "severita",
    dir: "asc",
    searchFields: ["tipo", "codice", "descrizione", "messaggio", "azione"],
  });

  return (
    <Layout title="Centro anomalie" subtitle="controlli prima di ordinare o importare">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <KpiCard label="Totale anomalie" value={formatNum(data.summary?.totale)} tone={data.summary?.totale ? "warning" : "default"} />
        <KpiCard label="Alta priorita" value={formatNum(data.summary?.alta)} tone={toneBySeverity.alta} />
        <KpiCard label="Media priorita" value={formatNum(data.summary?.media)} tone={toneBySeverity.media} />
        <KpiCard label="Bassa priorita" value={formatNum(data.summary?.bassa)} />
      </div>

      <div className="flex flex-col gap-3 mb-4 md:flex-row">
        <SearchBar value={query} onChange={setQuery} placeholder="Cerca codice, descrizione, messaggio o azione..." className="md:max-w-lg flex-1" />
        <select value={severity} onChange={e => setSeverity(e.target.value)} className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm">
          <option value="">Tutte le priorita</option>
          <option value="alta">Alta</option>
          <option value="media">Media</option>
          <option value="bassa">Bassa</option>
        </select>
        <select value={type} onChange={e => setType(e.target.value)} className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm">
          <option value="">Tutti i tipi</option>
          {types.map(t => <option key={t} value={t}>{t.replaceAll("_", " ")}</option>)}
        </select>
        <button onClick={load} disabled={loading} className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm hover:bg-slate-50 disabled:opacity-40">
          {loading ? "Controllo..." : "Ricontrolla"}
        </button>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <Th sortKey="severita" currentKey={sortKey} dir={sortDir} onClick={toggle}>Priorita</Th>
                <Th sortKey="tipo" currentKey={sortKey} dir={sortDir} onClick={toggle}>Tipo</Th>
                <Th sortKey="codice" currentKey={sortKey} dir={sortDir} onClick={toggle}>Codice</Th>
                <Th sortKey="descrizione" currentKey={sortKey} dir={sortDir} onClick={toggle}>Articolo</Th>
                <th>Problema</th>
                <th>Azione consigliata</th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={6} className="text-center py-8 text-slate-400">Controllo anomalie...</td></tr>}
              {!loading && rows.map((item, idx) => (
                <tr key={`${item.tipo}-${item.codice}-${idx}`}>
                  <td><Badge tone={badgeBySeverity[item.severita] || "info"}>{item.severita}</Badge></td>
                  <td className="font-mono text-xs">{item.tipo.replaceAll("_", " ")}</td>
                  <td className="font-mono">{item.codice || "—"}</td>
                  <td className="max-w-xs truncate">{item.descrizione || "—"}</td>
                  <td className="min-w-64 whitespace-normal">{item.messaggio}</td>
                  <td className="min-w-64 whitespace-normal font-semibold text-slate-700">{item.azione}</td>
                </tr>
              ))}
              {!loading && rows.length === 0 && <tr><td colSpan={6} className="text-center py-8 text-slate-400">Nessuna anomalia con i filtri attuali.</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </Layout>
  );
}
