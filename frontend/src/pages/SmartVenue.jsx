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
  smart_venue: "",
};

const operationKey = (rowId, kind) => {
  const suffix = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${rowId}-${kind}-${suffix}`;
};

export default function SmartVenue() {
  const [allRows, setAllRows] = useState([]);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [exitDrafts, setExitDrafts] = useState({});
  const [barcodeDrafts, setBarcodeDrafts] = useState({});
  const [confirmingId, setConfirmingId] = useState(null);
  const [outgoingTarget, setOutgoingTarget] = useState(null);
  const [entryTarget, setEntryTarget] = useState(null);
  const [entryDrafts, setEntryDrafts] = useState({});
  const [savingEntryId, setSavingEntryId] = useState(null);
  const [savingBarcodeId, setSavingBarcodeId] = useState(null);
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
  const pending = allRows.reduce((total, row) => total + (row.carichi_pendenti || 0), 0);

  const openExitDialog = row => {
    setExitDrafts(current => ({
      ...current,
      [row.id]: current[row.id] || {
        quantita: String(row.differenza),
        motivazione: "Uscita registrata in Smart Venue",
        idempotency_key: operationKey(row.id, "exit"),
      },
    }));
    setOutgoingTarget(row);
  };

  const openEntryDialog = row => {
    setEntryDrafts(current => ({
      ...current,
      [row.id]: current[row.id] || {
        quantita: String(Math.abs(row.differenza)),
        motivazione: row.rettifica_motivazione || "Riconciliazione saldo Smart Venue",
        idempotency_key: operationKey(row.id, "entry"),
      },
    }));
    setEntryTarget(row);
  };

  const confirmDifference = async row => {
    const draft = exitDrafts[row.id] || {};
    const quantity = Number(draft.quantita);
    if (!Number.isInteger(quantity) || quantity <= 0 || quantity > row.differenza) {
      toast.error("La quantità di uscita deve essere positiva e non superiore alla differenza");
      return;
    }
    if (!String(draft.motivazione || "").trim()) {
      toast.error("La motivazione è obbligatoria");
      return;
    }
    setConfirmingId(row.id);
    try {
      const response = await api.post(
        `/smart-venue/${encodeURIComponent(row.id)}/conferma-uscita`,
        {
          codice: row.codice,
          quantita: quantity,
          motivazione: draft.motivazione.trim(),
          idempotency_key: draft.idempotency_key,
          saldo_atteso: row.smart_venue,
          differenza_attesa: row.differenza,
        },
      );
      await load();
      setExitDrafts(current => {
        const next = { ...current };
        delete next[row.id];
        return next;
      });
      setOutgoingTarget(null);
      toast.success(`Uscita di ${response.data.quantita} registrata in Smart Venue per ${row.codice}`);
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile confermare la differenza"));
    } finally {
      setConfirmingId(null);
    }
  };

  const confirmEntry = async row => {
    const draft = entryDrafts[row.id] || {};
    const quantity = Number(draft.quantita);
    if (!Number.isInteger(quantity) || quantity <= 0 || quantity > Math.abs(row.differenza)) {
      toast.error("La quantità di entrata deve essere intera e non superiore alla differenza negativa");
      return;
    }
    if (!String(draft.motivazione || "").trim()) {
      toast.error("La motivazione è obbligatoria");
      return;
    }
    setSavingEntryId(row.id);
    try {
      const response = await api.post(`/smart-venue/${encodeURIComponent(row.id)}/conferma-entrata`, {
        codice: row.codice,
        quantita: quantity,
        motivazione: draft.motivazione.trim(),
        idempotency_key: draft.idempotency_key,
        saldo_atteso: row.smart_venue,
        differenza_attesa: row.differenza,
        rettifica_id: row.rettifica_id || null,
      });
      await load();
      setEntryDrafts(current => {
        const next = { ...current };
        delete next[row.id];
        return next;
      });
      setEntryTarget(null);
      toast.success(`Entrata di ${quantity} registrata per ${row.codice}`);
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile confermare l'entrata"));
    } finally {
      setSavingEntryId(null);
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

  const saveBarcode = async row => {
    if (barcodeDrafts[row.id] === undefined) return;
    const barcode = barcodeDrafts[row.id].trim();
    if (barcode === (row.barcode || "")) {
      setBarcodeDrafts(current => {
        const next = { ...current };
        delete next[row.id];
        return next;
      });
      return;
    }
    setSavingBarcodeId(row.id);
    try {
      const response = await api.put(
        `/smart-venue/${encodeURIComponent(row.id)}/barcode`,
        { barcode },
      );
      setAllRows(current => current.map(item => item.id === row.id
        ? { ...item, barcode: response.data.barcode }
        : item));
      setBarcodeDrafts(current => {
        const next = { ...current };
        delete next[row.id];
        return next;
      });
      toast.success(`Barcode aggiornato per ${row.codice}`);
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile aggiornare il barcode"));
    } finally {
      setSavingBarcodeId(null);
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
    <Layout title="SMARTV VENUE" subtitle="saldo esterno confermato, fisico e operazioni in attesa">
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
            <input data-testid="smart-venue-new-quantity" type="number" min="0" step="1" placeholder="Smart Venue" value={productForm.smart_venue} onChange={event => setProductForm(current => ({ ...current, smart_venue: event.target.value }))} className="rounded-md border px-3 py-2 text-sm font-mono" />
            <button data-testid="smart-venue-add" type="submit" disabled={creating} className="inline-flex items-center justify-center gap-2 rounded-md bg-slate-900 px-4 py-2 text-sm font-bold text-white hover:bg-slate-800 disabled:opacity-50">
              <Plus size={17} weight="bold" /> {creating ? "Aggiunta…" : "Aggiungi prodotto"}
            </button>
          </div>
        </form>
      </Card>
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard label="Prodotti" value={allRows.length} />
        <KpiCard label="Acquistati" value={purchased} />
        <KpiCard label="Fisico totale" value={stock} />
        <KpiCard label="Carichi da inserire" value={pending} tone={pending ? "warning" : "default"} />
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
                <Th sortKey="giacenza_negozio" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Negozio</Th>
                <Th sortKey="giacenza_vending" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Vending</Th>
                <Th sortKey="rimanenze" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Fisico</Th>
                <Th sortKey="smart_venue" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Saldo SV</Th>
                <th className="text-right">Carichi pendenti</th>
                <th className="text-right">Quantità uscita</th>
                <Th sortKey="barcode" currentKey={sortKey} dir={sortDir} onClick={toggle}>Barcode</Th>
                <th>Stato</th>
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
                  <td className="text-right font-mono">{formatNum(row.giacenza_negozio)}</td>
                  <td className="text-right font-mono">{formatNum(row.giacenza_vending)}</td>
                  <td className="text-right font-mono font-bold">{formatNum(row.rimanenze)}</td>
                  <td className="text-right font-mono font-bold">{formatNum(row.smart_venue)}</td>
                  <td className="text-right font-mono">{formatNum(row.carichi_pendenti)}</td>
                  <td className="text-right">
                    {row.differenza > 0 ? <input
                      data-testid={`smart-venue-insertion-${row.codice}`}
                      aria-label={`Uscita da registrare ${row.codice}`}
                      type="number"
                      inputMode="numeric"
                      min="0"
                      max={row.smart_venue}
                      step="1"
                      placeholder={String(row.differenza)}
                      value={exitDrafts[row.id]?.quantita ?? ""}
                      onChange={event => setExitDrafts(current => ({
                        ...current,
                        [row.id]: {
                          quantita: event.target.value,
                          motivazione: current[row.id]?.motivazione || "Uscita registrata in Smart Venue",
                          idempotency_key: current[row.id]?.idempotency_key || operationKey(row.id, "exit"),
                        },
                      }))}
                      disabled={confirmingId === row.id}
                      className="w-24 rounded-md border border-slate-300 px-2 py-1 text-right font-mono text-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200 disabled:bg-slate-100"
                    /> : <span className="text-xs text-slate-400">Non applicabile</span>}
                  </td>
                  <td>
                    <input
                      data-testid={`smart-venue-barcode-${row.codice}`}
                      aria-label={`Barcode ${row.codice}`}
                      type="text"
                      inputMode="text"
                      maxLength={100}
                      placeholder="Barcode"
                      value={barcodeDrafts[row.id] ?? row.barcode ?? ""}
                      onChange={event => setBarcodeDrafts(current => ({ ...current, [row.id]: event.target.value }))}
                      onBlur={() => saveBarcode(row)}
                      onKeyDown={event => {
                        if (event.key === "Enter") event.currentTarget.blur();
                      }}
                      disabled={savingBarcodeId === row.id}
                      className="w-32 rounded-md border border-slate-300 px-2 py-1 font-mono text-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200 disabled:bg-slate-100"
                    />
                  </td>
                  <td><span className={`text-xs font-semibold ${row.stato === "ANOMALIA VENDING" ? "text-red-700" : row.stato === "ALLINEATO" ? "text-slate-500" : "text-amber-700"}`}>{row.stato}</span></td>
                  <td className={`text-right font-mono font-bold ${row.differenza > 0 ? "text-emerald-700" : row.differenza < 0 ? "text-red-700" : "text-slate-400"}`}>
                    {row.differenza > 0 ? "+" : ""}{formatNum(row.differenza)}
                  </td>
                  <td className="whitespace-nowrap text-right">
                    {row.stato === "USCITA DA CONFERMARE" && <button
                      data-testid={`smart-venue-confirm-${row.codice}`}
                      onClick={() => openExitDialog(row)}
                      disabled={confirmingId === row.id || row.differenza <= 0}
                      className="mr-1 inline-flex items-center gap-1 rounded-md bg-emerald-600 px-2 py-1.5 text-xs font-bold text-white hover:bg-emerald-700 disabled:cursor-default disabled:opacity-35"
                      title={`Conferma uscita registrata in Smart Venue per ${row.codice}`}
                    >
                      <CheckCircle size={14} weight="bold" /> Conferma uscita
                    </button>}
                    {row.differenza < 0 && (row.stato === "ENTRATA DA VERIFICARE" || row.stato === "RETTIFICA DA VERIFICARE") && (
                      <button
                        data-testid={`smart-venue-entry-${row.codice}`}
                        onClick={() => openEntryDialog(row)}
                        disabled={savingEntryId === row.id}
                        className="mr-1 rounded-md bg-blue-600 px-2 py-1.5 text-xs font-bold text-white hover:bg-blue-700 disabled:opacity-40"
                      >Conferma entrata</button>
                    )}
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
                  <td colSpan="13" className="py-10 text-center text-slate-500">
                    Nessun prodotto disponibile nel gestionale.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
      <ConfirmDialog
        open={Boolean(outgoingTarget)}
        title="Confermare uscita registrata in Smart Venue?"
        confirmLabel="Conferma uscita esterna"
        loading={confirmingId === outgoingTarget?.id}
        onCancel={() => {
          if (outgoingTarget) setExitDrafts(current => {
            const next = { ...current };
            delete next[outgoingTarget.id];
            return next;
          });
          setOutgoingTarget(null);
        }}
        onConfirm={() => confirmDifference(outgoingTarget)}
      >
        <p><b>{outgoingTarget?.codice}</b> — {outgoingTarget?.descrizione}</p>
        <div className="mt-2 grid grid-cols-2 gap-2 text-sm text-slate-600">
          <p>Fisico totale: <b>{formatNum(outgoingTarget?.rimanenze || 0)}</b></p>
          <p>Saldo SV precedente: <b>{formatNum(outgoingTarget?.smart_venue || 0)}</b></p>
          <p>Differenza precedente: <b>{formatNum(outgoingTarget?.differenza || 0)}</b></p>
          <p>Saldo SV successivo: <b data-testid="smart-venue-exit-next-balance">{formatNum(Math.max(0, (outgoingTarget?.smart_venue || 0) - Number(exitDrafts[outgoingTarget?.id]?.quantita || 0)))}</b></p>
          <p>Differenza successiva: <b data-testid="smart-venue-exit-next-difference">{formatNum((outgoingTarget?.differenza || 0) - Number(exitDrafts[outgoingTarget?.id]?.quantita || 0))}</b></p>
        </div>
        <div className="mt-3 grid gap-2">
          <label className="text-sm font-semibold">Quantità uscita
            <input data-testid="smart-venue-exit-quantity" type="number" min="1" max={outgoingTarget?.differenza || 0} step="1" className="mt-1 w-full rounded-md border p-2" value={exitDrafts[outgoingTarget?.id]?.quantita || ""} onChange={event => setExitDrafts(current => ({ ...current, [outgoingTarget.id]: { ...current[outgoingTarget.id], quantita: event.target.value } }))} />
          </label>
          <label className="text-sm font-semibold">Motivazione
            <textarea data-testid="smart-venue-exit-reason" className="mt-1 w-full rounded-md border p-2" value={exitDrafts[outgoingTarget?.id]?.motivazione || ""} onChange={event => setExitDrafts(current => ({ ...current, [outgoingTarget.id]: { ...current[outgoingTarget.id], motivazione: event.target.value } }))} />
          </label>
        </div>
      </ConfirmDialog>
      <ConfirmDialog
        open={Boolean(entryTarget)}
        title="Confermare entrata/rettifica Smart Venue?"
        confirmLabel="Conferma entrata"
        loading={savingEntryId === entryTarget?.id}
        onCancel={() => {
          if (entryTarget) setEntryDrafts(current => {
            const next = { ...current };
            delete next[entryTarget.id];
            return next;
          });
          setEntryTarget(null);
        }}
        onConfirm={() => confirmEntry(entryTarget)}
      >
        <p><b>{entryTarget?.codice}</b> — {entryTarget?.descrizione}</p>
        <div className="mt-2 grid grid-cols-2 gap-2 text-sm text-slate-600">
          <p>Negozio: <b>{formatNum(entryTarget?.giacenza_negozio || 0)}</b></p>
          <p>Vending: <b>{formatNum(entryTarget?.giacenza_vending || 0)}</b></p>
          <p>Fisico totale: <b>{formatNum(entryTarget?.rimanenze || 0)}</b></p>
          <p>Saldo SV precedente: <b>{formatNum(entryTarget?.smart_venue || 0)}</b></p>
          <p>Differenza precedente: <b>{formatNum(entryTarget?.differenza || 0)}</b></p>
          <p>Saldo SV successivo: <b data-testid="smart-venue-entry-next-balance">{formatNum((entryTarget?.smart_venue || 0) + Number(entryDrafts[entryTarget?.id]?.quantita || 0))}</b></p>
          <p>Differenza successiva: <b data-testid="smart-venue-entry-next-difference">{formatNum((entryTarget?.differenza || 0) + Number(entryDrafts[entryTarget?.id]?.quantita || 0))}</b></p>
        </div>
        <div className="mt-3 grid gap-2">
          <label className="text-sm font-semibold">Quantità
            <input data-testid="smart-venue-entry-quantity" type="number" min="1" max={Math.abs(entryTarget?.differenza || 0)} step="1" className="mt-1 w-full rounded-md border p-2" value={entryDrafts[entryTarget?.id]?.quantita || ""} onChange={event => setEntryDrafts(current => ({ ...current, [entryTarget.id]: { ...current[entryTarget.id], quantita: event.target.value } }))} />
          </label>
          <label className="text-sm font-semibold">Motivazione
            <textarea data-testid="smart-venue-entry-reason" className="mt-1 w-full rounded-md border p-2" value={entryDrafts[entryTarget?.id]?.motivazione || ""} onChange={event => setEntryDrafts(current => ({ ...current, [entryTarget.id]: { ...current[entryTarget.id], motivazione: event.target.value } }))} />
          </label>
        </div>
        {entryTarget?.rettifica_id && <p className="mt-2 text-xs text-amber-700">Rettifica collegata: <b>{entryTarget.rettifica_id}</b>{entryTarget.rettifica_motivazione ? ` — ${entryTarget.rettifica_motivazione}` : ""}</p>}
        <p className="mt-2 text-xs text-slate-500">Differenza attesa: {formatNum(entryTarget?.differenza || 0)}. Le giacenze fisiche non vengono modificate.</p>
      </ConfirmDialog>
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
