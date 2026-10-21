import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  ArrowRight,
  ChartBar,
  CheckCircle,
  ClipboardText,
  Coins,
  Cube,
  Lightning,
  Package,
  ShoppingCart,
  Storefront,
  WarningCircle,
  Wallet,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import Layout from "../components/Layout";
import { api, apiErrorMessage } from "../lib/api";
import { buildStockAnalysisRows } from "../lib/stockAnalysis";
import { Card, formatDashboardEur, formatEur, formatNum, formatSignedEur } from "../components/UI";

const PERIODS = [
  { value: 7, label: "7 giorni" },
  { value: 30, label: "30 giorni" },
  { value: 365, label: "12 mesi" },
];

function formatShortDate(value) {
  if (!value) return "—";
  const [year, month, day] = String(value).slice(0, 10).split("-");
  return day && month && year ? `${day}/${month}` : value;
}

function KpiCard({ icon: Icon, label, value, detail, tone = "amber", testId }) {
  const tones = {
    red: { card: "border-red-100", icon: "bg-red-50 text-red-600", value: "text-slate-950", detail: "text-red-600" },
    green: { card: "border-emerald-100", icon: "bg-emerald-50 text-emerald-700", value: "text-emerald-700", detail: "text-emerald-700" },
    blue: { card: "border-blue-100", icon: "bg-blue-50 text-blue-700", value: "text-slate-950", detail: "text-blue-700" },
    amber: { card: "border-amber-100", icon: "bg-amber-50 text-amber-700", value: "text-slate-950", detail: "text-[#526A9D]" },
  };
  const style = tones[tone] || tones.amber;
  const compactValue = String(value ?? "").length > 11;
  return (
    <Card className={`flex min-h-[148px] min-w-0 items-center gap-5 p-5 ${style.card}`}>
      <div className={`flex h-[68px] w-[68px] shrink-0 items-center justify-center rounded-lg ${style.icon}`}>
        <Icon size={33} weight="duotone" />
      </div>
      <div className="min-w-0">
        <div className="text-base font-semibold text-[#10245A]">{label}</div>
        <div className={`mt-1 whitespace-nowrap font-heading font-black leading-none tabular-nums ${compactValue ? "text-[clamp(22px,1.7vw,30px)]" : "text-[clamp(26px,2.2vw,38px)]"} ${style.value}`} data-testid={testId}>{value}</div>
        {detail ? <div className={`mt-2 text-sm font-semibold ${style.detail}`}>{detail}</div> : null}
      </div>
    </Card>
  );
}

