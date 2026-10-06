import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api, apiErrorMessage } from "../lib/api";
import { Card, KpiCard, formatNum } from "../components/UI";
import { SearchBar, Th, useSortSearch } from "../lib/tableHooks";
import { toast } from "sonner";
import { CheckCircle, Plus, Trash, UploadSimple } from "@phosphor-icons/react";
import ConfirmDialog from "../components/ConfirmDialog";
import { PRODUCT_CATEGORIES } from "../lib/categories";

const EMPTY_PRODUCT = {
  codice: "",
  descrizione: "",
  barcode: "",
  categoria: "ACCESSORI",
  prezzo: "",
  acquistati: "",
  giacenza_negozio: "",
  giacenza_vending: "",
  smart_venue: "",
};

export default function SmartVenue() {
  const [allRows, setAllRows] = useState([]);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [insertionDrafts, setInsertionDrafts] = useState({});
  const [confirmingId, setConfirmingId] = useState(null);
  const [productForm, setProductForm] = useState(EMPTY_PRODUCT);
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);

  const load = () => api.get("/smart-venue")
      .then(response => setAllRows(response.data || []))
      .catch(err => toast.error(apiErrorMessage(err, "Impossibile caricare SMARTV VENUE")));

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const { rows, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(allRows, {
    initial: "codice",
    dir: "asc",
    searchFields: ["codice", "descrizione", "barcode"],
  });

  const purchased = allRows.reduce((total, row) => total + (row.acquistati || 0), 0);
  const stock = allRows.reduce((total, row) => total + (row.rimanenze || 0), 0);

  const applySmartVenueResult = result => {
    setAllRows(current => current.map(row => row.id === result.id ? {
      ...row,
      smart_venue: result.smart_venue,
      rimanenze: result.rimanenze,
      differenza: result.differenza,
    } : row));
    setInsertionDrafts(current => {
      const next = { ...current };
      delete next[result.id];
      return next;
    });
  };

  const confirmDifference = async row => {
    const draft = insertionDrafts[row.id];
    const quantity = draft === undefined || draft === "" ? null : Number(draft);
    if (quantity !== null && (!Number.isInteger(quantity) || quantity < 0)) {
      toast.error("Inserimento Smart Venue deve essere un numero intero non negativo");
      return;
    }
    if (quantity !== null && quantity > row.smart_venue) {
      toast.error("Inserimento Smart Venue superiore alla quantità disponibile");
      return;
    }
    setConfirmingId(row.id);
    try {
      const response = await api.post(
        `/smart-venue/${encodeURIComponent(row.id)}/conferma`,
        quantity === null ? {} : { inserimento_smart_venue: quantity },
      );
      applySmartVenueResult(response.data);
      toast.success(`${response.data.inserimento_smart_venue} sottratti da Smart Venue per ${row.codice}`);
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile confermare la differenza"));
    } finally {
      setConfirmingId(null);
    }
  };

  const deleteRow = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.delete(`/smart-venue/${encodeURIComponent(deleteTarget.id)}`);
      setAllRows(current => current.filter(row => row.id !== deleteTarget.id));
      toast.success(`Riga ${deleteTarget.codice} eliminata da SMARTV VENUE`);
      setDeleteTarget(null);
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile eliminare la riga"));
    } finally {
      setDeleting(false);
    }
  };

  const addProduct = async event => {
    event.preventDefault();
    if (!productForm.codice.trim() || !productForm.descrizione.trim()) {
      toast.error("Codice e descrizione sono obbligatori");
      return;
    }
    setCreating(true);
    try {
      const payload = {
        ...productForm,
        prezzo: Number(productForm.prezzo || 0),
        acquistati: Number(productForm.acquistati || 0),
        giacenza_negozio: Number(productForm.giacenza_negozio || 0),
        giacenza_vending: Number(productForm.giacenza_vending || 0),
        smart_venue: Number(productForm.smart_venue || 0),
      };
      const response = await api.post("/smart-venue", payload);
      setAllRows(current => [...current, response.data]);
      setProductForm(EMPTY_PRODUCT);
      toast.success(`Prodotto ${response.data.codice} aggiunto a SMARTV VENUE`);
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile aggiungere il prodotto"));
    } finally {
      setCreating(false);
    }
  };

  const importSmartVenue = async event => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setImporting(true);
    try {
      const data = new FormData();
      data.append("file", file);
      const response = await api.post("/smart-venue/import-excel", data);
      await load();
      toast.success(`${response.data.totali.smart_venue_righe} righe aggiornate dalle colonne E/F`);
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile importare SMART VENUE"));
    } finally {
      setImporting(false);
    }
  };

  return (
    <Layout title="SMARTV VENUE" subtitle="dati Smart Venue da Excel e rimanenze correnti dei prodotti">
      <Card className="mb-6 p-4">
        <form onSubmit={addProduct}>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="font-bold text-slate-900">Aggiungi prodotto</h2>
              <p className="text-xs text-slate-500">Il prodotto viene creato anche nell’anagrafica generale.</p>
            </div>
            <label className={`inline-flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium ${importing ? "pointer-events-none opacity-50" : "hover:bg-slate-50"}`}>
              <UploadSimple size={17} /> {importing ? "Importazione…" : "Importa colonne E/F da Excel"}
              <input data-testid="smart-venue-import" type="file" accept=".xlsx,.xlsm" className="hidden" onChange={importSmartVenue} disabled={importing} />
            </label>
          </div>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <input data-testid="smart-venue-new-code" required placeholder="Codice" value={productForm.codice} onChange={event => setProductForm(current => ({ ...current, codice: event.target.value }))} className="rounded-md border px-3 py-2 text-sm font-mono" />
            <input data-testid="smart-venue-new-description" required placeholder="Descrizione" value={productForm.descrizione} onChange={event => setProductForm(current => ({ ...current, descrizione: event.target.value }))} className="rounded-md border px-3 py-2 text-sm lg:col-span-2" />
            <input data-testid="smart-venue-new-barcode" type="text" inputMode="text" placeholder="Barcode" value={productForm.barcode} onChange={event => setProductForm(current => ({ ...current, barcode: event.target.value }))} className="rounded-md border px-3 py-2 text-sm font-mono" />
            <select data-testid="smart-venue-new-category" value={productForm.categoria} onChange={event => setProductForm(current => ({ ...current, categoria: event.target.value }))} className="rounded-md border px-3 py-2 text-sm">
              {PRODUCT_CATEGORIES.map(category => <option key={category} value={category}>{category}</option>)}
            </select>
            <input type="number" min="0" step="0.01" placeholder="Prezzo" value={productForm.prezzo} onChange={event => setProductForm(current => ({ ...current, prezzo: event.target.value }))} className="rounded-md border px-3 py-2 text-sm font-mono" />
            <input type="number" min="0" step="1" placeholder="Acquistati" value={productForm.acquistati} onChange={event => setProductForm(current => ({ ...current, acquistati: event.target.value }))} className="rounded-md border px-3 py-2 text-sm font-mono" />
            <input type="number" min="0" step="1" placeholder="Giacenza negozio" value={productForm.giacenza_negozio} onChange={event => setProductForm(current => ({ ...current, giacenza_negozio: event.target.value }))} className="rounded-md border px-3 py-2 text-sm font-mono" />
            <input type="number" min="0" step="1" placeholder="Giacenza vending" value={productForm.giacenza_vending} onChange={event => setProductForm(current => ({ ...current, giacenza_vending: event.target.value }))} className="rounded-md border px-3 py-2 text-sm font-mono" />
            <input data-testid="smart-venue-new-quantity" type="number" min="0" step="1" placeholder="Smart Venue" value={productForm.smart_venue} onChange={event => setProductForm(current => ({ ...current, smart_venue: event.target.value }))} className="rounded-md border px-3 py-2 text-sm font-mono" />
            <button data-testid="smart-venue-add" type="submit" disabled={creating} className="inline-flex items-center justify-center gap-2 rounded-md bg-slate-900 px-4 py-2 text-sm font-bold text-white hover:bg-slate-800 disabled:opacity-50">
              <Plus size={17} weight="bold" /> {creating ? "Aggiunta…" : "Aggiungi prodotto"}
            </button>
          </div>
        </form>
      </Card>
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-3">
        <KpiCard label="Prodotti" value={allRows.length} />
        <KpiCard label="Acquistati" value={purchased} />
        <KpiCard label="Rimanenze" value={stock} />
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <SearchBar
          data-testid="smart-venue-search"
          value={query}
          onChange={setQuery}
          placeholder="Cerca codice, descrizione o barcode…"
          className="max-w-lg flex-1"
        />
        <span className="text-sm text-slate-500">{rows.length} risultati</span>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <Th sortKey="codice" currentKey={sortKey} dir={sortDir} onClick={toggle}>Codice</Th>
                <Th sortKey="descrizione" currentKey={sortKey} dir={sortDir} onClick={toggle}>Descrizione</Th>
                <Th sortKey="acquistati" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Acquistati</Th>
                <Th sortKey="rimanenze" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Rimanenze</Th>
                <Th sortKey="smart_venue" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Smart Venue</Th>
                <th className="text-right">Inserimento Smart Venue</th>
                <Th sortKey="barcode" currentKey={sortKey} dir={sortDir} onClick={toggle}>Barcode</Th>
                <Th sortKey="differenza" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Differenza</Th>
                <th aria-label="Azioni"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.id} data-testid={`smart-venue-row-${row.codice}`}>
                  <td className="font-mono font-semibold">{row.codice}</td>
                  <td className="max-w-lg truncate">{row.descrizione}</td>
                  <td className="text-right font-mono">{formatNum(row.acquistati)}</td>
                  <td className="text-right font-mono font-bold">{formatNum(row.rimanenze)}</td>
                  <td className="text-right font-mono font-bold">{formatNum(row.smart_venue)}</td>
                  <td className="text-right">
                    <input
                      data-testid={`smart-venue-insertion-${row.codice}`}
                      aria-label={`Inserimento Smart Venue ${row.codice}`}
                      type="number"
                      inputMode="numeric"
                      min="0"
                      max={row.smart_venue}
                      step="1"
                      placeholder={String(Math.max(0, row.differenza))}
                      value={insertionDrafts[row.id] ?? ""}
                      onChange={event => setInsertionDrafts(current => ({ ...current, [row.id]: event.target.value }))}
                      disabled={confirmingId === row.id}
                      className="w-24 rounded-md border border-slate-300 px-2 py-1 text-right font-mono text-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200 disabled:bg-slate-100"
                    />
                  </td>
                  <td className="font-mono">{row.barcode || "—"}</td>
                  <td className={`text-right font-mono font-bold ${row.differenza > 0 ? "text-emerald-700" : row.differenza < 0 ? "text-red-700" : "text-slate-400"}`}>
                    {row.differenza > 0 ? "+" : ""}{formatNum(row.differenza)}
                  </td>
                  <td className="whitespace-nowrap text-right">
                    <button
                      data-testid={`smart-venue-confirm-${row.codice}`}
                      onClick={() => confirmDifference(row)}
                      disabled={confirmingId === row.id || ((insertionDrafts[row.id] === undefined || insertionDrafts[row.id] === "") && row.differenza <= 0)}
                      className="mr-1 inline-flex items-center gap-1 rounded-md bg-emerald-600 px-2 py-1.5 text-xs font-bold text-white hover:bg-emerald-700 disabled:cursor-default disabled:opacity-35"
                      title={`Conferma differenza ${row.codice}`}
                    >
                      <CheckCircle size={14} weight="bold" /> Conferma
                    </button>
                    <button
                      data-testid={`smart-venue-delete-${row.codice}`}
                      onClick={() => setDeleteTarget(row)}
                      className="rounded-md p-2 text-red-600 hover:bg-red-50 hover:text-red-800"
                      title={`Elimina ${row.codice}`}
                      aria-label={`Elimina riga ${row.codice}`}
                    >
                      <Trash size={16} weight="bold" />
                    </button>
                  </td>
                </tr>
              ))}
              {!rows.length && (
                <tr>
                  <td colSpan="9" className="py-10 text-center text-slate-500">
                    Nessun prodotto disponibile nel gestionale.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="Eliminare questa riga?"
        confirmLabel="Elimina riga"
        danger
        loading={deleting}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={deleteRow}
      >
        <p>Verrà eliminata la riga <b>{deleteTarget?.codice}</b> — {deleteTarget?.descrizione}.</p>
        <p className="mt-2 text-slate-500">Il prodotto resta nel gestionale: viene nascosto soltanto da questa pagina.</p>
      </ConfirmDialog>
    </Layout>
  );
}
