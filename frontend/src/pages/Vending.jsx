import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api } from "../lib/api";
import { Card, Badge, KpiCard } from "../components/UI";
import { toast } from "sonner";

export default function Vending() {
  const [rows, setRows] = useState([]);
  const [sel, setSel] = useState(null);
  const [qta, setQta] = useState(0);

  const load = async () => {
    const r = await api.get("/vending");
    setRows(r.data);
  };
  useEffect(() => { load(); }, []);

  const rica = async (v) => {
    const q = window.prompt(`Ricarica colonna ${v.colonna} — capacità ${v.capacita_max}, attuale ${v.giacenza}. Quanti pezzi?`, v.proposta);
    if (!q) return;
    await api.post(`/vending/${v.id}/ricarica`, { quantita: parseInt(q, 10) });
    toast.success(`Colonna ${v.colonna} ricaricata`);
    load();
  };

  const daCaricare = rows.filter(r => r.esito === "DA CARICARE").length;
  const piene = rows.filter(r => r.esito === "PIENO").length;
  const totProposta = rows.reduce((s, r) => s + (r.proposta || 0), 0);

  const cellTone = (r) => {
    if (r.esito === "DA CARICARE") return "bg-red-50 border-red-200";
    if (r.esito === "PIENO") return "bg-emerald-50 border-emerald-200";
    if (r.esito === "OLTRE CAPACITA") return "bg-amber-50 border-amber-200";
    return "bg-slate-50 border-slate-200";
  };

  return (
    <Layout title="Distributore Vending" subtitle={`${rows.length} colonne mappate`}>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <KpiCard label="Colonne totali" value={rows.length} />
        <KpiCard label="Da caricare" value={daCaricare} tone={daCaricare ? "danger" : "default"} />
        <KpiCard label="Piene" value={piene} tone="success" />
        <KpiCard label="Pezzi da caricare" value={totProposta} />
      </div>

      <div className="grid grid-cols-4 md:grid-cols-6 lg:grid-cols-8 2xl:grid-cols-12 gap-2 mb-8">
        {rows.map(r => (
          <button
            key={r.id}
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

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <th>Colonna</th><th>Codice</th><th>Descrizione</th>
                <th className="text-right">Giacenza</th>
                <th className="text-right">Capacità</th>
                <th className="text-right">Soglia</th>
                <th className="text-right">Proposta</th>
                <th>Stato</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id} data-testid={`vending-row-${r.colonna}`}>
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