function TaskRow({ icon: Icon, title, detail, to, tone = "amber" }) {
  const iconTone = {
    amber: "bg-amber-50 text-amber-700",
    red: "bg-red-50 text-red-600",
    blue: "bg-blue-50 text-blue-700",
  }[tone];
  return (
    <Link to={to} className="group flex items-center gap-4 border-b border-slate-100 py-3.5 last:border-0">
      <span className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-full ${iconTone}`}><Icon size={25} weight="duotone" /></span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-heading text-base font-black text-[#10245A]">{title}</span>
        <span className="mt-0.5 block truncate text-sm text-[#6076A7]">{detail}</span>
      </span>
      <ArrowRight size={18} weight="bold" className="text-blue-700 transition-transform group-hover:translate-x-1" />
    </Link>
  );
}

function QuickAction({ icon: Icon, children, to, primary = false }) {
  return (
    <Link to={to} className={`flex h-12 items-center gap-3 rounded-md border px-4 text-sm font-bold transition-colors ${primary ? "border-amber-500 bg-amber-500 text-slate-950 hover:bg-amber-400" : "border-slate-200 bg-white text-[#10245A] hover:border-blue-200 hover:bg-blue-50"}`}>
      <Icon size={22} weight="duotone" />
      <span className="flex-1">{children}</span>
      <ArrowRight size={16} weight="bold" />
    </Link>
  );
}

function ChannelRow({ icon: Icon, label, amount, percent, color }) {
  return (
    <div className="py-2">
      <div className="mb-2 flex items-center gap-3">
        <Icon size={25} weight="duotone" className="text-amber-600" />
        <span className="font-semibold text-[#10245A]">{label}</span>
        <span className="ml-auto font-heading text-lg font-black text-[#10245A]">{formatNum(percent)}%</span>
      </div>
      <div className="ml-9 h-3 overflow-hidden rounded-full bg-slate-100"><div className={`h-full rounded-full ${color}`} style={{ width: `${percent}%` }} /></div>
      <div className="mt-1 text-right text-xs font-medium text-[#6076A7]">{formatEur(amount)}</div>
    </div>
  );
}

export default function Dashboard() {
  const [dashboard, setDashboard] = useState(null);
  const [sales, setSales] = useState(null);
  const [anomalies, setAnomalies] = useState({ totale: 0 });
  const [autoOrder, setAutoOrder] = useState(null);
  const [days, setDays] = useState(30);

  useEffect(() => {
    let active = true;
    Promise.all([
      api.get("/dashboard"),
      api.get("/anomalie"),
      api.get("/auto-order", { timeout: 20000 }),
    ])
      .then(([dashboardResponse, anomalyResponse, autoOrderResponse]) => {
        if (!active) return;
        setDashboard(dashboardResponse.data);
        setAnomalies(anomalyResponse.data?.summary || { totale: 0 });
        setAutoOrder(autoOrderResponse.data);
      })
      .catch(error => toast.error(apiErrorMessage(error, "Impossibile caricare la dashboard")));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    api.get(`/report/vendite?giorni=${days}`)
      .then(response => { if (active) setSales(response.data); })
      .catch(error => { if (active) toast.error(apiErrorMessage(error, "Impossibile caricare l'andamento vendite")); });
    return () => { active = false; };
  }, [days]);

  const balances = dashboard?.saldi || {};
  const latestSales = dashboard?.andamento_vendite || {};
  const latestAmount = dashboard?.vendite_oggi?.importo || latestSales.totale_ultimo_giorno;
  const variation = latestSales.variazione_pct;
  const venditeVendingContanti = balances.venditeVendingContanti ?? balances.saldoVendingContanti;
  const giacenzaVending = balances.scontriniVending !== undefined ? balances.giacenzaVendingContanti : balances.totalePrelievi;
  const giacenzaInizialeVending = balances.giacenzaInizialeVendingContanti ?? 0;
  const prelievoLordo = balances.prelievoVendingLordo ?? balances.prelievoVending ?? (
    venditeVendingContanti !== undefined && giacenzaVending !== undefined
      ? giacenzaInizialeVending + venditeVendingContanti - giacenzaVending
      : undefined
  );
  const prelievoNetto = balances.prelievoVendingNetto ?? (prelievoLordo === undefined ? undefined : prelievoLordo - (balances.scontriniVending ?? 0));
  const operatingBalance = prelievoNetto !== undefined && balances.saldoCassa !== undefined
    ? prelievoNetto + balances.saldoCassa
    : balances.saldoCassaNegozioEVending ?? balances.differenzaCassaVendingContanti;
  const vendingToLoad = dashboard?.vending_da_caricare || 0;
  const vendingTotal = dashboard?.vending_totale || 0;
  const vendingOperational = Math.max(0, vendingTotal - vendingToLoad);

  const analysisRows = useMemo(() => buildStockAnalysisRows(autoOrder), [autoOrder]);
  const reorderRows = analysisRows.filter(row => ["ESAURITO", "SCORTA BASSA"].includes(row.analysisStatus.label));
  const orderRows = autoOrder?.n_righe ?? autoOrder?.righe?.length ?? reorderRows.length;
  const manualChecks = (autoOrder?.riepilogo_stati?.["CONTROLLO MANUALE"] || 0) + (autoOrder?.n_anomalie_stock || 0);
  const monitoredStock = reorderRows.slice(0, 3);

  const channelData = sales?.canali || [];
  const channelTotal = channelData.reduce((sum, item) => sum + Number(item.importo || 0), 0);
  const channel = name => channelData.find(item => item.nome === name) || { importo: 0 };
  const shopAmount = Number(channel("NEGOZIO").importo || 0);
  const vendingAmount = Number(channel("VENDING").importo || 0);
  const shopPercent = channelTotal ? Math.round((shopAmount / channelTotal) * 100) : 0;
  const vendingPercent = channelTotal ? Math.round((vendingAmount / channelTotal) * 100) : 0;

  return (
    <Layout title="Dashboard operativa" subtitle="dati e attività in tempo reale" statusMode="hidden" workspace>
      <div className="mb-5 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="font-heading text-[32px] font-black leading-none tracking-[-0.035em] text-[#10245A] lg:text-[40px]" data-testid="page-title">Dashboard operativa</h1>
          <p className="mt-2 text-base font-medium text-[#526A9D] lg:text-[19px]">Dati e attività in tempo reale</p>
        </div>
        <div className="inline-flex h-11 items-center gap-2 self-start rounded-md border border-emerald-100 bg-emerald-50 px-4 text-sm text-emerald-800">
          <span className="h-3 w-3 rounded-full bg-emerald-500" /> <b>Sistema online</b><span className="text-[#526A9D]">· aggiornato ora</span>
        </div>
      </div>

      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 2xl:grid-cols-4" aria-label="Indicatori operativi">
        <KpiCard icon={ChartBar} label="Vendite oggi" value={formatDashboardEur(latestAmount)} detail={variation == null ? "Confronto non disponibile" : `${variation >= 0 ? "+" : ""}${variation.toLocaleString("it-IT")}% vs media`} tone={variation < 0 ? "red" : "green"} testId="dashboard-last-sales" />
        <KpiCard icon={Wallet} label="Saldo operativo" value={formatSignedEur(operatingBalance)} tone={operatingBalance < 0 ? "red" : "green"} testId="saldo-cassa-negozio-vending" />
        <KpiCard icon={Storefront} label="Vending" value={`${formatNum(vendingOperational)}/${formatNum(vendingTotal)}`} detail={vendingToLoad ? `${formatNum(vendingToLoad)} da rifornire` : "tutte operative"} tone="green" testId="dashboard-vending-to-load" />
        <KpiCard icon={ShoppingCart} label="Ordini aperti" value={formatNum(orderRows)} detail={manualChecks ? `${formatNum(manualChecks)} da controllare` : "pronti da verificare"} tone="amber" />
      </section>

      <section className="mt-5 grid grid-cols-1 gap-4 2xl:grid-cols-3">
        <Card className="border-slate-200 p-5 2xl:col-span-2">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="flex items-center gap-3 font-heading text-xl font-black text-[#10245A]"><ChartBar size={25} weight="duotone" className="text-amber-600" /> Andamento vendite</h2>
              <div className="mt-4 flex flex-wrap items-center gap-4">
                <span className="font-heading text-[32px] font-black text-[#10245A]">{formatEur(sales?.totale)}</span>
                {sales?.variazione_pct != null ? <span className={`font-bold ${sales.variazione_pct >= 0 ? "text-emerald-600" : "text-red-600"}`}>{sales.variazione_pct >= 0 ? "+" : ""}{sales.variazione_pct.toLocaleString("it-IT")}% <span className="font-normal text-[#6076A7]">sul periodo precedente</span></span> : null}
              </div>
            </div>
            <div className="inline-flex rounded-md border border-slate-200 bg-white p-1" aria-label="Periodo andamento vendite">
              {PERIODS.map(period => <button key={period.value} type="button" data-testid={`dashboard-period-${period.value}`} aria-pressed={days === period.value} onClick={() => setDays(period.value)} className={`rounded px-4 py-2 text-sm font-semibold transition-colors ${days === period.value ? "border border-amber-400 bg-amber-50 text-slate-950" : "border border-transparent text-[#6076A7] hover:bg-slate-50"}`}>{period.label}</button>)}
            </div>
          </div>
          <div className="mt-4 h-[230px] w-full" data-testid="dashboard-sales-chart">
            <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={230} initialDimension={{ width: 900, height: 230 }}>
              <AreaChart data={sales?.serie || []} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <defs><linearGradient id="dashboardSalesFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#F2B233" stopOpacity={0.35} /><stop offset="100%" stopColor="#FDF4DE" stopOpacity={0.1} /></linearGradient></defs>
                <CartesianGrid stroke="#DFE6F0" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="data" tickFormatter={formatShortDate} tick={{ fontSize: 11, fill: "#6076A7" }} axisLine={false} tickLine={false} minTickGap={26} />
                <YAxis tick={{ fontSize: 11, fill: "#6076A7" }} axisLine={false} tickLine={false} width={46} />
                <Tooltip labelFormatter={label => new Date(`${label}T12:00:00`).toLocaleDateString("it-IT")} formatter={value => [formatEur(value), "Vendite"]} contentStyle={{ border: "1px solid #DFE6F0", borderRadius: 6, boxShadow: "0 8px 24px rgba(15, 35, 80, .08)" }} />
                <Area type="monotone" dataKey="totale" stroke="#10245A" strokeWidth={3} fill="url(#dashboardSalesFill)" dot={false} activeDot={{ r: 4, fill: "#D99100" }} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card className="p-5">
          <h2 className="flex items-center gap-3 border-b border-slate-100 pb-3 font-heading text-xl font-black text-[#10245A]"><ClipboardText size={25} weight="duotone" className="text-amber-600" /> Da fare oggi</h2>
          <TaskRow icon={ShoppingCart} title={`Conferma ${formatNum(orderRows)} righe`} detail={manualChecks ? `${formatNum(manualChecks)} richiedono controllo` : "Proposta pronta da verificare"} to="/auto-order" />
          <TaskRow icon={WarningCircle} title={`Verifica ${formatNum(anomalies.totale || 0)} anomalie`} detail="Segnalazioni da controllare" to="/anomalie" tone="red" />
          <TaskRow icon={Coins} title="Controlla chiusura cassa" detail="Verifica i movimenti di fine giornata" to="/contabilita" tone="blue" />
          <Link to="/anomalie" className="mt-2 inline-flex items-center gap-2 text-sm font-bold text-amber-700 hover:text-amber-900">Vedi tutte le attività <ArrowRight size={16} /></Link>
        </Card>
      </section>

      <section className="mt-4 grid grid-cols-1 gap-4 2xl:grid-cols-3">
        <Card className="p-5">
          <h2 className="flex items-center gap-3 border-b border-slate-100 pb-3 font-heading text-lg font-black text-[#10245A]"><ChartBar size={23} weight="duotone" className="text-amber-600" /> Vendite per canale</h2>
          <ChannelRow icon={Storefront} label="Negozio" amount={shopAmount} percent={shopPercent} color="bg-amber-500" />
          <ChannelRow icon={Package} label="Vending" amount={vendingAmount} percent={vendingPercent} color="bg-[#10245A]" />
        </Card>

        <Card className="p-5">
          <div className="flex items-center justify-between border-b border-slate-100 pb-3">
            <h2 className="flex items-center gap-3 font-heading text-lg font-black text-[#10245A]"><Cube size={23} weight="duotone" className="text-amber-600" /> Scorte da monitorare</h2>
            <Link to="/giacenze-reali?tab=analisi" className="text-sm font-bold text-amber-700">Vedi tutte</Link>
          </div>
          <div className="divide-y divide-slate-100">
            {monitoredStock.map(row => <Link key={row.codice} to="/giacenze-reali?tab=analisi" className="flex items-center gap-3 py-3"><span className="min-w-0 flex-1"><span className="block truncate font-bold text-[#10245A]">{row.descrizione}</span><span className="text-xs text-[#6076A7]">{row.codice}{row.categoria ? ` · ${row.categoria}` : ""}</span></span><WarningCircle size={18} weight="fill" className="text-amber-500" /><span className="font-bold text-red-600">{formatNum(row.magazzino_reale_lordo ?? row.magazzino_reale)} pz</span><ArrowRight size={15} className="text-blue-700" /></Link>)}
            {!monitoredStock.length ? <div className="flex items-center gap-2 py-8 text-sm text-emerald-700"><CheckCircle size={20} /> Nessuna scorta urgente.</div> : null}
          </div>
        </Card>

        <Card className="p-5">
          <h2 className="mb-3 flex items-center gap-3 font-heading text-lg font-black text-[#10245A]"><Lightning size={23} weight="duotone" className="text-amber-600" /> Azioni rapide</h2>
          <div className="space-y-2">
            <QuickAction icon={ShoppingCart} to="/vendite" primary>Nuova vendita</QuickAction>
            <QuickAction icon={Cube} to="/carico">Registra carico</QuickAction>
            <QuickAction icon={Storefront} to="/vending">Apri Vending</QuickAction>
          </div>
        </Card>
      </section>
    </Layout>
  );
}
