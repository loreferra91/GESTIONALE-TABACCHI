import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api, apiErrorMessage } from "../lib/api";
import { Card, KpiCard, formatNum } from "../components/UI";
import { SearchBar, Th, useSortSearch } from "../lib/tableHooks";
import { toast } from "sonner";
import { CheckCircle, Trash } from "@phosphor-icons/react";
import ConfirmDialog from "../components/ConfirmDialog";

export default function SmartVenue() {
  const [allRows, setAllRows] = useState([]);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [insertionDrafts, setInsertionDrafts] = useState({});
  const [confirmingId, setConfirmingId] = useState(null);

  useEffect(() => {
    api.get("/smart-venue")
      .then(response => setAllRows(response.data || []))
      .catch(err => toast.error(apiErrorMessage(err, "Impossibile caricare SMARTV VENUE")));
  }, []);

  const { rows, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(allRows, {
    initial: "codice",
    dir: "asc",
    searchFields: ["codice", "descrizione"],
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

  return (
    <Layout title="SMARTV VENUE" subtitle="dati Smart Venue da Excel e rimanenze correnti dei prodotti">
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
          placeholder="Cerca codice o descrizione…"
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
                  <td colSpan="8" className="py-10 text-center text-slate-500">
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
