import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowClockwise, ArrowRight } from "@phosphor-icons/react";
import { toast } from "sonner";
import Layout from "../components/Layout";
import { api, apiErrorMessage } from "../lib/api";
import { Card, formatDashboardEur, formatSignedEur } from "../components/UI";

function BalanceCard({ label, value, tone = "default", signed = false, testId }) {
  const toneColor = {
    positive: "text-emerald-700",
    negative: "text-red-600",
    default: "text-slate-900",
  }[tone];

  return (
    <Card className="min-w-0 p-6">
      <div className="overline min-h-8 break-words leading-4" data-testid={`${testId}-label`}>{label}</div>
      <div className={`kpi-value mt-3 truncate text-[clamp(1.8rem,3vw,2.7rem)] tabular-nums ${toneColor}`} data-testid={testId}>
        {signed ? formatSignedEur(value) : formatDashboardEur(value)}
      </div>
    </Card>
  );
}

export default function Contabilita() {
  const [balances, setBalances] = useState({});
  const [refreshing, setRefreshing] = useState(false);
  const [updatedAt, setUpdatedAt] = useState(null);

  const loadBalances = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setRefreshing(true);
    try {
      const response = await api.get("/dashboard", { params: { _ts: Date.now() } });
      setBalances(response.data?.saldi || {});
      setUpdatedAt(new Date());
    } catch (error) {
      toast.error(apiErrorMessage(error, "Impossibile caricare i dati contabili"));
    } finally {
      if (!silent) setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadBalances({ silent: true });
    const refreshOnFocus = () => loadBalances({ silent: true });
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") refreshOnFocus();
    };
    window.addEventListener("focus", refreshOnFocus);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.removeEventListener("focus", refreshOnFocus);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [loadBalances]);

  const difference = balances.differenzaCassaVendingContanti;

  return (
    <Layout title="Contabilità" subtitle="saldi, incassi & riconciliazione" statusMode="compact">
      <div className="mb-4 flex flex-wrap items-center justify-end gap-3">
        {updatedAt ? <span className="text-xs text-slate-500">Aggiornato alle {updatedAt.toLocaleTimeString("it-IT")}</span> : null}
        <button
          type="button"
          data-testid="refresh-accounting"
          onClick={() => loadBalances()}
          disabled={refreshing}
          className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          <ArrowClockwise size={17} className={refreshing ? "animate-spin" : ""} />
          {refreshing ? "Aggiornamento…" : "Aggiorna saldi"}
        </button>
      </div>
      <section className="grid grid-cols-1 gap-5 lg:grid-cols-3" aria-label="Saldi vending">
        <BalanceCard label="Saldo vending" value={balances.saldoVendingTotale} testId="saldo-vending-totale" />
        <BalanceCard label="Saldo vending contanti" value={balances.saldoVendingContanti} testId="saldo-vending-contanti" />
        <BalanceCard label="Saldo vending bancomat / pagamenti elettronici" value={balances.saldoVendingElettronico} testId="saldo-vending-elettronico" />
      </section>

      <section className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-2" aria-label="Riconciliazione contabile">
        <BalanceCard
          label="Giacenza contanti vending"
          value={balances.totalePrelievi}
          tone={balances.totalePrelievi > 0 ? "negative" : "default"}
          testId="totale-prelievi"
        />
        <BalanceCard
          label="Differenza saldo cassa - saldo vending contanti"
          value={difference}
          tone={difference > 0 ? "positive" : difference < 0 ? "negative" : "default"}
          signed
          testId="differenza-cassa-vending"
        />
      </section>

      <section className="mt-5 grid grid-cols-1 gap-5 md:grid-cols-2" aria-label="Operazioni contabili">
        <Link to="/versamenti" className="group">
          <Card className="flex items-center justify-between p-5 transition-all group-hover:-translate-y-0.5 group-hover:border-slate-300 group-hover:shadow-md">
            <div>
              <div className="font-heading text-lg font-black text-slate-900">Versamenti</div>
              <div className="mt-1 text-sm text-slate-500">Registra e consulta i versamenti effettuati</div>
            </div>
            <ArrowRight size={22} className="text-blue-800" />
          </Card>
        </Link>
        <Link to="/prelievi-vending" className="group">
          <Card className="flex items-center justify-between p-5 transition-all group-hover:-translate-y-0.5 group-hover:border-slate-300 group-hover:shadow-md">
            <div>
              <div className="font-heading text-lg font-black text-slate-900">Prelievi Vending</div>
              <div className="mt-1 text-sm text-slate-500">Registra e consulta il contante prelevato</div>
            </div>
            <ArrowRight size={22} className="text-blue-800" />
          </Card>
        </Link>
      </section>
    </Layout>
  );
}
