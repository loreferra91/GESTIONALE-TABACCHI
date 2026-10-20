import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api, apiErrorMessage } from "../lib/api";
import { Badge, Card, formatEur, formatNum } from "../components/UI";
import { useSortSearch, Th, SearchBar } from "../lib/tableHooks";
import { toast } from "sonner";

export default function Ordini() {
  const [allRows, setAllRows] = useState([]);
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = useState({ data: today, codice: "", descrizione: "", quantita: 10, prezzo: 0 });
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [smartStatus, setSmartStatus] = useState("");

  const { rows, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(allRows, {
    initial: "data", dir: "desc",
    searchFields: ["codice", "descrizione", "file_sorgente"],
  });

  const load = async () => {
    try { const r = await api.get("/ordini"); setAllRows(r.data); }
    catch (error) { toast.error(apiErrorMessage(error, "Impossibile caricare gli ordini")); }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    if (!form.codice || !form.quantita) return toast.error("Codice e quantità richiesti");
    setSaving(true);
    try { await api.post("/ordini", form); toast.success("Ordine registrato"); setForm({ ...form, codice: "", descrizione: "", quantita: 10, prezzo: 0 }); await load(); }
    catch (error) { toast.error(apiErrorMessage(error, "Impossibile registrare il carico")); }
    finally { setSaving(false); }
  };

  const del = async (id) => {
    if (!window.confirm("Eliminare?")) return;
    setDeleting(id);
    try { await api.delete(`/ordini/${id}`); await load(); }
    catch (error) { toast.error(apiErrorMessage(error, "Impossibile annullare il carico")); }
    finally { setDeleting(null); }
  };

  const filteredRows = smartStatus ? rows.filter(row => (smartStatus === "CONFERMATO" ? row.caricato_smart_venue === true : row.smart_venue_stato === smartStatus || (!row.smart_venue_stato && (smartStatus === "NON_APPLICABILE" ? !row.smart_venue_applicabile : false)))) : rows;
  const totQ = filteredRows.reduce((s, r) => s + (r.quantita || 0), 0);
  const totV = filteredRows.reduce((s, r) => s + (r.quantita || 0) * (r.prezzo || 0), 0);

  return (
    <Layout title="Storico ordini" subtitle={`${allRows.length} righe totali`}>
      <Card className="p-4 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-6 gap-3">
          <input data-testid="ord-data" type="date" value={form.data} onChange={e => setForm({...form, data: e.target.value})} className="border rounded-md px-3 py-2 text-sm" />
          <input data-testid="ord-codice" placeholder="Codice" value={form.codice} onChange={e => setForm({...form, codice: e.target.value})} className="border rounded-md px-3 py-2 text-sm font-mono" />
          <input data-testid="ord-desc" placeholder="Descrizione" value={form.descrizione} onChange={e => setForm({...form, descrizione: e.target.value})} className="border rounded-md px-3 py-2 text-sm md:col-span-2" />
          <input data-testid="ord-qta" type="number" placeholder="Qta" value={form.quantita} onChange={e => setForm({...form, quantita: parseInt(e.target.value) || 0})} className="border rounded-md px-3 py-2 text-sm font-mono" />
          <div className="flex gap-2">
            <input data-testid="ord-prezzo" type="number" step="0.01" placeholder="Prezzo" value={form.prezzo} onChange={e => setForm({...form, prezzo: parseFloat(e.target.value) || 0})} className="border rounded-md px-3 py-2 text-sm font-mono flex-1" />
            <button data-testid="ord-save-btn" onClick={save} disabled={saving} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors disabled:opacity-40">{saving ? "…" : "+"}</button>
          </div>
        </div>
      </Card>

      <div className="flex items-center justify-between gap-3 mb-4">
        <SearchBar data-testid="ord-search" value={query} onChange={setQuery} placeholder="Cerca codice, descrizione o sorgente…" className="flex-1 max-w-md" />
        <select data-testid="ord-smart-status" value={smartStatus} onChange={event => setSmartStatus(event.target.value)} className="rounded-md border px-2 py-2 text-sm"><option value="">Tutti gli stati Smart Venue</option><option value="NON_APPLICABILE">Non applicabile</option><option value="DA_INSERIRE">Da inserire in Smart Venue</option><option value="CONFERMATO">Confermato in Smart Venue</option><option value="ANNULLATO">Annullato</option></select>
        <div className="text-sm text-slate-600 whitespace-nowrap">Totale filtrato: <span className="font-heading font-black text-lg text-slate-900 ml-2">{formatEur(totV)}</span> · {formatNum(totQ)} pz</div>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto max-h-[70vh]">
          <table className="data-table w-full">
            <thead>
              <tr>
                <Th sortKey="data" currentKey={sortKey} dir={sortDir} onClick={toggle}>Data</Th>
                <Th sortKey="codice" currentKey={sortKey} dir={sortDir} onClick={toggle}>Codice</Th>
                <Th sortKey="descrizione" currentKey={sortKey} dir={sortDir} onClick={toggle}>Descrizione</Th>
                <Th sortKey="quantita" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Qta</Th>
                <Th sortKey="prezzo" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Prezzo</Th>
                <th className="text-right">Totale</th>
                <Th sortKey="file_sorgente" currentKey={sortKey} dir={sortDir} onClick={toggle}>Sorgente</Th>
                <th>Smart Venue</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map(r => (
                <tr key={r.id} data-testid={`ord-row-${r.id}`}>
                  <td className="font-mono text-xs">{(r.data || "").slice(0, 10)}</td>
                  <td className="font-mono">{r.codice}</td>
                  <td className="max-w-sm truncate">{r.descrizione}</td>
                  <td className="font-mono text-right">{r.quantita}</td>
                  <td className="font-mono text-right">{formatEur(r.prezzo)}</td>
                  <td className="font-mono text-right font-semibold">{formatEur(r.quantita * r.prezzo)}</td>
                  <td className="text-xs text-slate-500">{r.file_sorgente}</td>
                  <td><Badge tone={r.caricato_smart_venue ? "ok" : r.smart_venue_applicabile ? "warning" : "default"}>{r.annullato ? "Annullato" : r.caricato_smart_venue ? "Confermato in Smart Venue" : r.smart_venue_applicabile ? "Da inserire in Smart Venue" : "Non applicabile"}</Badge></td>
                  <td className="text-right"><button onClick={() => del(r.id)} disabled={deleting === r.id} className="text-red-600 text-xs disabled:opacity-40">✕</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </Layout>
  );
}
