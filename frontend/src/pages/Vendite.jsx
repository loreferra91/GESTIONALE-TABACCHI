import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api } from "../lib/api";
import { Card, Badge, formatEur, formatNum } from "../components/UI";
import { toast } from "sonner";

export default function Vendite() {
  const [rows, setRows] = useState([]);
  const [giorno, setGiorno] = useState(new Date().toISOString().slice(0, 10));
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = useState({ data: today, codice: "", descrizione: "", quantita: 1, importo: 0, canale: "NEGOZIO", pagamento: "CONTANTI" });

  const load = async () => {
    const r = await api.get("/vendite", { params: { giorno } });
    setRows(r.data);
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [giorno]);

  const save = async () => {
    if (!form.codice || !form.importo) { toast.error("Compila codice e importo"); return; }
    await api.post("/vendite", form);
    toast.success("Vendita registrata");
    setForm({ ...form, codice: "", descrizione: "", quantita: 1, importo: 0 });
    load();
  };

  const del = async (id) => {
    if (!window.confirm("Eliminare?")) return;
    await api.delete(`/vendite/${id}`);
    toast.success("Eliminata");
    load();
  };

  const lookupPrezzo = async () => {
    if (!form.codice) return;
    try {
      const r = await api.get("/prodotti", { params: { q: form.codice } });
      const p = (r.data || []).find(x => x.codice === form.codice);
      if (p) setForm(f => ({ ...f, descrizione: p.descrizione, importo: p.prezzo * (f.quantita || 1) }));
    } catch {}
  };

  const tot = rows.reduce((s, r) => s + (r.importo || 0), 0);
  const pezzi = rows.reduce((s, r) => s + (r.quantita || 0), 0);

  return (
    <Layout title="Vendite giornaliere" subtitle={`Registrazioni del ${giorno}`}>
      <Card className="p-4 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-7 gap-3">
          <input data-testid="vend-data" type="date" value={form.data} onChange={e => setForm({...form, data: e.target.value})} className="border rounded-md px-3 py-2 text-sm" />
          <input data-testid="vend-codice" placeholder="Codice" value={form.codice} onChange={e => setForm({...form, codice: e.target.value})} onBlur={lookupPrezzo} className="border rounded-md px-3 py-2 text-sm font-mono" />
          <input data-testid="vend-desc" placeholder="Descrizione" value={form.descrizione} onChange={e => setForm({...form, descrizione: e.target.value})} className="border rounded-md px-3 py-2 text-sm md:col-span-2" />
          <input data-testid="vend-qta" type="number" placeholder="Qta" value={form.quantita} onChange={e => setForm({...form, quantita: parseInt(e.target.value) || 1})} className="border rounded-md px-3 py-2 text-sm font-mono" />
          <input data-testid="vend-importo" type="number" step="0.01" placeholder="Importo €" value={form.importo} onChange={e => setForm({...form, importo: parseFloat(e.target.value) || 0})} className="border rounded-md px-3 py-2 text-sm font-mono" />
          <button data-testid="vend-save-btn" onClick={save} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors">Registra</button>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
          <select data-testid="vend-canale" value={form.canale} onChange={e => setForm({...form, canale: e.target.value})} className="border rounded-md px-3 py-2 text-sm">
            <option>NEGOZIO</option><option>VENDING</option>
          </select>
          <select data-testid="vend-pagamento" value={form.pagamento} onChange={e => setForm({...form, pagamento: e.target.value})} className="border rounded-md px-3 py-2 text-sm">
            <option>CONTANTI</option><option>POS</option><option>SATISPAY</option><option>ALTRO</option>
          </select>
        </div>
      </Card>

      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <label className="text-sm text-slate-600">Filtra giorno:</label>
          <input data-testid="vend-giorno-filter" type="date" value={giorno} onChange={e => setGiorno(e.target.value)} className="border rounded-md px-3 py-2 text-sm" />
        </div>
        <div className="flex gap-2 items-baseline">
          <Badge tone="info">{formatNum(pezzi)} pz</Badge>
          <span className="font-heading font-black text-xl text-slate-900">{formatEur(tot)}</span>
        </div>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr><th>Ora</th><th>Codice</th><th>Descrizione</th><th className="text-right">Qta</th><th className="text-right">Importo</th><th>Canale</th><th>Pagamento</th><th></th></tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id} data-testid={`vend-row-${r.id}`}>
                  <td className="font-mono text-xs text-slate-500">{r.data.slice(11, 16) || "—"}</td>
                  <td className="font-mono">{r.codice}</td>
                  <td className="max-w-sm truncate">{r.descrizione}</td>
                  <td className="font-mono text-right">{r.quantita}</td>
                  <td className="font-mono text-right font-semibold">{formatEur(r.importo)}</td>
                  <td><Badge tone={r.canale === "VENDING" ? "warning" : "info"}>{r.canale}</Badge></td>
                  <td className="text-xs">{r.pagamento}</td>
                  <td className="text-right"><button onClick={() => del(r.id)} className="text-red-600 hover:text-red-800 text-xs">✕</button></td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={8} className="text-center py-8 text-slate-400">Nessuna vendita per {giorno}</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </Layout>
  );
}
