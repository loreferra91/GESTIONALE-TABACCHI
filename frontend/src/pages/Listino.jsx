import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowClockwise,
  CheckCircle,
  Database,
  FileXls,
  Info,
  WarningCircle,
} from "@phosphor-icons/react";
import Layout from "../components/Layout";
import { api, apiErrorMessage } from "../lib/api";
import { Badge, Card, formatEur, formatNum } from "../components/UI";
import { Th, SearchBar } from "../lib/tableHooks";
import ConfirmDialog from "../components/ConfirmDialog";
import { toast } from "sonner";

const PAGE_SIZE = 100;

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(`${value}T12:00:00`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("it-IT");
}

function PreviewMetric({ label, value, tone = "default" }) {
  const tones = {
    default: "text-slate-950",
    success: "text-emerald-700",
    warning: "text-amber-700",
    danger: "text-red-600",
  };
  return (
    <div className="min-w-0 border-r border-slate-200 px-4 last:border-r-0 first:pl-0">
      <div className="text-xs font-semibold text-slate-500">{label}</div>
      <div className={`mt-1 font-heading text-2xl font-black tabular-nums ${tones[tone]}`}>{formatNum(value)}</div>
    </div>
  );
}

export default function Listino() {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [statusLoading, setStatusLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [legacySyncing, setLegacySyncing] = useState(false);
  const [fitStatus, setFitStatus] = useState(null);
  const [preview, setPreview] = useState(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [categoria, setCategoria] = useState("");
  const [fonte, setFonte] = useState("");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [page, setPage] = useState(1);
  const [sortKey, setSortKey] = useState("descrizione");
  const [sortDir, setSortDir] = useState("asc");

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => { setPage(1); }, [debouncedQuery, categoria, fonte]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await api.get("/listino", {
        params: {
          q: debouncedQuery || undefined,
          categoria: categoria || undefined,
          fonte: fonte || undefined,
          limit: PAGE_SIZE,
          skip: (page - 1) * PAGE_SIZE,
          sort: sortKey,
          direction: sortDir,
        },
      });
      setItems(response.data.items);
      setTotal(response.data.total);
    } catch (error) {
      toast.error(apiErrorMessage(error, "Impossibile caricare il listino"));
    } finally {
      setLoading(false);
    }
  }, [categoria, debouncedQuery, fonte, page, sortDir, sortKey]);

  const loadFitStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      const response = await api.get("/fit/status", { timeout: 120000 });
      setFitStatus(response.data);
    } catch (error) {
      console.warn("fit status failed", error);
    } finally {
      setStatusLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadFitStatus(); }, [loadFitStatus]);

  const categorie = useMemo(
    () => Object.keys(fitStatus?.fit?.categories || {}).sort(),
    [fitStatus],
  );
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const toggleSort = (key) => {
    setPage(1);
    if (sortKey === key) setSortDir(current => current === "asc" ? "desc" : "asc");
    else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  const preparePreview = async () => {
    setPreviewing(true);
    try {
      const response = await api.post("/fit/preview", {}, { timeout: 600000 });
      setPreview(response.data);
      if (response.data.prezzi_modificati) {
        toast.success(`${formatNum(response.data.prezzi_modificati)} prezzi da verificare`);
      } else {
        toast.success("Il catalogo prodotti è già allineato al tariffario");
      }
    } catch (error) {
      toast.error(apiErrorMessage(error, "Errore durante il controllo del tariffario"));
    } finally {
      setPreviewing(false);
    }
  };

  const applyPreview = async () => {
    if (!preview?.preview_id) return;
    setApplying(true);
    try {
      const response = await api.post(
        "/fit/apply",
        { preview_id: preview.preview_id },
        { timeout: 600000 },
      );
      toast.success(
        `Tariffario aggiornato: ${formatNum(response.data.righe)} articoli, ${formatNum(response.data.prodotti_aggiornati)} prodotti modificati`,
      );
      setConfirmOpen(false);
      setPreview(null);
      await Promise.all([load(), loadFitStatus()]);
    } catch (error) {
      toast.error(apiErrorMessage(error, "Errore durante l'applicazione del tariffario"));
    } finally {
      setApplying(false);
    }
  };

  const syncAdmFallback = async () => {
    setLegacySyncing(true);
    try {
      const response = await api.post("/adm/sync", {}, { timeout: 600000 });
      toast.success(`PDF ADM sincronizzati: ${formatNum(response.data.righe)} righe`);
      await Promise.all([load(), loadFitStatus()]);
    } catch (error) {
      toast.error(apiErrorMessage(error, "Errore sincronizzazione PDF ADM"));
    } finally {
      setLegacySyncing(false);
    }
  };

  return (
    <Layout
      title="Listino ADM"
      subtitle={`${formatNum(total)} articoli nel listino · pagina ${formatNum(page)} di ${formatNum(totalPages)}`}
      actions={(
        <button
          onClick={preparePreview}
          disabled={previewing || applying}
          className="inline-flex items-center gap-2 rounded-md bg-amber-500 px-4 py-2 text-sm font-bold text-slate-950 shadow-sm hover:bg-amber-400 disabled:opacity-50"
        >
          <ArrowClockwise size={17} weight="bold" className={previewing ? "animate-spin" : ""} />
          {previewing ? "Controllo in corso..." : "Controlla aggiornamenti"}
        </button>
      )}
    >
      <Card className="mb-4 overflow-hidden">
        <div className="grid gap-0 lg:grid-cols-[1.25fr_1fr_auto]">
          <div className="flex items-start gap-3 border-b border-slate-200 p-4 lg:border-b-0 lg:border-r">
            <div className="rounded-md bg-emerald-50 p-2 text-emerald-700"><FileXls size={22} weight="duotone" /></div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="font-heading text-base font-black text-slate-950">Tariffario FIT in formato XLS</h2>
                <Badge tone={fitStatus?.fit ? "ok" : "warning"}>{fitStatus?.fit ? "Disponibile" : "Da verificare"}</Badge>
              </div>
              <p className="mt-1 text-sm text-slate-600">
                {statusLoading
                  ? "Controllo della fonte in corso..."
                  : `${formatNum(fitStatus?.fit?.row_count)} articoli · in vigore dal ${formatDate(fitStatus?.fit?.effective_date)}`}
              </p>
            </div>
          </div>
          <div className="flex items-start gap-3 border-b border-slate-200 p-4 lg:border-b-0 lg:border-r">
            <div className="rounded-md bg-blue-50 p-2 text-blue-700"><Database size={22} weight="duotone" /></div>
            <div>
              <h2 className="font-heading text-base font-black text-slate-950">Verifica ufficiale ADM</h2>
              <p className="mt-1 text-sm text-slate-600">
                {fitStatus?.adm_newer_categories?.length
                  ? `${formatNum(fitStatus.adm_newer_categories.length)} categorie verranno verificate sui PDF ADM`
                  : "Il foglio FIT è più recente dei PDF di categoria"}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3 p-4">
            <div className="text-right">
              <div className="text-xs font-semibold text-slate-500">Ultimo aggiornamento</div>
              <div className="mt-1 text-sm font-bold text-slate-900">
                {fitStatus?.last_sync?.created_at
                  ? new Date(fitStatus.last_sync.created_at).toLocaleString("it-IT")
                  : "Mai eseguito"}
              </div>
            </div>
          </div>
        </div>
      </Card>

      {preview && (
        <Card className="mb-4 border-amber-300 shadow-sm">
          <div className="flex flex-col gap-4 border-b border-slate-200 p-5 xl:flex-row xl:items-start xl:justify-between">
            <div>
              <div className="flex items-center gap-2">
                <CheckCircle size={22} weight="fill" className="text-emerald-600" />
                <h2 className="font-heading text-lg font-black text-slate-950">Anteprima pronta</h2>
              </div>
              <p className="mt-1 text-sm text-slate-600">
                I prezzi non sono ancora stati modificati. L’applicazione crea prima un backup di prodotti e listino.
              </p>
            </div>
            <div className="flex gap-2">
              <button onClick={() => setPreview(null)} className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold hover:bg-slate-50">
                Annulla
              </button>
              <button onClick={() => setConfirmOpen(true)} className="rounded-md bg-slate-950 px-4 py-2 text-sm font-bold text-white hover:bg-slate-800">
                Applica aggiornamento
              </button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-y-4 p-5 sm:grid-cols-3 xl:grid-cols-6">
            <PreviewMetric label="Righe sorgente" value={preview.righe} />
            <PreviewMetric label="Prodotti collegati" value={preview.prodotti_collegati} tone="success" />
            <PreviewMetric label="Prezzi modificati" value={preview.prezzi_modificati} tone={preview.prezzi_modificati ? "warning" : "success"} />
            <PreviewMetric label="Invariati" value={preview.prezzi_invariati} />
            <PreviewMetric label="Non trovati" value={preview.prodotti_non_trovati} tone={preview.prodotti_non_trovati ? "warning" : "default"} />
            <PreviewMetric label="Ambigui" value={preview.collegamenti_ambigui} tone={preview.collegamenti_ambigui ? "danger" : "default"} />
          </div>
          {!!preview.adm_overlays?.length && (
            <div className="mx-5 mb-4 flex items-start gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900">
              <Info size={18} className="mt-0.5 shrink-0" />
              <span>Integrati i PDF ADM più recenti per: {preview.adm_overlays.map(item => item.categoria).join(", ")}.</span>
            </div>
          )}
          {!!preview.warnings?.length && (
            <div className="mx-5 mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              {preview.warnings.join(" · ")}
            </div>
          )}
          <div className="border-t border-slate-200">
            <div className="flex items-center justify-between px-5 py-3">
              <h3 className="font-heading text-sm font-black text-slate-900">Variazioni di prezzo</h3>
              <span className="text-xs text-slate-500">{formatNum(preview.variazioni?.length)} righe</span>
            </div>
            <div className="max-h-64 overflow-auto">
              <table className="data-table w-full">
                <thead><tr><th>Prodotto</th><th>Codice ADM</th><th className="text-right">Attuale</th><th className="text-right">Nuovo</th><th className="text-right">Differenza</th></tr></thead>
                <tbody>
                  {!preview.variazioni?.length && (
                    <tr><td colSpan={5} className="py-6 text-center text-slate-500">Nessuna variazione di prezzo</td></tr>
                  )}
                  {preview.variazioni?.map(item => (
                    <tr key={`${item.codice}-${item.adm_codice}`}>
                      <td className="wrap font-semibold">{item.descrizione}</td>
                      <td className="font-mono text-slate-500">{item.adm_codice}</td>
                      <td className="text-right font-mono">{formatEur(item.prezzo_attuale)}</td>
                      <td className="text-right font-mono font-bold">{formatEur(item.prezzo_nuovo)}</td>
                      <td className={`text-right font-mono font-bold ${item.differenza > 0 ? "text-red-600" : "text-emerald-700"}`}>
                        {item.differenza > 0 ? "+" : ""}{formatEur(item.differenza)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </Card>
      )}

      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center">
        <SearchBar data-testid="listino-search" value={query} onChange={setQuery} placeholder="Cerca codice o prodotto..." className="flex-1 lg:max-w-xl" />
        <select value={categoria} onChange={event => setCategoria(event.target.value)} className="min-h-10 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm">
          <option value="">Tutte le categorie</option>
          {categorie.map(item => <option key={item} value={item}>{item}</option>)}
        </select>
        <select value={fonte} onChange={event => setFonte(event.target.value)} className="min-h-10 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm">
          <option value="">Tutte le fonti</option>
          <option value="FIT">FIT XLS</option>
          <option value="ADM">ADM PDF</option>
        </select>
      </div>

      <Card className="overflow-hidden">
        <div className="max-h-[66vh] overflow-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <Th sortKey="codice" currentKey={sortKey} dir={sortDir} onClick={toggleSort}>Codice</Th>
                <Th sortKey="adm_codice" currentKey={sortKey} dir={sortDir} onClick={toggleSort}>Cod. ADM</Th>
                <Th sortKey="descrizione" currentKey={sortKey} dir={sortDir} onClick={toggleSort}>Descrizione</Th>
                <Th sortKey="categoria_adm" currentKey={sortKey} dir={sortDir} onClick={toggleSort}>Categoria ADM</Th>
                <Th sortKey="confezione" currentKey={sortKey} dir={sortDir} onClick={toggleSort}>Confezione</Th>
                <Th sortKey="fonte" currentKey={sortKey} dir={sortDir} onClick={toggleSort}>Fonte</Th>
                <Th sortKey="prezzo" currentKey={sortKey} dir={sortDir} onClick={toggleSort} align="right">Prezzo</Th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={7} className="py-10 text-center text-slate-500">Caricamento del listino...</td></tr>}
              {!loading && !items.length && <tr><td colSpan={7} className="py-10 text-center text-slate-500">Nessun articolo trovato</td></tr>}
              {!loading && items.map(item => (
                <tr key={item.id || `${item.adm_codice}-${item.codice}`}>
                  <td className="font-mono">{item.codice}</td>
                  <td className="font-mono text-slate-500">{item.adm_codice || "—"}</td>
                  <td className="wrap min-w-72 font-semibold">{item.descrizione}</td>
                  <td>{item.categoria_adm ? <Badge tone="info">{item.categoria_adm}</Badge> : <span className="text-slate-400">—</span>}</td>
                  <td className="wrap max-w-64 text-xs text-slate-500">{item.confezione || "—"}</td>
                  <td><Badge tone={item.fonte === "FIT" ? "ok" : "info"}>{item.fonte || "Locale"}</Badge></td>
                  <td className="text-right font-mono font-semibold">{formatEur(item.prezzo)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex flex-col gap-3 border-t border-slate-200 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
          <span className="text-slate-500">
            {total ? `${formatNum((page - 1) * PAGE_SIZE + 1)}–${formatNum(Math.min(page * PAGE_SIZE, total))} di ${formatNum(total)}` : "0 risultati"}
          </span>
          <div className="flex items-center gap-2">
            <button disabled={page <= 1 || loading} onClick={() => setPage(current => current - 1)} className="rounded-md border border-slate-300 px-3 py-1.5 font-semibold hover:bg-slate-50 disabled:opacity-40">Precedente</button>
            <span className="min-w-20 text-center font-semibold">{page} / {totalPages}</span>
            <button disabled={page >= totalPages || loading} onClick={() => setPage(current => current + 1)} className="rounded-md border border-slate-300 px-3 py-1.5 font-semibold hover:bg-slate-50 disabled:opacity-40">Successiva</button>
          </div>
        </div>
      </Card>

      <div className="mt-4 flex flex-col gap-3 rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-2">
          <WarningCircle size={18} className="mt-0.5 shrink-0 text-slate-500" />
          <span>Il parser dei PDF ADM resta disponibile come procedura di emergenza.</span>
        </div>
        <button onClick={syncAdmFallback} disabled={legacySyncing} className="whitespace-nowrap font-semibold text-slate-700 underline decoration-slate-300 underline-offset-4 hover:text-slate-950 disabled:opacity-40">
          {legacySyncing ? "Sincronizzazione PDF..." : "Usa fallback PDF ADM"}
        </button>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title="Applicare il nuovo tariffario?"
        confirmLabel="Crea backup e aggiorna"
        loading={applying}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={applyPreview}
      >
        Verranno aggiornati <b>{formatNum(preview?.prodotti_collegati)}</b> prodotti collegati per codice ADM.
        I collegamenti ambigui e i prodotti non trovati resteranno invariati. Prima dell’operazione verrà creato un backup recuperabile.
      </ConfirmDialog>
    </Layout>
  );
}
