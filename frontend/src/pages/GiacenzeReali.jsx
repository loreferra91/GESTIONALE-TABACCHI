import { useEffect, useMemo, useState } from "react";
import { FloppyDisk, Printer, WarningCircle } from "@phosphor-icons/react";
import { toast } from "sonner";
import Layout from "../components/Layout";
import { Badge, Card, formatNum } from "../components/UI";
import { SearchBar } from "../lib/tableHooks";
import { api, apiErrorMessage } from "../lib/api";
import InventoryPrint from "../components/InventoryPrint";
import "./GiacenzeReali.css";

const integerDraft = value => /^\d+$/.test(String(value ?? "")) ? Number(value) : null;

export default function GiacenzeReali() {
  const [rows, setRows] = useState([]);
  const [history, setHistory] = useState([]);
  const [query, setQuery] = useState("");
  const [drafts, setDrafts] = useState({});
  const [reasons, setReasons] = useState({});
  const [saving, setSaving] = useState(null);
  const [printMode, setPrintMode] = useState(null);
  const [printedAt, setPrintedAt] = useState(null);

  const load = async () => {
    const [stockResponse, historyResponse] = await Promise.all([
      api.get("/giacenze-reali"),
      api.get("/giacenze-reali/rettifiche/storico", { params: { limit: 50 } }),
    ]);
    setRows(stockResponse.data || []);
    setHistory(historyResponse.data || []);
  };

  useEffect(() => {
    load().catch(error => toast.error(apiErrorMessage(error, "Impossibile caricare le giacenze reali")));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!printMode) return undefined;
    const clear = () => setPrintMode(null);
    window.addEventListener("afterprint", clear, { once: true });
    const timer = window.setTimeout(() => window.print(), 100);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("afterprint", clear);
    };
  }, [printMode]);

  const visibleRows = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("it-IT");
    if (!needle) return rows;
    return rows.filter(row => `${row.codice} ${row.descrizione}`.toLocaleLowerCase("it-IT").includes(needle));
  }, [query, rows]);

  const dirtyCodes = Object.keys(drafts);
  const hasUnsavedChanges = dirtyCodes.length > 0;
  const totalShop = rows.reduce((sum, row) => sum + row.giacenza_negozio, 0);
  const totalVending = rows.reduce((sum, row) => sum + row.giacenza_vending, 0);

  const displayedValue = (row, field) => drafts[row.codice]?.[field] ?? row[field];
  const updateDraft = (row, field, value) => {
    setDrafts(current => {
      const nextRow = { ...(current[row.codice] || {}), [field]: value };
      const shop = nextRow.giacenza_negozio ?? String(row.giacenza_negozio);
      const vending = nextRow.giacenza_vending ?? String(row.giacenza_vending);
      if (String(shop) === String(row.giacenza_negozio) && String(vending) === String(row.giacenza_vending)) {
        const next = { ...current };
        delete next[row.codice];
        return next;
      }
      return { ...current, [row.codice]: nextRow };
    });
  };

  const saveRow = async row => {
    const shop = integerDraft(displayedValue(row, "giacenza_negozio"));
    const vending = integerDraft(displayedValue(row, "giacenza_vending"));
    if (shop === null || vending === null) {
      toast.error("Le giacenze devono essere numeri interi non negativi");
      return false;
    }
    if (vending > row.capacita_vending && vending > row.giacenza_vending) {
      toast.error(`La vending di ${row.descrizione} può contenere al massimo ${row.capacita_vending} pezzi`);
      return false;
    }
    setSaving(row.codice);
    try {
      const response = await api.put(`/giacenze-reali/${encodeURIComponent(row.codice)}`, {
        giacenza_negozio: shop,
        giacenza_vending: vending,
        attesa_giacenza_negozio: row.giacenza_negozio,
        attesa_giacenza_vending: row.giacenza_vending,
        motivazione: reasons[row.codice] || "",
      });
      setRows(current => current.map(item => item.codice === row.codice ? { ...item, ...response.data } : item));
      setDrafts(current => {
        const next = { ...current };
        delete next[row.codice];
        return next;
      });
      setReasons(current => {
        const next = { ...current };
        delete next[row.codice];
        return next;
      });
      setHistory(current => [...(response.data.rettifiche || []).reverse(), ...current].slice(0, 50));
      toast.success(`Giacenze salvate per ${row.descrizione}`);
      return true;
    } catch (error) {
      toast.error(apiErrorMessage(error, "Impossibile salvare la rettifica"));
      return false;
    } finally {
      setSaving(null);
    }
  };

  const saveAll = async () => {
    for (const code of [...dirtyCodes]) {
      const row = rows.find(item => item.codice === code);
      if (row && !(await saveRow(row))) return;
    }
  };

  const startPrint = mode => {
    if (hasUnsavedChanges) {
      toast.error("Salva o annulla le modifiche prima di stampare");
      return;
    }
    setPrintedAt(new Date());
    setPrintMode(mode);
  };

  return (
    <Layout title="GIACENZE REALI" subtitle="fotografia fisica di negozio e vending">
      <div className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Card className="p-4"><div className="overline">Giacenza negozio</div><div className="kpi-value mt-2 text-2xl">{formatNum(totalShop)}</div></Card>
        <Card className="p-4"><div className="overline">Giacenza vending</div><div className="kpi-value mt-2 text-2xl">{formatNum(totalVending)}</div></Card>
        <Card className="p-4"><div className="overline">Giacenza totale</div><div className="kpi-value mt-2 text-2xl">{formatNum(totalShop + totalVending)}</div></Card>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <SearchBar value={query} onChange={setQuery} placeholder="Cerca prodotto…" className="min-w-64 flex-1" data-testid="real-stock-search" />
        {hasUnsavedChanges && <Badge tone="warning">{dirtyCodes.length} modifiche non salvate</Badge>}
        <button type="button" onClick={() => startPrint("blank")} className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-bold hover:bg-slate-50">
          <Printer size={17} /> STAMPA INVENTARIO IN BIANCO
        </button>
        <button type="button" onClick={() => startPrint("current")} className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-bold hover:bg-slate-50">
          <Printer size={17} /> STAMPA GIACENZE ATTUALI
        </button>
        <button type="button" onClick={saveAll} disabled={!hasUnsavedChanges || Boolean(saving)} className="inline-flex items-center gap-2 rounded-md bg-emerald-600 px-4 py-2 text-sm font-bold text-white hover:bg-emerald-700 disabled:opacity-40">
          <FloppyDisk size={17} /> Salva modifiche
        </button>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto max-h-[68vh]">
          <table className="data-table w-full min-w-[760px]">
            <thead><tr><th>Descrizione prodotto</th><th className="text-right">Giacenza negozio</th><th className="text-right">Giacenza vending</th><th className="text-right">Giacenza totale</th></tr></thead>
            <tbody>
              {visibleRows.map(row => {
                const shop = integerDraft(displayedValue(row, "giacenza_negozio"));
                const vending = integerDraft(displayedValue(row, "giacenza_vending"));
                const dirty = Boolean(drafts[row.codice]);
                return (
                  <tr key={row.codice} data-testid={`real-stock-row-${row.codice}`} className={dirty ? "bg-amber-50" : ""}>
                    <td>
                      <div className="font-semibold text-slate-900">{row.descrizione}</div>
                      <div className="font-mono text-xs text-slate-500">{row.codice}</div>
                      {dirty && <input value={reasons[row.codice] || ""} onChange={event => setReasons(current => ({ ...current, [row.codice]: event.target.value }))} maxLength={500} placeholder="Motivazione rettifica (facoltativa)" className="mt-2 w-full max-w-sm rounded border px-2 py-1 text-xs" />}
                    </td>
                    <td className="text-right"><input aria-label={`Giacenza negozio ${row.descrizione}`} type="number" min="0" step="1" value={displayedValue(row, "giacenza_negozio")} onChange={event => updateDraft(row, "giacenza_negozio", event.target.value)} className="w-24 rounded-md border px-2 py-1 text-right font-mono" /></td>
                    <td className="text-right"><input aria-label={`Giacenza vending ${row.descrizione}`} type="number" min="0" max={row.capacita_vending} step="1" value={displayedValue(row, "giacenza_vending")} onChange={event => updateDraft(row, "giacenza_vending", event.target.value)} disabled={row.colonne_vending === 0} title={row.colonne_vending ? `${row.colonne_vending} colonne · capacità ${row.capacita_vending}` : "Prodotto non presente in vending"} className="w-24 rounded-md border px-2 py-1 text-right font-mono disabled:bg-slate-100" /></td>
                    <td className="text-right">
                      <div className="font-mono text-base font-black">{shop === null || vending === null ? "—" : formatNum(shop + vending)}</div>
                      {dirty && <button type="button" onClick={() => saveRow(row)} disabled={saving === row.codice} className="mt-2 text-xs font-bold text-emerald-700 hover:text-emerald-900">{saving === row.codice ? "Salvataggio…" : "Salva riga"}</button>}
                    </td>
                  </tr>
                );
              })}
              {!visibleRows.length && <tr><td colSpan="4" className="py-10 text-center text-slate-500">Nessun prodotto trovato.</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>

      <Card className="mt-6 overflow-hidden">
        <div className="border-b border-slate-200 px-4 py-3"><h2 className="font-bold">Ultime rettifiche inventariali</h2></div>
        <div className="overflow-x-auto">
          <table className="data-table w-full min-w-[760px]">
            <thead><tr><th>Data</th><th>Prodotto</th><th>Ubicazione</th><th className="text-right">Prima</th><th className="text-right">Dopo</th><th className="text-right">Differenza</th><th>Motivazione</th></tr></thead>
            <tbody>
              {history.map(item => <tr key={item.id}><td className="whitespace-nowrap text-xs">{new Date(item.created_at).toLocaleString("it-IT")}</td><td>{item.descrizione}</td><td><Badge tone="info">{item.ubicazione}</Badge></td><td className="text-right font-mono">{item.quantita_precedente}</td><td className="text-right font-mono">{item.quantita_nuova}</td><td className={`text-right font-mono font-bold ${item.differenza < 0 ? "text-red-700" : "text-emerald-700"}`}>{item.differenza > 0 ? "+" : ""}{item.differenza}</td><td className="text-xs text-slate-500">{item.motivazione || "—"}</td></tr>)}
              {!history.length && <tr><td colSpan="7" className="py-8 text-center text-slate-400">Nessuna rettifica registrata.</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>

      {hasUnsavedChanges && <div className="fixed bottom-4 right-4 z-50 flex items-center gap-2 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm font-bold text-amber-900 shadow-lg"><WarningCircle size={18} /> Modifiche non salvate</div>}

      {printMode && <InventoryPrint mode={printMode} rows={rows} printedAt={printedAt} />}
    </Layout>
  );
}
