import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { toast } from "sonner";
import Layout from "../components/Layout";
import { api, apiErrorMessage } from "../lib/api";
import { Card, formatDashboardEur, formatEur, formatNum, formatSignedEur } from "../components/UI";

function formatShortDate(value) {
  if (!value) return "—";
  const [year, month, day] = String(value).slice(0, 10).split("-");
  return day && month && year ? `${day}/${month}` : value;
}

function formatActivityDate(value) {
  if (!value) return "—";
  const raw = String(value);
  const isoDate = raw.match(/^(\d{4})-(\d{2})-(\d{2})(?:T00:00:00(?:\.000)?(?:Z|[+-]\d{2}:?\d{2})?)?$/);
  if (isoDate) return `${isoDate[3]}/${isoDate[2]}`;
  if (/^\d{2}[/-]\d{2}[/-]\d{4}$/.test(raw.slice(0, 10))) return raw.slice(0, 10);
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw.slice(0, 10);
  return new Intl.DateTimeFormat("it-IT", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function ActionCard({ label, value, tone = "default", detail, to, linkLabel, testId }) {
  const tones = {
    default: "text-slate-900",
    success: "text-emerald-700",
    danger: "text-red-600",
    warning: "text-amber-600",
  };

  return (
    <Card className="flex min-h-[178px] min-w-0 flex-col p-5 lg:p-6">
      <div className="overline leading-4">{label}</div>
      <div className={`kpi-value mt-5 text-[clamp(1.7rem,2.4vw,2.5rem)] leading-none ${tones[tone]}`} data-testid={testId}>
        {value}
      </div>
      {detail ? <div className={`mt-3 text-sm font-semibold ${tones[tone]}`}>{detail}</div> : null}
      {to ? (
        <Link
          to={to}
          className="mt-auto flex items-center justify-center rounded-md border border-slate-200 px-4 py-2 text-sm font-semibold text-blue-800 transition-colors hover:border-blue-200 hover:bg-blue-50"
        >
          {linkLabel} <span aria-hidden="true" className="ml-2">→</span>
        </Link>
      ) : null}
    </Card>
  );
}

function ActivityRow({ label, value }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-slate-100 py-3 last:border-0">
      <span className="font-semibold text-slate-800">{label}</span>
      <span className="shrink-0 text-sm text-slate-500">{formatActivityDate(value)}</span>
    </div>
  );
}

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [activities, setActivities] = useState({ ordine: null, versamento: null, prelievo: null });

  useEffect(() => {
    let active = true;
    Promise.all([
      api.get("/dashboard"),
      api.get("/ordini?limit=1"),
      api.get("/versamenti?limit=1"),
      api.get("/prelievi-vending?limit=1"),
    ])
      .then(([dashboard, orders, deposits, withdrawals]) => {
        if (!active) return;
        setData(dashboard.data);
        setActivities({
          ordine: orders.data?.[0]?.data || null,
          versamento: deposits.data?.movimenti?.[0]?.data || null,
          prelievo: withdrawals.data?.movimenti?.[0]?.data || null,
        });
      })
      .catch((error) => toast.error(apiErrorMessage(error, "Impossibile caricare la dashboard")));
    return () => { active = false; };
  }, []);

  const sales = data?.andamento_vendite || {};
  const balances = data?.saldi || {};
  const variation = sales.variazione_pct;
  const venditeVendingContanti = balances.venditeVendingContanti ?? balances.saldoVendingContanti;
  const giacenzaVending = balances.scontriniVending !== undefined
    ? balances.giacenzaVendingContanti
    : balances.totalePrelievi;
  const prelievoVending = balances.prelievoVending
    ?? (venditeVendingContanti !== undefined && giacenzaVending !== undefined
      ? venditeVendingContanti - giacenzaVending
      : undefined);
  const saldoCasse = prelievoVending !== undefined && balances.saldoCassa !== undefined
    ? prelievoVending + balances.saldoCassa
    : balances.saldoCassaNegozioEVending ?? balances.differenzaCassaVendingContanti;
  const chartData = sales.serie || [];
  const vendingToLoad = data?.vending_da_caricare || 0;
  const vendingTotal = data?.vending_totale || 0;

  return (
    <Layout title="Dashboard operativa" subtitle="controllo quotidiano" statusMode="compact">
      <section className="grid grid-cols-1 gap-5 lg:grid-cols-2" aria-label="Indicatori operativi">
        <ActionCard
          label={`Vendite ultimo giorno${sales.ultimo_giorno ? ` · ${formatShortDate(sales.ultimo_giorno)}` : ""}`}
          value={formatDashboardEur(sales.totale_ultimo_giorno)}
          tone={variation >= 0 ? "success" : "danger"}
          detail={variation === null || variation === undefined ? "Media non disponibile" : `${variation >= 0 ? "+" : ""}${variation.toLocaleString("it-IT")}% vs media 30 gg`}
          to="/vendite"
          linkLabel="Inserisci vendita"
          testId="dashboard-last-sales"
        />
        <ActionCard
          label="Da caricare Vending"
          value={`${formatNum(vendingToLoad)} su ${formatNum(vendingTotal)}`}
          tone={vendingToLoad ? "warning" : "success"}
          detail={vendingToLoad ? "colonne da rifornire" : "tutte le colonne sono operative"}
          to="/vending"
          linkLabel="Apri Vending"
          testId="dashboard-vending-to-load"
        />
      </section>

      <section className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-2" aria-label="Riepilogo e attività">
        <Card className="flex min-h-[214px] flex-col p-5 lg:p-6">
          <div className="overline">Cassa &amp; Vending</div>
          <div className="mt-7 text-sm text-slate-500">Saldo cassa negozio e vending</div>
          <div
            className={`kpi-value mt-2 text-[clamp(2rem,3vw,2.8rem)] leading-none ${saldoCasse < 0 ? "text-red-600" : "text-emerald-700"}`}
            data-testid="saldo-cassa-negozio-vending"
          >
            {formatSignedEur(saldoCasse)}
          </div>
          <Link
            to="/contabilita"
            className="mt-auto flex items-center justify-center rounded-md border border-slate-200 px-4 py-2 text-sm font-semibold text-blue-800 transition-colors hover:border-blue-200 hover:bg-blue-50"
          >
            Vai a Contabilità <span aria-hidden="true" className="ml-2">→</span>
          </Link>
        </Card>

        <Card className="min-h-[214px] p-5 lg:p-6">
          <div className="overline mb-3">Attività recenti</div>
          <ActivityRow label="Ultimo carico merce" value={activities.ordine} />
          <ActivityRow label="Ultimo versamento" value={activities.versamento} />
          <ActivityRow label="Ultimo prelievo vending" value={activities.prelievo} />
        </Card>
      </section>

      <section className="mt-5">
        <Card className="p-5 lg:p-6">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <div className="overline">Andamento vendite · ultimi 30 giorni</div>
              <div className="kpi-value mt-2 text-2xl text-slate-900">{formatEur(sales.totale_periodo)}</div>
            </div>
            <div className="text-xs text-slate-500">Fino al {sales.ultimo_giorno ? new Date(`${sales.ultimo_giorno}T12:00:00`).toLocaleDateString("it-IT") : "—"}</div>
          </div>
          <div className="mt-4 h-40 w-full" data-testid="dashboard-sales-chart">
            <ResponsiveContainer
              width="100%"
              height="100%"
              minWidth={0}
              minHeight={160}
              initialDimension={{ width: 1000, height: 160 }}
            >
              <AreaChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="salesFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#86efac" stopOpacity={0.5} />
                    <stop offset="100%" stopColor="#dcfce7" stopOpacity={0.12} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="#E2E8F0" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="data" tickFormatter={formatShortDate} tick={{ fontSize: 11, fill: "#64748B" }} axisLine={false} tickLine={false} minTickGap={26} />
                <YAxis tick={{ fontSize: 11, fill: "#64748B" }} axisLine={false} tickLine={false} width={48} />
                <Tooltip
                  labelFormatter={(label) => new Date(`${label}T12:00:00`).toLocaleDateString("it-IT")}
                  formatter={(value) => [formatEur(value), "Vendite"]}
                  contentStyle={{ border: "1px solid #E2E8F0", borderRadius: 6, boxShadow: "0 8px 24px rgba(15, 23, 42, .08)" }}
                />
                <Area type="monotone" dataKey="importo" stroke="#0F2B5B" strokeWidth={3} fill="url(#salesFill)" dot={false} activeDot={{ r: 4, fill: "#0F2B5B" }} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </section>
    </Layout>
  );
}
