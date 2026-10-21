import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
const PREVIEW_PAGE_SIZE = 50;
const FIELD_LABELS = {
  codice: "Codice",
  adm_codice: "Cod. ADM",
  descrizione: "Descrizione",
  categoria_adm: "Categoria ADM",
  confezione: "Confezione",
  fonte: "Fonte",
  prezzo: "Prezzo",
};

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

function formatChangeValue(field, value) {
  if (value === null || value === undefined || value === "") return "—";
  return field === "prezzo" ? formatEur(value) : String(value);
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
  const [appliedResult, setAppliedResult] = useState(null);
  const [previewFilter, setPreviewFilter] = useState("TUTTI");
  const [previewPage, setPreviewPage] = useState(1);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [categoria, setCategoria] = useState("");
  const [fonte, setFonte] = useState("");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [page, setPage] = useState(1);
  const [sortKey, setSortKey] = useState("descrizione");
  const [sortDir, setSortDir] = useState("asc");
  const previewRef = useRef(null);

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
  const filteredPreviewChanges = useMemo(() => {
    const changes = preview?.listino_variazioni || [];
    return previewFilter === "TUTTI"
      ? changes
      : changes.filter(item => item.stato === previewFilter);
  }, [preview, previewFilter]);
  const previewPages = Math.max(1, Math.ceil(filteredPreviewChanges.length / PREVIEW_PAGE_SIZE));
  const visiblePreviewChanges = filteredPreviewChanges.slice(
    (previewPage - 1) * PREVIEW_PAGE_SIZE,
    previewPage * PREVIEW_PAGE_SIZE,
  );

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
      setAppliedResult(null);
      setPreviewFilter("TUTTI");
      setPreviewPage(1);
      window.requestAnimationFrame(() => {
        if (typeof previewRef.current?.scrollIntoView === "function") {
          previewRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
      if (response.data.prezzi_modificati) {
        toast.success(`${formatNum(response.data.prezzi_modificati)} prezzi da verificare`);
      } else if (response.data.nuovi_articoli_listino) {
        toast.success(`${formatNum(response.data.nuovi_articoli_listino)} righe del listino da aggiungere`);
      } else {
        toast.success("I prezzi sono già corretti; puoi comunque riallineare il listino ufficiale");
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
      setAppliedResult(response.data);
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
          {previewing ? "Preparo l’anteprima..." : "Prepara aggiornamento"}
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
                  ? `${formatNum(fitStatus.adm_newer_categories.length)} categorie ADM più recenti disponibili nella verifica PDF separata`
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
        <div ref={previewRef} className="scroll-mt-24">
        <Card className={`mb-4 shadow-sm ${appliedResult ? "border-emerald-300" : "border-amber-300"}`}>
          <div className="flex flex-col gap-4 border-b border-slate-200 p-5 xl:flex-row xl:items-start xl:justify-between">
            <div>
              <div className="flex items-center gap-2">
                <CheckCircle size={22} weight="fill" className="text-emerald-600" />
                <h2 className="font-heading text-lg font-black text-slate-950">
                  {appliedResult ? "Aggiornamento completato" : "Anteprima pronta"}
                </h2>
              </div>
              <p className="mt-1 text-sm text-slate-600">
                {appliedResult
                  ? `${formatNum(appliedResult.aggiornati)} righe aggiornate e ${formatNum(appliedResult.inseriti)} nuove righe inserite. Backup: ${appliedResult.backup_id || "creato"}.`
                  : "I dati non sono ancora stati modificati. L’applicazione crea prima un backup di prodotti e listino."}
              </p>
            </div>
            <div className="flex gap-2">
              <button onClick={() => setPreview(null)} className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold hover:bg-slate-50">
                {appliedResult ? "Chiudi riepilogo" : "Annulla"}
              </button>
              {!appliedResult && (
                <button onClick={() => setConfirmOpen(true)} className="rounded-md bg-slate-950 px-4 py-2 text-sm font-bold text-white hover:bg-slate-800">
                  Applica aggiornamento
                </button>
              )}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-y-4 p-5 sm:grid-cols-3 xl:grid-cols-6">
            <PreviewMetric label="Righe sorgente" value={preview.righe} />
            <PreviewMetric label="Nuove righe" value={preview.listino_nuovi} tone={preview.listino_nuovi ? "warning" : "success"} />
            <PreviewMetric label="Righe aggiornate" value={preview.listino_aggiornati} tone={preview.listino_aggiornati ? "warning" : "success"} />
            <PreviewMetric label="Codici ADM compilati" value={preview.listino_codici_adm_compilati} tone="success" />
            <PreviewMetric label="Categorie compilate" value={preview.listino_categorie_compilate} tone="success" />
            <PreviewMetric label="Fonti compilate" value={preview.listino_fonti_compilate} tone="success" />
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
            <div className="flex flex-col gap-3 px-5 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h3 className="font-heading text-sm font-black text-slate-900">Modifiche effettive al listino</h3>
                <p className="mt-0.5 text-xs text-slate-500">
                  Ogni riga mostra esattamente quali campi cambiano, con valore precedente e nuovo.
                </p>
              </div>
              <div className="flex gap-2">
                {[
                  ["TUTTI", "Tutte"],
                  ["AGGIORNATO", "Aggiornate"],
                  ["NUOVO", "Nuove"],
                ].map(([value, label]) => (
                  <button
                    key={value}
                    onClick={() => { setPreviewFilter(value); setPreviewPage(1); }}
                    className={`rounded-md px-3 py-1.5 text-xs font-bold ${previewFilter === value ? "bg-slate-950 text-white" : "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <div className="max-h-[34rem] overflow-auto border-t border-slate-200">
              <table className="data-table w-full">
                <thead><tr><th>Stato</th><th>Articolo</th><th>Campo</th><th>Prima</th><th>Dopo</th></tr></thead>
                <tbody>
                  {!visiblePreviewChanges.length && (
                    <tr><td colSpan={5} className="py-6 text-center text-slate-500">Nessuna modifica in questa sezione</td></tr>
                  )}
                  {visiblePreviewChanges.map(item => (
                    <tr key={`${item.stato}-${item.adm_codice}`}>
                      <td className="align-top"><Badge tone={item.stato === "NUOVO" ? "ok" : "warning"}>{item.stato === "NUOVO" ? "Nuova" : "Aggiornata"}</Badge></td>
                      <td className="wrap min-w-64 align-top">
                        <div className="font-semibold text-slate-900">{item.descrizione}</div>
                        <div className="mt-1 font-mono text-xs text-slate-500">{item.codice} · ADM {item.adm_codice}</div>
                      </td>
                      <td className="align-top text-xs font-semibold text-slate-700">
                        {item.campi.map(change => <div key={change.campo} className="min-h-6 py-1">{FIELD_LABELS[change.campo] || change.campo}</div>)}
                      </td>
                      <td className="wrap max-w-72 align-top text-xs text-slate-500">
                        {item.campi.map(change => <div key={change.campo} className="min-h-6 py-1">{formatChangeValue(change.campo, change.prima)}</div>)}
                      </td>
                      <td className="wrap max-w-72 align-top text-xs font-semibold text-slate-900">
                        {item.campi.map(change => <div key={change.campo} className="min-h-6 py-1">{formatChangeValue(change.campo, change.dopo)}</div>)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex flex-col gap-2 border-t border-slate-200 px-5 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
              <span className="text-slate-500">
                {formatNum(filteredPreviewChanges.length)} righe con modifiche · pagina {previewPage} di {previewPages}
              </span>
              <div className="flex gap-2">
                <button disabled={previewPage <= 1} onClick={() => setPreviewPage(current => current - 1)} className="rounded-md border border-slate-300 px-3 py-1.5 font-semibold disabled:opacity-40">Precedente</button>
                <button disabled={previewPage >= previewPages} onClick={() => setPreviewPage(current => current + 1)} className="rounded-md border border-slate-300 px-3 py-1.5 font-semibold disabled:opacity-40">Successiva</button>
              </div>
            </div>
          </div>
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
        </div>
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
        Verranno riallineate <b>{formatNum(preview?.righe)}</b> righe del listino e aggiornati <b>{formatNum(preview?.prodotti_collegati)}</b> prodotti collegati per codice ADM.
        I collegamenti ambigui e i prodotti non trovati resteranno invariati. Prima dell’operazione verrà creato un backup recuperabile.
      </ConfirmDialog>
    </Layout>
  );
}
