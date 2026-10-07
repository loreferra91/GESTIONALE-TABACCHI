import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Layout from "../components/Layout";
import { api, apiErrorMessage } from "../lib/api";
import { Card, Badge, formatEur, formatNum } from "../components/UI";
import { useSortSearch, Th, SearchBar } from "../lib/tableHooks";
import { toast } from "sonner";
import { PRODUCT_CATEGORIES } from "../lib/categories";

export default function Prodotti() {
  const [searchParams] = useSearchParams();
  const [allRows, setAllRows] = useState([]);
  const [cat, setCat] = useState("");
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ codice: "", descrizione: "", categoria: "ACCESSORI", prezzo: "", giacenza_negozio: "" });

  const filtered = cat ? allRows.filter(r => r.categoria === cat) : allRows;
  const { rows, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(filtered, {
    initial: "codice", dir: "asc",
    searchFields: ["codice", "descrizione", "categoria", "categoria_adm", "adm_codice"],
  });

  useEffect(() => {
    const search = searchParams.get("search");
    if (search) setQuery(search);
  }, [searchParams, setQuery]);

  const load = async () => {
    setLoading(true);
    const r = await api.get("/prodotti", { params: { limit: 5000 } });
    setAllRows(r.data);
    setLoading(false);
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const save = async () => {
    try {
      const payload = {
        ...form,
        prezzo: parseFloat(form.prezzo) || 0,
        giacenza_negozio: parseInt(form.giacenza_negozio) || 0,
      };
      if (editing) await api.put(`/prodotti/${editing}`, payload);
      else await api.post("/prodotti", payload);
      toast.success(editing ? "Prodotto aggiornato" : "Prodotto creato");
      setEditing(null);
      setForm({ codice: "", descrizione: "", categoria: "ACCESSORI", prezzo: "", giacenza_negozio: "" });
      load();
    } catch (e) {
      toast.error(apiErrorMessage(e, "Errore salvataggio prodotto"));
    }
  };

  const del = async (id) => {
    if (!window.confirm("Eliminare?")) return;
    try {
      await api.delete(`/prodotti/${id}`);
      toast.success("Eliminato");
      load();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Errore eliminazione prodotto"));
    }
  };

  const startEdit = (r) => {
    setEditing(r.id);
    setForm({ codice: r.codice, descrizione: r.descrizione, categoria: r.categoria, prezzo: String(r.prezzo ?? ""), giacenza_negozio: String(r.giacenza_negozio ?? "") });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <Layout title="Prodotti" subtitle={`${rows.length} articoli`}>
      <Card className="p-4 mb-6">
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
          <input data-testid="prod-form-codice" placeholder="Codice" className="border rounded-md px-3 py-2 text-sm font-mono" value={form.codice} onChange={e => setForm({...form, codice: e.target.value})} />
          <input data-testid="prod-form-desc" placeholder="Descrizione" className="border rounded-md px-3 py-2 text-sm col-span-2" value={form.descrizione} onChange={e => setForm({...form, descrizione: e.target.value})} />
          <select data-testid="prod-form-cat" className="border rounded-md px-3 py-2 text-sm" value={form.categoria} onChange={e => setForm({...form, categoria: e.target.value})}>
            {PRODUCT_CATEGORIES.map(c => <option key={c}>{c}</option>)}
          </select>
          <input data-testid="prod-form-prezzo" type="number" step="0.01" placeholder="Prezzo" className="border rounded-md px-3 py-2 text-sm font-mono" value={form.prezzo} onChange={e => setForm({...form, prezzo: e.target.value})} />
          <div className="flex gap-2 col-span-2 md:col-span-1">
            <button data-testid="prod-save-btn" onClick={save} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-slate-800 transition-colors flex-1">{editing ? "Aggiorna" : "Aggiungi"}</button>
            {editing && <button onClick={() => { setEditing(null); setForm({ codice: "", descrizione: "", categoria: "ACCESSORI", prezzo: "", giacenza_negozio: "" }); }} className="border rounded-md px-3 text-sm">✕</button>}
          </div>
        </div>
        <div className="mt-3 md:max-w-md">
          <input type="number" placeholder="Giacenza negozio" className="border rounded-md px-3 py-2 text-sm font-mono" value={form.giacenza_negozio} onChange={e => setForm({...form, giacenza_negozio: e.target.value})} />
          <p className="mt-2 text-xs text-slate-500">La giacenza vending è calcolata automaticamente dalla somma delle colonne Vending.</p>
        </div>
      </Card>

      <div className="flex flex-col sm:flex-row gap-3 mb-4">
        <SearchBar data-testid="prod-search" value={query} onChange={setQuery} placeholder="Cerca codice, descrizione o categoria…" className="flex-1 sm:max-w-md" />
        <select data-testid="prod-filter-cat" value={cat} onChange={e => setCat(e.target.value)} className="border rounded-md px-3 py-2 text-sm">
          <option value="">Tutte le categorie</option>
          {PRODUCT_CATEGORIES.map(c => <option key={c}>{c}</option>)}
        </select>
        <span className="text-sm text-slate-500 self-center">{rows.length} risultati</span>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <Th sortKey="codice" currentKey={sortKey} dir={sortDir} onClick={toggle}>Codice</Th>
                <Th sortKey="descrizione" currentKey={sortKey} dir={sortDir} onClick={toggle}>Descrizione</Th>
                <Th sortKey="categoria" currentKey={sortKey} dir={sortDir} onClick={toggle}>Categoria</Th>
                <Th sortKey="categoria_adm" currentKey={sortKey} dir={sortDir} onClick={toggle}>Categoria ADM</Th>
                <Th sortKey="prezzo" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Prezzo</Th>
                <Th sortKey="giacenza_negozio" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Giac. negozio</Th>
                <Th sortKey="giacenza_vending" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Giac. vending</Th>
                <th className="text-right">Venduti</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={9} className="text-center py-8 text-slate-400">Caricamento…</td></tr>}
              {rows.map(r => (
                <tr key={r.id} data-testid={`prod-row-${r.codice}`}>
                  <td className="font-mono">{r.codice}</td>
                  <td className="max-w-sm truncate">{r.descrizione}</td>
                  <td><Badge tone="info">{r.categoria}</Badge></td>
                  <td>{r.categoria_adm ? <Badge tone="ok">{r.categoria_adm}</Badge> : <span className="text-slate-400">—</span>}</td>
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
