import { NavLink } from "react-router-dom";
import { useState } from "react";
import {
  ChartBar, Package, ListChecks, Storefront, Receipt,
  ShoppingCart, ClockCounterClockwise, CashRegister, Gear, GridFour, Lightning, Truck,
  WarningCircle, FileText, List, X,
} from "@phosphor-icons/react";

const NAV = [
  { to: "/", label: "Dashboard", icon: ChartBar, testid: "nav-dashboard", end: true },
  // { to: "/cassa-rapida", label: "Cassa Rapida", icon: Lightning, testid: "nav-cassa-rapida", highlight: true },
  { to: "/prodotti", label: "Prodotti", icon: Package, testid: "nav-prodotti" },
  { to: "/listino", label: "Listino ADM", icon: ListChecks, testid: "nav-listino" },
  { to: "/magazzino", label: "Magazzino / Pivot", icon: GridFour, testid: "nav-magazzino" },
  { to: "/vending", label: "Vending", icon: Storefront, testid: "nav-vending" },
  { to: "/vendite", label: "Vendite giornaliere", icon: Receipt, testid: "nav-vendite" },
  { to: "/auto-order", label: "Auto-Order", icon: ShoppingCart, testid: "nav-auto-order" },
  { to: "/anomalie", label: "Anomalie", icon: WarningCircle, testid: "nav-anomalie" },
  { to: "/carico", label: "Carico Merce", icon: Truck, testid: "nav-carico" },
  { to: "/ordini", label: "Storico ordini", icon: ClockCounterClockwise, testid: "nav-ordini" },
  { to: "/report", label: "Report giornaliero", icon: FileText, testid: "nav-report" },
  { to: "/cassa", label: "Cassa", icon: CashRegister, testid: "nav-cassa" },
  { to: "/parametri", label: "Parametri", icon: Gear, testid: "nav-parametri" },
];

export default function Sidebar() {
  const [open, setOpen] = useState(false);
  const content = (
    <>
      <div className="p-6 border-b border-slate-800">
        <div className="overline text-slate-400">GOD SERVICES</div>
        <div className="font-heading text-xl font-black tracking-tight mt-1">Gestionale Tabacchi</div>
      </div>
      <nav className="flex-1 overflow-y-auto py-4">
        {NAV.map((n) => {
          const Icon = n.icon;
          return (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              data-testid={n.testid}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 px-5 py-2.5 text-sm font-medium transition-colors border-l-2 ${
                  isActive
                    ? "bg-slate-800 border-brand text-white"
                    : n.highlight
                      ? "border-transparent text-amber-300 hover:bg-slate-800 hover:text-white"
                      : "border-transparent text-slate-300 hover:bg-slate-800 hover:text-white"
                }`
              }
            >
              <Icon size={18} weight="regular" />
              <span>{n.label}</span>
            </NavLink>
          );
        })}
      </nav>
      <div className="p-4 text-xs text-slate-500 border-t border-slate-800">
        v1.1 · Backup & Anomalie
      </div>
    </>
  );
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="fixed left-3 top-3 z-50 rounded-md border border-slate-700 bg-slate-900 p-2 text-white shadow-md md:hidden"
        aria-label="Apri menu"
      >
        <List size={20} />
      </button>
      <aside className="hidden md:flex flex-col fixed left-0 top-0 h-screen w-64 bg-slate-900 text-slate-100 z-40 border-r border-slate-800">
        {content}
      </aside>
      {open && (
        <div className="fixed inset-0 z-[75] md:hidden">
          <button className="absolute inset-0 bg-slate-950/50" onClick={() => setOpen(false)} aria-label="Chiudi menu" />
          <aside className="relative flex h-screen w-72 max-w-[85vw] flex-col bg-slate-900 text-slate-100 shadow-2xl">
            <button onClick={() => setOpen(false)} className="absolute right-3 top-3 rounded-md p-2 text-slate-300 hover:bg-slate-800" aria-label="Chiudi menu">
              <X size={18} />
            </button>
            {content}
          </aside>
        </div>
      )}
    </>
  );
}
