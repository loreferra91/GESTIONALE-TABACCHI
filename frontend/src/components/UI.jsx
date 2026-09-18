export function Badge({ children, tone = "ok" }) {
  const map = {
    ok: "bg-emerald-100 text-emerald-800 border-emerald-200",
    warning: "bg-amber-100 text-amber-800 border-amber-200",
    error: "bg-red-100 text-red-800 border-red-200",
    info: "bg-slate-100 text-slate-800 border-slate-200",
  };
  // Abbreviazioni per label lunghe
  const s = typeof children === "string" ? children : null;
  let display = children;
  if (s === "SIGARETTE ELETTRONICHE") display = "ELETTR.";
  else if (s && s.startsWith("SLOW MOVER: 1 LOTTO")) display = "SLOW MOVER";
  else if (s && s.startsWith("FAST MOVER")) display = "FAST MOVER";
  else if (s && s.startsWith("ALLERT SOTTO SOGLIA")) display = "SOTTO SOGLIA";
  return (
    <span
      title={s || undefined}
      className={`inline-flex items-center whitespace-nowrap px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${map[tone] || map.info}`}
    >
      {display}
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
    <Card className="p-4 lg:p-5 hover:-translate-y-[2px] hover:shadow-md transition-transform duration-200 min-w-0">
      <div className="overline truncate">{label}</div>
      <div className={`kpi-value text-2xl xl:text-3xl 2xl:text-4xl mt-2 truncate tabular-nums ${toneColor}`}>
        {value}
        {suffix && <span className="text-base text-slate-400 ml-1">{suffix}</span>}
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
