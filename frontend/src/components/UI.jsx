export function Badge({ children, tone = "ok" }) {
  const map = {
    ok: "bg-emerald-100 text-emerald-800 border-emerald-200",
    warning: "bg-amber-100 text-amber-800 border-amber-200",
    error: "bg-red-100 text-red-800 border-red-200",
    info: "bg-slate-100 text-slate-800 border-slate-200",
  };
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${map[tone] || map.info}`}>
      {children}
    </span>
  );
}

export function statoToTone(s) {
  if (!s) return "info";
  const u = s.toUpperCase();
  if (["OK", "PIENO", "RICARICA COMPLETA"].some(k => u.includes(k))) return "ok";
  if (["ESAURITO", "RIORDINO", "DA CARICARE", "NEGATIVA"].some(k => u.includes(k))) return "error";
  if (["FERMO", "LENTO", "ALLERT", "OLTRE"].some(k => u.includes(k))) return "warning";
  return "info";
}

export function Card({ children, className = "" }) {
  return (
    <div className={`bg-white border border-slate-200 rounded-md ${className}`}>{children}</div>
  );
}

export function KpiCard({ label, value, tone = "default", suffix }) {
  const toneColor = { danger: "text-red-600", warning: "text-amber-600", success: "text-emerald-700", default: "text-slate-900" }[tone];
  return (
    <Card className="p-5 hover:-translate-y-[2px] hover:shadow-md transition-transform duration-200">
      <div className="overline">{label}</div>
      <div className={`kpi-value text-3xl sm:text-4xl mt-2 ${toneColor}`}>
        {value}
        {suffix && <span className="text-lg text-slate-400 ml-1">{suffix}</span>}
      </div>
    </Card>
  );
}

export function formatEur(v) {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR" }).format(Number(v) || 0);
}

export function formatNum(v) {
  if (v === null || v === undefined) return "0";
  return new Intl.NumberFormat("it-IT").format(Number(v) || 0);
}
