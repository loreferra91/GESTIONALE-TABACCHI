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
  const [vendutiEditor, setVendutiEditor] = useState(null);
  const [savingVenduti, setSavingVenduti] = useState(false);
  const [minimumEditor, setMinimumEditor] = useState(null);
  const [savingMinimum, setSavingMinimum] = useState(false);
  const [planningRows, setPlanningRows] = useState({});
  const [planningAvailable, setPlanningAvailable] = useState(null);
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
    setPlanningAvailable(null);
    const productsRequest = api.get("/prodotti", { params: { limit: 5000 } })
      .then(response => setAllRows(response.data))
      .catch(error => toast.error(apiErrorMessage(error, "Impossibile caricare i prodotti")))
      .finally(() => setLoading(false));
    const planningRequest = api.get("/auto-order", { timeout: 20000 })
      .then(response => {
        setPlanningRows(Object.fromEntries([...(response.data.righe || []), ...(response.data.esclusi || [])].map(row => [row.codice, row])));
        setPlanningAvailable(true);
      })
      .catch(() => {
        setPlanningRows({});
        setPlanningAvailable(false);
      });
    await Promise.all([productsRequest, planningRequest]);
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

  const startVendutiEdit = (row) => {
    if (!row.id) {
      toast.error("Questo prodotto non ha un identificativo valido e non può essere rettificato");
      return;
    }
    const vendutiNegozio = Number(row.venduti_negozio || 0);
    setVendutiEditor({
      id: row.id,
      value: String(vendutiNegozio),
      initialValue: vendutiNegozio,
      vendutiVending: Number(row.venduti_vending || 0),
    });
  };

  const saveVendutiNegozio = async () => {
    const nextValue = Number(vendutiEditor?.value);
    if (vendutiEditor?.value.trim() === "" || !Number.isInteger(nextValue) || nextValue < 0) {
      toast.error("Inserisci un numero intero maggiore o uguale a zero");
      return;
    }

    setSavingVenduti(true);
    try {
      const response = await api.patch(`/prodotti/${vendutiEditor.id}/venduti-negozio`, {
        venduti_negozio: nextValue,
        valore_atteso: vendutiEditor.initialValue,
      });
      setAllRows(current => current.map(row => (
        row.id === vendutiEditor.id ? { ...row, venduti_negozio: response.data.venduti_negozio } : row
      )));
      setVendutiEditor(null);
      toast.success("Venduti negozio e contabilità aggiornati");
    } catch (error) {
      toast.error(apiErrorMessage(error, "Errore aggiornamento venduti negozio"));
      if (error?.response?.status === 409) {
        setVendutiEditor(null);
        await load();
      }
    } finally {
      setSavingVenduti(false);
    }
  };

  const startMinimumEdit = row => setMinimumEditor({
    codice: row.codice,
    value: String(row.scorta_minima_negozio_auto_order || 0),
    version: row.auto_order_version || 0,
  });

  const saveMinimum = async () => {
    const value = Number(minimumEditor?.value);
    if (!minimumEditor?.value.trim() || !Number.isInteger(value) || value < 0 || value > 100000) {
      toast.error("Inserisci un numero intero tra 0 e 100000");
      return;
    }
    setSavingMinimum(true);
    try {
      await api.patch(`/auto-order/prodotti/${encodeURIComponent(minimumEditor.codice)}/scorta-minima`, {
        minimo: value, version: minimumEditor.version,
      });
      setMinimumEditor(null);
      await load();
      toast.success("Minimo operativo aggiornato; Auto-Order userà il nuovo valore");
    } catch (error) {
      toast.error(apiErrorMessage(error, "Impossibile aggiornare il minimo operativo"));
      if (error?.response?.status === 409) {
        setMinimumEditor(null);
        await load();
      }
    } finally {
      setSavingMinimum(false);
    }
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
      <p className="mb-3 text-xs text-slate-600">Soglia e target sono calcolati dalle vendite WebApp e dai giorni di copertura; Auto-Order considera anche le forniture future affidabili. Il minimo operativo del negozio è facoltativo: 0 lascia lavorare solo l’algoritmo. La giacenza vending resta separata.</p>
      {planningAvailable === false && !loading && <p className="mb-3 text-xs text-amber-700">Soglie calcolate temporaneamente non disponibili; i prodotti restano modificabili.</p>}

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
                <th className="text-right">Soglia totale calcolata</th>
                <th className="text-right">Target totale calcolato</th>
                <th className="text-right">Scorta minima negozio</th>
                <th className="text-right">Venduti</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={12} className="text-center py-8 text-slate-400">Caricamento…</td></tr>}
              {rows.map((r, index) => (
                <tr key={`${r.id || r.codice}-${index}`} data-testid={`prod-row-${r.codice}`}>
                  <td className="font-mono">{r.codice}</td>
                  <td className="max-w-sm truncate">{r.descrizione}</td>
                  <td><Badge tone="info">{r.categoria}</Badge></td>
                  <td>{r.categoria_adm ? <Badge tone="ok">{r.categoria_adm}</Badge> : <span className="text-slate-400">—</span>}</td>
                  <td className="font-mono text-right">{formatEur(r.prezzo)}</td>
                  <td className={`font-mono text-right ${r.giacenza_negozio < 0 ? 'text-red-600 font-bold' : ''}`}>{formatNum(r.giacenza_negozio)}</td>
                  <td className="font-mono text-right">{formatNum(r.giacenza_vending)}</td>
                  <td className="font-mono text-right">{planningRows[r.codice] ? formatNum(planningRows[r.codice].punto_riordino) : "—"}</td>
                  <td className="font-mono text-right">{planningRows[r.codice] ? formatNum(planningRows[r.codice].target_scorta) : "—"}</td>
                  <td className="text-right">
                    {planningAvailable === true && !planningRows[r.codice] ? <span className="text-slate-400">—</span> : minimumEditor?.codice === r.codice ? <div className="flex items-center justify-end gap-1">
                      <input data-testid={`prod-minimum-input-${r.codice}`} type="number" min="0" step="1" value={minimumEditor.value} onChange={event => setMinimumEditor(current => ({ ...current, value: event.target.value }))} onKeyDown={event => { if (event.key === "Enter") saveMinimum(); if (event.key === "Escape") setMinimumEditor(null); }} className="w-20 rounded border px-2 py-1 text-right font-mono text-sm" aria-label={`Minimo operativo negozio ${r.descrizione}`} />
                      <button type="button" onClick={saveMinimum} disabled={savingMinimum} className="rounded bg-slate-900 px-2 py-1 text-xs text-white disabled:opacity-50">Salva</button>
                      <button type="button" onClick={() => setMinimumEditor(null)} disabled={savingMinimum} className="rounded border px-2 py-1 text-xs">Annulla</button>
                    </div> : <button type="button" data-testid={`prod-minimum-edit-${r.codice}`} onClick={() => startMinimumEdit(r)} className="rounded px-2 py-1 font-mono hover:bg-slate-100" title="Modifica minimo operativo negozio">{formatNum(r.scorta_minima_negozio_auto_order || 0)} ✎</button>}
                  </td>
                  <td className="font-mono text-right">
                    {vendutiEditor !== null && vendutiEditor.id === r.id ? (
                      <div className="ml-auto w-56 rounded-lg border border-slate-200 bg-white p-3 text-left font-sans shadow-lg" data-testid={`prod-venduti-editor-${r.codice}`}>
                        <label className="block text-[11px] font-bold uppercase tracking-wide text-slate-500" htmlFor={`venduti-negozio-${r.id}`}>
                          Venduti negozio
                        </label>
                        <input
                          autoFocus
                          id={`venduti-negozio-${r.id}`}
                          data-testid={`prod-venduti-negozio-${r.codice}`}
                          type="number"
                          min="0"
                          step="1"
                          value={vendutiEditor.value}
                          onChange={event => setVendutiEditor(current => ({ ...current, value: event.target.value }))}
                          onKeyDown={event => {
                            if (event.key === "Enter") saveVendutiNegozio();
                            if (event.key === "Escape") setVendutiEditor(null);
                          }}
                          className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-right font-mono text-sm"
                        />
                        <div className="mt-2 flex justify-between text-xs text-slate-500">
                          <span>Venduti vending</span>
                          <span className="font-mono text-slate-700">{formatNum(vendutiEditor.vendutiVending)}</span>
                        </div>
                        <div className="mt-1 flex justify-between border-t border-slate-100 pt-1 text-xs font-semibold text-slate-700">
                          <span>Totale</span>
                          <span className="font-mono">{formatNum((Number(vendutiEditor.value) || 0) + vendutiEditor.vendutiVending)}</span>
                        </div>
                        <div className="mt-3 flex justify-end gap-2">
                          <button type="button" onClick={() => setVendutiEditor(null)} disabled={savingVenduti} className="rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-100">Annulla</button>
                          <button type="button" onClick={saveVendutiNegozio} disabled={savingVenduti} className="rounded bg-slate-900 px-2.5 py-1 text-xs font-medium text-white disabled:opacity-50">
                            {savingVenduti ? "Salvo…" : "Salva"}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        data-testid={`prod-venduti-edit-${r.codice}`}
                        onClick={() => startVendutiEdit(r)}
                        className="rounded px-2 py-1 font-mono hover:bg-slate-100 hover:text-slate-900"
                        title="Modifica venduti negozio"
                      >
                        {formatNum((r.venduti_negozio || 0) + (r.venduti_vending || 0))}
                      </button>
                    )}
                  </td>
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
