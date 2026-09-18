import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api } from "../lib/api";
import { Card, formatEur, formatNum } from "../components/UI";
import { toast } from "sonner";

export default function Ordini() {
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = useState({ data: today, codice: "", descrizione: "", quantita: 10, prezzo: 0 });

  const load = async () => {
    const r = await api.get("/ordini");
    setRows(r.data);
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!form.codice || !form.quantita) return toast.error("Codice e quantità richiesti");
    await api.post("/ordini", form);
    toast.success("Ordine registrato");
    setForm({ ...form, codice: "", descrizione: "", quantita: 10, prezzo: 0 });
    load();
  };

  const del = async (id) => {
    if (!window.confirm("Eliminare?")) return;
    await api.delete(`/ordini/${id}`);
    load();
  };

  const filtered = rows.filter(r => !q || r.codice.toLowerCase().includes(q.toLowerCase()) || (r.descrizione || "").toLowerCase().includes(q.toLowerCase()));
  const totQ = filtered.reduce((s, r) => s + (r.quantita || 0), 0);
  const totV = filtered.reduce((s, r) => s + (r.quantita || 0) * (r.prezzo || 0), 0);

  return (
    <Layout title="Storico ordini" subtitle={`${rows.length} righe totali`}>
      <Card className="p-4 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-6 gap-3">
          <input data-testid="ord-data" type="date" value={form.data} onChange={e => setForm({...form, data: e.target.value})} className="border rounded-md px-3 py-2 text-sm" />
          <input data-testid="ord-codice" placeholder="Codice" value={form.codice} onChange={e => setForm({...form, codice: e.target.value})} className="border rounded-md px-3 py-2 text-sm font-mono" />
          <input data-testid="ord-desc" placeholder="Descrizione" value={form.descrizione} onChange={e => setForm({...form, descrizione: e.target.value})} className="border rounded-md px-3 py-2 text-sm md:col-span-2" />
          <input data-testid="ord-qta" type="number" placeholder="Qta" value={form.quantita} onChange={e => setForm({...form, quantita: parseInt(e.target.value) || 0})} className="border rounded-md px-3 py-2 text-sm font-mono" />
          <div className="flex gap-2">
            <input data-testid="ord-prezzo" type="number" step="0.01" placeholder="Prezzo" value={form.prezzo} onChange={e => setForm({...form, prezzo: parseFloat(e.target.value) || 0})} className="border rounded-md px-3 py-2 text-sm font-mono flex-1" />
            <button data-testid="ord-save-btn" onClick={save} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors">+</button>
          </div>
        </div>
      </Card>

      <div className="flex items-center justify-between mb-4">
        <input data-testid="ord-search" value={q} onChange={e => setQ(e.target.value)} placeholder="Cerca…" className="border rounded-md px-3 py-2 text-sm max-w-md" />
        <div className="text-sm text-slate-600">Totale filtrato: <span className="font-heading font-black text-lg text-slate-900 ml-2">{formatEur(totV)}</span> · {formatNum(totQ)} pz</div>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto max-h-[70vh]">
          <table className="data-table w-full">
            <thead>
              <tr><th>Data</th><th>Codice</th><th>Descrizione</th><th className="text-right">Qta</th><th className="text-right">Prezzo</th><th className="text-right">Totale</th><th>Sorgente</th><th></th></tr>
            </thead>
            <tbody>
              {filtered.map(r => (
                <tr key={r.id} data-testid={`ord-row-${r.id}`}>
                  <td className="font-mono text-xs">{(r.data || "").slice(0, 10)}</td>
                  <td className="font-mono">{r.codice}</td>
                  <td className="max-w-sm truncate">{r.descrizione}</td>
                  <td className="font-mono text-right">{r.quantita}</td>
                  <td className="font-mono text-right">{formatEur(r.prezzo)}</td>
                  <td className="font-mono text-right font-semibold">{formatEur(r.quantita * r.prezzo)}</td>
                  <td className="text-xs text-slate-500">{r.file_sorgente}</td>
                  <td className="text-right"><button onClick={() => del(r.id)} className="text-red-600 text-xs">✕</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </Layout>
  );
}
