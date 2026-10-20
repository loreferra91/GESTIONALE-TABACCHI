import { useCallback, useEffect, useMemo, useState } from "react";
import { Archive, CaretDown, ClockCounterClockwise, FloppyDisk, Printer, WarningCircle, FunnelSimple, X, ArrowsDownUp, Package, Storefront } from "@phosphor-icons/react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import Layout from "../components/Layout";
import { Badge, Card, formatNum } from "../components/UI";
import { SearchBar } from "../lib/tableHooks";
import { api, apiErrorMessage } from "../lib/api";
import { shortCategory } from "../lib/categories";
import InventoryPrint from "../components/InventoryPrint";
import StockAnalysis from "../components/StockAnalysis";
import "./GiacenzeReali.css";

const integerDraft = value => /^\d+$/.test(String(value ?? "")) ? Number(value) : null;

const STORAGE_KEY = "giacenze_reali_prefs";

const SORT_OPTIONS = [
  { value: "nome_az", label: "Nome A → Z" },
  { value: "nome_za", label: "Nome Z → A" },
  { value: "cat_nome", label: "Categoria + Nome A → Z" },
  { value: "neg_asc", label: "Giacenza negozio ↑" },
  { value: "neg_desc", label: "Giacenza negozio ↓" },
  { value: "vend_asc", label: "Giacenza vending ↑" },
  { value: "vend_desc", label: "Giacenza vending ↓" },
  { value: "tot_asc", label: "Giacenza totale ↑" },
  { value: "tot_desc", label: "Giacenza totale ↓" },
];

const SENZA_CATEGORIA = "(senza categoria)";

function InventoryMetricCard({ icon: Icon, label, value, tone }) {
  const toneClasses = {
    amber: "bg-amber-50 text-amber-700",
    blue: "bg-blue-50 text-blue-700",
    slate: "bg-slate-100 text-slate-700",
  };
  return (
    <Card className="flex min-h-[104px] min-w-0 items-center gap-4 border-amber-100 p-4 lg:px-5">
      <div className={`flex h-[68px] w-[68px] shrink-0 items-center justify-center rounded-lg ${toneClasses[tone]}`}>
        <Icon size={30} weight="duotone" />
      </div>
      <div className="min-w-0">
        <div className="text-[17px] font-semibold text-slate-600">{label}</div>
        <div className="mt-1 truncate font-heading text-[30px] font-black leading-none tabular-nums text-slate-950">{value} <span className="text-base font-bold text-slate-400">pz</span></div>
      </div>
    </Card>
  );
}

function loadPrefs() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch { return null; }
}

function savePrefs(prefs) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs)); } catch { /* ignore */ }
}

function sortRows(list, sortValue) {
  const copy = [...list];
  const secondary = (a, b) => a.descrizione.localeCompare(b.descrizione, "it", { sensitivity: "base" }) || a.codice.localeCompare(b.codice, "it");
  switch (sortValue) {
    case "nome_az":
      copy.sort((a, b) => secondary(a, b));
      break;
    case "nome_za":
      copy.sort((a, b) => -secondary(a, b));
      break;
    case "cat_nome":
      copy.sort((a, b) => {
        const ca = (a.categoria || "").localeCompare(b.categoria || "", "it", { sensitivity: "base" });
        return ca !== 0 ? ca : secondary(a, b);
      });
      break;
    case "neg_asc":
      copy.sort((a, b) => (a.giacenza_negozio - b.giacenza_negozio) || secondary(a, b));
      break;
    case "neg_desc":
      copy.sort((a, b) => (b.giacenza_negozio - a.giacenza_negozio) || secondary(a, b));
      break;
    case "vend_asc":
      copy.sort((a, b) => (a.giacenza_vending - b.giacenza_vending) || secondary(a, b));
      break;
    case "vend_desc":
      copy.sort((a, b) => (b.giacenza_vending - a.giacenza_vending) || secondary(a, b));
      break;
    case "tot_asc":
      copy.sort((a, b) => (a.giacenza_totale - b.giacenza_totale) || secondary(a, b));
      break;
    case "tot_desc":
      copy.sort((a, b) => (b.giacenza_totale - a.giacenza_totale) || secondary(a, b));
      break;
    default:
      copy.sort((a, b) => secondary(a, b));
  }
  return copy;
}

