import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api, API } from "../lib/api";
import { Card, Badge, KpiCard } from "../components/UI";
import { useSortSearch, Th, SearchBar } from "../lib/tableHooks";
import { toast } from "sonner";
import { Printer } from "@phosphor-icons/react";

export default function Vending() {
  const [allRows, setAllRows] = useState([]);

  const load = async () => {
    const r = await api.get("/vending");
    setAllRows(r.data);
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const { rows, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(allRows, {
    initial: "colonna", dir: "asc",
    searchFields: ["colonna", "codice", "descrizione", "esito"],
  });

  const rica = async (v) => {
    const q = window.prompt(`Ricarica colonna ${v.colonna} — capacità ${v.capacita_max}, attuale ${v.giacenza}. Quanti pezzi?`, v.proposta);
    if (!q) return;
    await api.post(`/vending/${v.id}/ricarica`, { quantita: parseInt(q, 10) });
    toast.success(`Colonna ${v.colonna} ricaricata`);
    load();
  };

  const daCaricare = allRows.filter(r => r.esito === "DA CARICARE").length;
  const piene = allRows.filter(r => r.esito === "PIENO").length;
  const totProposta = allRows.reduce((s, r) => s + (r.proposta || 0), 0);

  const stampaPdf = () => {
    window.open(`${API}/vending/ricarica-pdf`, "_blank");
  };

  const cellTone = (r) => {
    if (r.esito === "DA CARICARE") return "bg-red-50 border-red-200";
    if (r.esito === "PIENO") return "bg-emerald-50 border-emerald-200";
    if (r.esito === "OLTRE CAPACITA") return "bg-amber-50 border-amber-200";
    return "bg-slate-50 border-slate-200";
  };

  return (
    <Layout title="Distributore Vending" subtitle={`${allRows.length} colonne mappate`} actions={
      <button data-testid="vending-pdf" onClick={stampaPdf} disabled={!daCaricare} className="flex items-center gap-2 bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors disabled:opacity-40">
        <Printer size={16} weight="bold" /> Stampa da caricare ({daCaricare})
      </button>
    }>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <KpiCard label="Colonne totali" value={allRows.length} />
        <KpiCard label="Da caricare" value={daCaricare} tone={daCaricare ? "danger" : "default"} />
        <KpiCard label="Piene" value={piene} tone="success" />
        <KpiCard label="Pezzi da caricare" value={totProposta} />
      </div>

      <div className="grid grid-cols-4 md:grid-cols-6 lg:grid-cols-8 2xl:grid-cols-12 gap-2 mb-8">
        {allRows.map(r => (
          <button
            key={r.colonna}
            data-testid={`vending-cell-${r.colonna}`}
            onClick={() => rica(r)}
            className={`aspect-square flex flex-col items-center justify-center border rounded-md p-1 hover:scale-[1.03] transition-transform ${cellTone(r)}`}
            title={`${r.codice} · ${r.descrizione}\n${r.giacenza}/${r.capacita_max} · soglia ${r.soglia_minima}`}
          >
            <div className="font-mono font-bold text-xs">{r.colonna}</div>
            <div className="font-mono text-[10px] mt-1">{r.giacenza}/{r.capacita_max}</div>
            {r.proposta > 0 && <div className="text-[9px] text-red-600 font-bold mt-1">+{r.proposta}</div>}
          </button>
        ))}
      </div>

      <div className="flex gap-3 mb-4">
        <SearchBar data-testid="vending-search" value={query} onChange={setQuery} placeholder="Cerca colonna, codice o descrizione…" className="flex-1 max-w-md" />
        <span className="text-sm text-slate-500 self-center">{rows.length} risultati</span>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <Th sortKey="colonna" currentKey={sortKey} dir={sortDir} onClick={toggle}>Colonna</Th>
                <Th sortKey="codice" currentKey={sortKey} dir={sortDir} onClick={toggle}>Codice</Th>
                <Th sortKey="descrizione" currentKey={sortKey} dir={sortDir} onClick={toggle}>Descrizione</Th>
                <Th sortKey="giacenza" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Giacenza</Th>
                <Th sortKey="capacita_max" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Capacità</Th>
                <Th sortKey="soglia_minima" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Soglia</Th>
                <Th sortKey="proposta" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Proposta</Th>
                <Th sortKey="esito" currentKey={sortKey} dir={sortDir} onClick={toggle}>Stato</Th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.colonna} data-testid={`vending-row-${r.colonna}`}>
                  <td className="font-mono font-bold">{r.colonna}</td>
                  <td className="font-mono">{r.codice}</td>
                  <td className="max-w-sm truncate">{r.descrizione}</td>
                  <td className="font-mono text-right">{r.giacenza}</td>
                  <td className="font-mono text-right">{r.capacita_max}</td>
                  <td className="font-mono text-right">{r.soglia_minima}</td>
                  <td className="font-mono text-right font-semibold">{r.proposta || "—"}</td>
                  <td><Badge tone={r.esito === "PIENO" ? "ok" : r.esito === "DA CARICARE" ? "error" : "warning"}>{r.esito}</Badge></td>
                  <td className="text-right">
                    {r.proposta > 0 && (
                      <button data-testid={`vending-rica-${r.colonna}`} onClick={() => rica(r)} className="bg-slate-900 text-white rounded-md px-3 py-1 text-xs hover:bg-slate-800 transition-colors">Ricarica</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </Layout>
  );
}
