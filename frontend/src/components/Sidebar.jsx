import { NavLink } from "react-router-dom";
import {
  ChartBar, Package, ListChecks, Storefront, Receipt,
  ShoppingCart, ClockCounterClockwise, CashRegister, Gear, GridFour, Lightning,
} from "@phosphor-icons/react";

const NAV = [
  { to: "/", label: "Dashboard", icon: ChartBar, testid: "nav-dashboard", end: true },
  { to: "/cassa-rapida", label: "Cassa Rapida", icon: Lightning, testid: "nav-cassa-rapida", highlight: true },
  { to: "/prodotti", label: "Prodotti", icon: Package, testid: "nav-prodotti" },
  { to: "/listino", label: "Listino ADM", icon: ListChecks, testid: "nav-listino" },
  { to: "/magazzino", label: "Magazzino / Pivot", icon: GridFour, testid: "nav-magazzino" },
  { to: "/vending", label: "Vending", icon: Storefront, testid: "nav-vending" },
  { to: "/vendite", label: "Vendite giornaliere", icon: Receipt, testid: "nav-vendite" },
  { to: "/auto-order", label: "Auto-Order", icon: ShoppingCart, testid: "nav-auto-order" },
  { to: "/ordini", label: "Storico ordini", icon: ClockCounterClockwise, testid: "nav-ordini" },
  { to: "/cassa", label: "Cassa", icon: CashRegister, testid: "nav-cassa" },
  { to: "/parametri", label: "Parametri", icon: Gear, testid: "nav-parametri" },
];

export default function Sidebar() {
  return (
    <aside className="hidden md:flex flex-col fixed left-0 top-0 h-screen w-64 bg-slate-900 text-slate-100 z-40 border-r border-slate-800">
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
        v1.0 · Vending Ready
      </div>
    </aside>
  );
}