export default function GiacenzeReali() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get("tab") === "analisi" ? "analisi" : "inventario";
  const [rows, setRows] = useState([]);
  const [history, setHistory] = useState([]);
  const [categories, setCategories] = useState([]);
  const [query, setQuery] = useState("");
  const [drafts, setDrafts] = useState({});
  const [reasons, setReasons] = useState({});
  const [saving, setSaving] = useState(null);
  const [printMode, setPrintMode] = useState(null);
  const [printedAt, setPrintedAt] = useState(null);
  const [printScope, setPrintScope] = useState(null); // "filtered" | "all"

  // Filter state
  const prefs = loadPrefs();
  const [filterMode, setFilterMode] = useState(prefs?.filterMode || "include"); // "include" | "exclude"
  const [selectedCats, setSelectedCats] = useState(new Set(prefs?.selectedCats || []));
  const [sortValue, setSortValue] = useState(prefs?.sortValue || "nome_az");
  const [showFilterPanel, setShowFilterPanel] = useState(false);

  // Persist preferences
  const persistPrefs = useCallback((mode, cats, sort) => {
    savePrefs({ filterMode: mode, selectedCats: [...cats], sortValue: sort });
  }, []);

  useEffect(() => {
    persistPrefs(filterMode, selectedCats, sortValue);
  }, [filterMode, selectedCats, sortValue, persistPrefs]);

  const load = async () => {
    const [stockResponse, historyResponse, catResponse] = await Promise.all([
      api.get("/giacenze-reali"),
      api.get("/giacenze-reali/rettifiche/storico", { params: { limit: 50 } }),
      api.get("/categorie-prodotti"),
    ]);
    setRows(stockResponse.data || []);
    setHistory(historyResponse.data || []);
    setCategories(catResponse.data || []);
  };

  useEffect(() => {
    load().catch(error => toast.error(apiErrorMessage(error, "Impossibile caricare le giacenze reali")));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!printMode) return undefined;
    const clear = () => { setPrintMode(null); setPrintScope(null); };
    window.addEventListener("afterprint", clear, { once: true });
    const timer = window.setTimeout(() => window.print(), 100);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("afterprint", clear);
    };
  }, [printMode]);

  // Detect products without a category
  const hasUncategorized = useMemo(() => rows.some(r => !r.categoria), [rows]);

  // All available category labels for the filter UI
  const availableCats = useMemo(() => {
    const set = [...categories];
    if (hasUncategorized && !set.includes(SENZA_CATEGORIA)) set.push(SENZA_CATEGORIA);
    return set;
  }, [categories, hasUncategorized]);

  // --- Filtering pipeline ---
  const filteredRows = useMemo(() => {
    let out = rows;

    // Category filter
    if (selectedCats.size > 0) {
      if (filterMode === "include") {
        out = out.filter(r => {
          const cat = r.categoria || "";
          if (!cat) return selectedCats.has(SENZA_CATEGORIA);
          return selectedCats.has(cat);
        });
      } else {
        out = out.filter(r => {
          const cat = r.categoria || "";
          if (!cat) return !selectedCats.has(SENZA_CATEGORIA);
          return !selectedCats.has(cat);
        });
      }
    }

    // Text search
    const needle = query.trim().toLocaleLowerCase("it-IT");
    if (needle) {
      out = out.filter(row => `${row.codice} ${row.descrizione}`.toLocaleLowerCase("it-IT").includes(needle));
    }

    return out;
  }, [rows, selectedCats, filterMode, query]);

  const visibleRows = useMemo(() => sortRows(filteredRows, sortValue), [filteredRows, sortValue]);

  // Rows used for printing
  const printRows = useMemo(() => {
    if (printScope === "all") return sortRows(rows, sortValue);
    return visibleRows;
  }, [printScope, rows, visibleRows, sortValue]);

  const dirtyCodes = Object.keys(drafts);
  const hasUnsavedChanges = dirtyCodes.length > 0;
  const totalShop = rows.reduce((sum, row) => sum + row.giacenza_negozio, 0);
  const totalVending = rows.reduce((sum, row) => sum + row.giacenza_vending, 0);

  const hasActiveFilters = selectedCats.size > 0 || query.trim().length > 0;

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

  const startPrint = (mode, scope) => {
    if (hasUnsavedChanges) {
      toast.error("Salva o annulla le modifiche prima di stampare");
      return;
    }
    setPrintedAt(new Date());
    setPrintScope(scope);
    setPrintMode(mode);
  };

  const toggleCat = cat => {
    setSelectedCats(current => {
      const next = new Set(current);
      if (next.has(cat)) next.delete(cat); else next.add(cat);
      return next;
    });
  };

  const selectAllCats = () => setSelectedCats(new Set(availableCats));
  const deselectAllCats = () => setSelectedCats(new Set());

  const resetFilters = () => {
    setSelectedCats(new Set());
    setQuery("");
    setSortValue("nome_az");
    setFilterMode("include");
    savePrefs({ filterMode: "include", selectedCats: [], sortValue: "nome_az" });
  };

  const selectTab = tab => {
    const next = new URLSearchParams(searchParams);
    if (tab === "inventario") next.delete("tab");
    else next.set("tab", "analisi");
    setSearchParams(next, { replace: true });
  };

  return (
    <Layout title="Giacenze reali" subtitle="inventario fisico e analisi delle scorte" statusMode="hidden" workspace>
      <div className="mb-5">
        <h1 className="font-heading text-[32px] font-black leading-none tracking-[-0.035em] text-slate-950 lg:text-[40px]" data-testid="page-title">Giacenze reali</h1>
        <p className="mt-2 text-base font-medium text-[#526A9D] lg:text-[19px]">Inventario fisico e analisi delle scorte</p>
      </div>

      <div className="mb-5 border-b border-slate-200" role="tablist" aria-label="Viste giacenze reali">
        <div className="flex gap-8">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "inventario"}
            data-testid="real-stock-tab-inventory"
            onClick={() => selectTab("inventario")}
            className={`relative px-1 pb-3 text-[15px] font-bold transition-colors ${activeTab === "inventario" ? "text-slate-950" : "text-slate-500 hover:text-slate-800"}`}
          >
            Inventario
            {activeTab === "inventario" ? <span className="absolute inset-x-0 bottom-[-1px] h-0.5 bg-amber-500" /> : null}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "analisi"}
            data-testid="real-stock-tab-analysis"
            onClick={() => selectTab("analisi")}
            className={`relative px-1 pb-3 text-[15px] font-bold transition-colors ${activeTab === "analisi" ? "text-slate-950" : "text-slate-500 hover:text-slate-800"}`}
          >
            Analisi scorte
            {activeTab === "analisi" ? <span className="absolute inset-x-0 bottom-[-1px] h-0.5 bg-amber-500" /> : null}
          </button>
        </div>
      </div>

      {activeTab === "analisi" ? <StockAnalysis /> : <>
      <div className="mb-5 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <InventoryMetricCard icon={Storefront} label="Negozio" value={formatNum(totalShop)} tone="amber" />
        <InventoryMetricCard icon={Package} label="Vending" value={formatNum(totalVending)} tone="blue" />
        <InventoryMetricCard icon={Archive} label="Totale" value={formatNum(totalShop + totalVending)} tone="slate" />
      </div>

      {/* Search + filter toggle + sort + actions */}
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <SearchBar value={query} onChange={setQuery} placeholder="Cerca prodotto…" className="h-11 min-w-64 flex-1" data-testid="real-stock-search" />

        <button type="button" onClick={() => setShowFilterPanel(p => !p)} className={`inline-flex h-11 items-center gap-1.5 rounded-md border px-3 text-sm font-bold transition ${showFilterPanel || selectedCats.size > 0 ? "border-blue-400 bg-blue-50 text-blue-700" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`} data-testid="toggle-filter-panel">
          <FunnelSimple size={17} /> Filtri{selectedCats.size > 0 ? ` (${selectedCats.size})` : ""}
        </button>

        <div className="inline-flex items-center gap-1.5">
          <ArrowsDownUp size={17} className="text-slate-500" />
          <select value={sortValue} onChange={e => setSortValue(e.target.value)} className="h-11 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium" data-testid="sort-select">
            {SORT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>

        {hasActiveFilters && (
          <button type="button" onClick={resetFilters} className="inline-flex h-11 items-center gap-1 rounded-md border border-red-300 bg-red-50 px-3 text-sm font-bold text-red-700 hover:bg-red-100" data-testid="reset-filters">
            <X size={15} /> Azzera filtri
          </button>
        )}
        <details className="relative z-20 ml-auto">
          <summary className="inline-flex h-11 cursor-pointer list-none items-center gap-2 rounded-md border border-slate-300 bg-white px-4 text-sm font-bold text-slate-700 hover:bg-slate-50">
            <Printer size={18} /> Stampa inventario <CaretDown size={14} weight="bold" />
          </summary>
          <div className="absolute right-0 mt-2 w-64 overflow-hidden rounded-md border border-slate-200 bg-white p-1.5 shadow-xl">
            <button type="button" onClick={() => startPrint("blank", "filtered")} className="block w-full rounded px-3 py-2 text-left text-sm font-semibold text-slate-700 hover:bg-slate-50">Foglio inventario in bianco</button>
            <button type="button" onClick={() => startPrint("current", "filtered")} className="block w-full rounded px-3 py-2 text-left text-sm font-semibold text-slate-700 hover:bg-slate-50">Giacenze attuali</button>
            {hasActiveFilters ? <>
              <div className="my-1 border-t border-slate-100" />
              <button type="button" onClick={() => startPrint("blank", "all")} className="block w-full rounded px-3 py-2 text-left text-xs font-semibold text-blue-700 hover:bg-blue-50">Foglio completo · {rows.length} prodotti</button>
              <button type="button" onClick={() => startPrint("current", "all")} className="block w-full rounded px-3 py-2 text-left text-xs font-semibold text-blue-700 hover:bg-blue-50">Giacenze complete · {rows.length} prodotti</button>
            </> : null}
          </div>
        </details>
        <button type="button" onClick={saveAll} disabled={!hasUnsavedChanges || Boolean(saving)} className="inline-flex h-11 items-center gap-2 rounded-md bg-amber-500 px-5 text-sm font-bold text-slate-950 hover:bg-amber-400 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-500">
          <FloppyDisk size={17} /> Salva modifiche
        </button>
      </div>

      {/* Category filter panel */}
      {showFilterPanel && (
        <Card className="mb-4 p-4" data-testid="filter-panel">
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <span className="text-sm font-bold text-slate-700">Modalità:</span>
            <button type="button" onClick={() => setFilterMode("include")} className={`rounded-full px-3 py-1 text-xs font-bold transition ${filterMode === "include" ? "bg-emerald-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`} data-testid="filter-mode-include">INCLUDI</button>
            <button type="button" onClick={() => setFilterMode("exclude")} className={`rounded-full px-3 py-1 text-xs font-bold transition ${filterMode === "exclude" ? "bg-red-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`} data-testid="filter-mode-exclude">ESCLUDI</button>
            <span className="mx-2 text-slate-300">|</span>
            <button type="button" onClick={selectAllCats} className="text-xs font-medium text-blue-600 hover:underline">Seleziona tutte</button>
            <button type="button" onClick={deselectAllCats} className="text-xs font-medium text-blue-600 hover:underline">Deseleziona tutte</button>
          </div>
          <div className="flex flex-wrap gap-2" data-testid="category-chips">
            {availableCats.map(cat => {
              const selected = selectedCats.has(cat);
              return (
                <button key={cat} type="button" onClick={() => toggleCat(cat)} className={`rounded-full border px-3 py-1 text-xs font-bold transition ${selected ? (filterMode === "include" ? "border-emerald-400 bg-emerald-100 text-emerald-800" : "border-red-400 bg-red-100 text-red-800") : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"}`} data-testid={`cat-chip-${cat}`}>
                  {cat === SENZA_CATEGORIA ? cat : shortCategory(cat)}
                </button>
              );
            })}
          </div>
          {selectedCats.size > 0 && (
            <div className="mt-2 text-xs text-slate-500">
              {filterMode === "include" ? "Mostra solo" : "Escludi"}: {[...selectedCats].map(c => c === SENZA_CATEGORIA ? c : shortCategory(c)).join(", ")}
            </div>
          )}
        </Card>
      )}

      <Card className="overflow-hidden">
        <div className="overflow-x-auto max-h-[68vh]">
          <table className="data-table stock-workspace-table w-full min-w-[900px]">
            <thead><tr><th>Descrizione prodotto</th><th className="text-right">Giacenza negozio</th><th className="text-right">Giacenza vending</th><th className="text-right">Giacenza totale</th><th>Stato</th></tr></thead>
            <tbody>
              {visibleRows.map(row => {
                const shop = integerDraft(displayedValue(row, "giacenza_negozio"));
                const vending = integerDraft(displayedValue(row, "giacenza_vending"));
                const dirty = Boolean(drafts[row.codice]);
                return (
                  <tr key={row.codice} data-testid={`real-stock-row-${row.codice}`} className={dirty ? "bg-amber-50" : ""}>
                    <td>
                      <div className="font-semibold text-slate-900">{row.descrizione}</div>
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs text-slate-500">{row.codice}</span>
                        {row.categoria && <Badge tone="info">{row.categoria}</Badge>}
                      </div>
                      {dirty && <input value={reasons[row.codice] || ""} onChange={event => setReasons(current => ({ ...current, [row.codice]: event.target.value }))} maxLength={500} placeholder="Motivazione rettifica (facoltativa)" className="mt-2 w-full max-w-sm rounded border px-2 py-1 text-xs" />}
                    </td>
                    <td className="text-right"><input aria-label={`Giacenza negozio ${row.descrizione}`} type="number" min="0" step="1" value={displayedValue(row, "giacenza_negozio")} onChange={event => updateDraft(row, "giacenza_negozio", event.target.value)} className="w-24 rounded-md border px-2 py-1 text-right font-mono" /></td>
                    <td className="text-right"><input aria-label={`Giacenza vending ${row.descrizione}`} type="number" min="0" max={row.capacita_vending} step="1" value={displayedValue(row, "giacenza_vending")} onChange={event => updateDraft(row, "giacenza_vending", event.target.value)} disabled={row.colonne_vending === 0} title={row.colonne_vending ? `${row.colonne_vending} colonne · capacità ${row.capacita_vending}` : "Prodotto non presente in vending"} className="w-24 rounded-md border px-2 py-1 text-right font-mono disabled:bg-slate-100" /></td>
                    <td className="text-right">
                      <div className="font-mono text-base font-black">{shop === null || vending === null ? "—" : formatNum(shop + vending)}</div>
                      {dirty && <button type="button" onClick={() => saveRow(row)} disabled={saving === row.codice} className="mt-2 text-xs font-bold text-emerald-700 hover:text-emerald-900">{saving === row.codice ? "Salvataggio…" : "Salva riga"}</button>}
                    </td>
                    <td>{shop === null || vending === null ? <Badge tone="error">DA VERIFICARE</Badge> : shop + vending === 0 ? <Badge tone="error">ESAURITO</Badge> : <Badge tone="ok">OK</Badge>}</td>
                  </tr>
                );
              })}
              {!visibleRows.length && <tr><td colSpan="5" className="py-10 text-center text-slate-500">Nessun prodotto trovato.</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>

      <details className="group mt-6 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 font-bold text-slate-800 hover:bg-slate-50">
          <span className="flex items-center gap-2"><ClockCounterClockwise size={19} /> Ultime rettifiche</span>
          <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600">{formatNum(history.length)}</span>
        </summary>
        <div className="overflow-x-auto border-t border-slate-200">
          <table className="data-table w-full min-w-[760px]">
            <thead><tr><th>Data</th><th>Prodotto</th><th>Ubicazione</th><th className="text-right">Prima</th><th className="text-right">Dopo</th><th className="text-right">Differenza</th><th>Motivazione</th></tr></thead>
            <tbody>
              {history.map(item => <tr key={item.id}><td className="whitespace-nowrap text-xs">{new Date(item.created_at).toLocaleString("it-IT")}</td><td>{item.descrizione}</td><td><Badge tone="info">{item.ubicazione}</Badge></td><td className="text-right font-mono">{item.quantita_precedente}</td><td className="text-right font-mono">{item.quantita_nuova}</td><td className={`text-right font-mono font-bold ${item.differenza < 0 ? "text-red-700" : "text-emerald-700"}`}>{item.differenza > 0 ? "+" : ""}{item.differenza}</td><td className="text-xs text-slate-500">{item.motivazione || "—"}</td></tr>)}
              {!history.length && <tr><td colSpan="7" className="py-8 text-center text-slate-400">Nessuna rettifica registrata.</td></tr>}
            </tbody>
          </table>
        </div>
      </details>

      {hasUnsavedChanges && <div className="fixed bottom-4 right-4 z-50 flex items-center gap-2 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm font-bold text-amber-900 shadow-lg"><WarningCircle size={18} /> Modifiche non salvate</div>}

      {printMode && <InventoryPrint mode={printMode} rows={printRows} printedAt={printedAt} totalCatalog={rows.length} />}
      </>}
    </Layout>
  );
}
