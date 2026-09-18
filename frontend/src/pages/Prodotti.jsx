import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api } from "../lib/api";
import { Card, Badge, formatEur, formatNum } from "../components/UI";
import { toast } from "sonner";

export default function Prodotti() {
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ codice: "", descrizione: "", categoria: "ACCESSORI", prezzo: 0, giacenza_negozio: 0, giacenza_vending: 0 });

  const load = async () => {
    setLoading(true);
    const r = await api.get("/prodotti", { params: { q, categoria: cat, limit: 1000 } });
    setRows(r.data);
    setLoading(false);
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const save = async () => {
    try {
      if (editing) await api.put(`/prodotti/${editing}`, form);
      else await api.post("/prodotti", form);
      toast.success(editing ? "Prodotto aggiornato" : "Prodotto creato");
      setEditing(null);
      setForm({ codice: "", descrizione: "", categoria: "ACCESSORI", prezzo: 0, giacenza_negozio: 0, giacenza_vending: 0 });
      load();
    } catch (e) {
      toast.error("Errore salvataggio");
    }
  };

  const del = async (id) => {
    if (!window.confirm("Eliminare?")) return;
    await api.delete(`/prodotti/${id}`);
    toast.success("Eliminato");
    load();
  };

  const startEdit = (r) => {
    setEditing(r.id);
    setForm({ codice: r.codice, descrizione: r.descrizione, categoria: r.categoria, prezzo: r.prezzo, giacenza_negozio: r.giacenza_negozio, giacenza_vending: r.giacenza_vending });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <Layout title="Prodotti" subtitle={`${rows.length} articoli`}>
      <Card className="p-4 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-6 gap-3">
          <input data-testid="prod-form-codice" placeholder="Codice" className="border rounded-md px-3 py-2 text-sm font-mono" value={form.codice} onChange={e => setForm({...form, codice: e.target.value})} />
          <input data-testid="prod-form-desc" placeholder="Descrizione" className="border rounded-md px-3 py-2 text-sm md:col-span-2" value={form.descrizione} onChange={e => setForm({...form, descrizione: e.target.value})} />
          <select data-testid="prod-form-cat" className="border rounded-md px-3 py-2 text-sm" value={form.categoria} onChange={e => setForm({...form, categoria: e.target.value})}>
            <option>SIGARETTE</option>
            <option>SIGARETTE ELETTRONICHE</option>
            <option>ACCESSORI</option>
          </select>
          <input data-testid="prod-form-prezzo" type="number" step="0.01" placeholder="Prezzo" className="border rounded-md px-3 py-2 text-sm font-mono" value={form.prezzo} onChange={e => setForm({...form, prezzo: parseFloat(e.target.value) || 0})} />
          <div className="flex gap-2">
            <button data-testid="prod-save-btn" onClick={save} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-800 transition-colors flex-1">{editing ? "Aggiorna" : "Aggiungi"}</button>
            {editing && <button onClick={() => { setEditing(null); setForm({ codice: "", descrizione: "", categoria: "ACCESSORI", prezzo: 0, giacenza_negozio: 0, giacenza_vending: 0 }); }} className="border rounded-md px-3 text-sm">✕</button>}
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mt-3">
          <input type="number" placeholder="Giacenza negozio" className="border rounded-md px-3 py-2 text-sm font-mono" value={form.giacenza_negozio} onChange={e => setForm({...form, giacenza_negozio: parseInt(e.target.value) || 0})} />
          <input type="number" placeholder="Giacenza vending" className="border rounded-md px-3 py-2 text-sm font-mono" value={form.giacenza_vending} onChange={e => setForm({...form, giacenza_vending: parseInt(e.target.value) || 0})} />
        </div>
      </Card>

      <div className="flex gap-3 mb-4">
        <input data-testid="prod-search" value={q} onChange={e => setQ(e.target.value)} placeholder="Cerca codice o descrizione…" className="border rounded-md px-3 py-2 text-sm flex-1 max-w-md" />
        <select data-testid="prod-filter-cat" value={cat} onChange={e => setCat(e.target.value)} className="border rounded-md px-3 py-2 text-sm">
          <option value="">Tutte le categorie</option>
          <option>SIGARETTE</option>
          <option>SIGARETTE ELETTRONICHE</option>
          <option>ACCESSORI</option>
        </select>
        <button data-testid="prod-search-btn" onClick={load} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors">Filtra</button>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <th>Codice</th><th>Descrizione</th><th>Categoria</th>
                <th className="text-right">Prezzo</th>
                <th className="text-right">Giac. negozio</th>
                <th className="text-right">Giac. vending</th>
                <th className="text-right">Venduti</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={8} className="text-center py-8 text-slate-400">Caricamento…</td></tr>}
              {rows.map(r => (
                <tr key={r.id} data-testid={`prod-row-${r.codice}`}>
                  <td className="font-mono">{r.codice}</td>
                  <td className="max-w-sm truncate">{r.descrizione}</td>
                  <td><Badge tone="info">{r.categoria}</Badge></td>
                  <td className="font-mono text-right">{formatEur(r.prezzo)}</td>
                  <td className={`font-mono text-right ${r.giacenza_negozio < 0 ? 'text-red-600 font-bold' : ''}`}>{formatNum(r.giacenza_negozio)}</td>
                  <td className="font-mono text-right">{formatNum(r.giacenza_vending)}</td>
                  <td className="font-mono text-right">{formatNum((r.venduti_negozio || 0) + (r.venduti_vending || 0))}</td>
                  <td className="text-right whitespace-nowrap">
                    <button data-testid={`prod-edit-${r.codice}`} onClick={() => startEdit(r)} className="text-slate-600 hover:text-slate-900 text-xs mr-3">Modifica</button>
                    <button data-testid={`prod-del-${r.codice}`} onClick={() => del(r.id)} className="text-red-600 hover:text-red-800 text-xs">Elimina</button>
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
