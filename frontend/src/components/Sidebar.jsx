import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  Archive,
  ArrowsLeftRight,
  Calculator,
  CaretDown,
  ChartBar,
  ClipboardText,
  ClockCounterClockwise,
  FileText,
  Gear,
  List,
  ListChecks,
  Package,
  Receipt,
  ShoppingCart,
  Storefront,
  Truck,
  WarningCircle,
  X,
} from "@phosphor-icons/react";

const GROUPS = [
  {
    id: "vendite",
    label: "Vendite",
    items: [
      { to: "/vendite", label: "Vendite giornaliere", icon: Receipt, testid: "nav-vendite" },
      { to: "/report", label: "Report vendite", icon: FileText, testid: "nav-report" },
    ],
  },
  {
    id: "magazzino",
    label: "Magazzino",
    items: [
      { to: "/prodotti", label: "Prodotti", icon: Package, testid: "nav-prodotti" },
      { to: "/listino", label: "Listino ADM", icon: ListChecks, testid: "nav-listino" },
    ],
  },
  {
    id: "ordini",
    label: "Ordini e controllo",
    items: [
      { to: "/auto-order", label: "Auto-Order", icon: ShoppingCart, testid: "nav-auto-order" },
      { to: "/ordini", label: "Storico ordini", icon: ClockCounterClockwise, testid: "nav-ordini" },
      { to: "/giacenze-reali", label: "Giacenze reali", icon: Archive, testid: "nav-real-stock" },
      { to: "/smart-venue", label: "SMARTV VENUE", icon: ArrowsLeftRight, testid: "nav-smart-venue" },
      { to: "/smart-venue-controllo", label: "Controllo SmartVenue", icon: ClipboardText, testid: "nav-smart-venue-control" },
      { to: "/carico", label: "Carico Merce", icon: Truck, testid: "nav-carico" },
    ],
  },
  {
    id: "impostazioni",
    label: "Impostazioni",
    items: [
      { to: "/parametri", label: "Parametri", icon: Gear, testid: "nav-parametri" },
      { to: "/anomalie", label: "Anomalie", icon: WarningCircle, testid: "nav-anomalie" },
    ],
  },
];

const DIRECT_ITEMS = {
  dashboard: { to: "/", label: "Dashboard", icon: ChartBar, testid: "nav-dashboard", end: true },
  vending: { to: "/vending", label: "Vending", icon: Storefront, testid: "nav-vending" },
  accounting: { to: "/contabilita", label: "Contabilità", icon: Calculator, testid: "nav-contabilita" },
};

const DEFAULT_OPEN = Object.fromEntries(GROUPS.map(group => [group.id, false]));

function NavItem({ item, closeMenu }) {
  const Icon = item.icon;
  return (
    <NavLink
      to={item.to}
      end={item.end}
      data-testid={item.testid}
      onClick={closeMenu}
      className={({ isActive }) => `group relative mx-3 flex items-center gap-3.5 rounded-md px-3 py-2.5 text-sm transition-colors ${
        isActive
          ? "bg-white/10 text-white before:absolute before:-left-3 before:top-1.5 before:bottom-1.5 before:w-1 before:rounded-r before:bg-amber-400"
          : "text-slate-400 hover:bg-white/5 hover:text-white"
      }`}
    >
      {({ isActive }) => (
        <>
          <Icon size={19} weight={isActive ? "fill" : "bold"} className={isActive ? "text-amber-400" : ""} />
          <span className="min-w-0 truncate font-medium">{item.label}</span>
        </>
      )}
    </NavLink>
  );
}

function Group({ group, expanded, toggle, closeMenu }) {
  return (
    <div className="border-t border-white/10 py-2.5">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between px-5 py-2 text-left text-xs font-bold uppercase tracking-[0.14em] text-slate-400 hover:text-slate-200"
      >
        <span>{group.label}</span>
        <CaretDown size={13} weight="bold" className={`transition-transform ${expanded ? "rotate-180" : ""}`} />
      </button>
      {expanded ? <div className="space-y-0.5">{group.items.map(item => <NavItem key={item.to} item={item} closeMenu={closeMenu} />)}</div> : null}
    </div>
  );
}

export default function Sidebar() {
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(DEFAULT_OPEN);

  useEffect(() => {
    const currentGroup = GROUPS.find(group => group.items.some(item => item.to === location.pathname));
    if (currentGroup) setExpanded(value => ({ ...value, [currentGroup.id]: true }));
  }, [location.pathname]);

  const closeMenu = () => setOpen(false);
  const toggleGroup = id => setExpanded(value => ({ ...value, [id]: !value[id] }));

  const content = (
    <>
      <div className="flex h-[84px] items-center overflow-hidden border-b border-white/10 px-[18px]">
        <img src="/terrybell-palace.png" alt="Terrybell Palace" className="h-auto w-[236px] max-w-none object-contain object-left" />
      </div>
      <nav className="flex-1 overflow-y-auto py-3" aria-label="Navigazione principale">
        <NavItem item={DIRECT_ITEMS.dashboard} closeMenu={closeMenu} />
        <Group group={GROUPS[0]} expanded={expanded.vendite} toggle={() => toggleGroup("vendite")} closeMenu={closeMenu} />
        <Group group={GROUPS[1]} expanded={expanded.magazzino} toggle={() => toggleGroup("magazzino")} closeMenu={closeMenu} />
        <div className="border-t border-white/10 py-2"><NavItem item={DIRECT_ITEMS.vending} closeMenu={closeMenu} /></div>
        <Group group={GROUPS[2]} expanded={expanded.ordini} toggle={() => toggleGroup("ordini")} closeMenu={closeMenu} />
        <div className="border-t border-white/10 py-2"><NavItem item={DIRECT_ITEMS.accounting} closeMenu={closeMenu} /></div>
        <Group group={GROUPS[3]} expanded={expanded.impostazioni} toggle={() => toggleGroup("impostazioni")} closeMenu={closeMenu} />
      </nav>
    </>
  );

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed left-3 top-3 z-50 rounded-md border border-slate-700 bg-[#181818] p-2 text-white shadow-md md:hidden"
        aria-label="Apri menu"
      >
        <List size={20} />
      </button>
      <aside className="fixed left-0 top-0 z-40 hidden h-screen w-[272px] flex-col border-r border-black/30 bg-[#181818] text-slate-100 md:flex">
        {content}
      </aside>
      {open ? (
        <div className="fixed inset-0 z-[75] md:hidden">
          <button type="button" className="absolute inset-0 bg-slate-950/60" onClick={closeMenu} aria-label="Chiudi menu" />
          <aside className="relative flex h-screen w-72 max-w-[88vw] flex-col bg-[#181818] text-slate-100 shadow-2xl">
            <button type="button" onClick={closeMenu} className="absolute right-3 top-3 rounded-md p-2 text-slate-300 hover:bg-white/10" aria-label="Chiudi menu">
              <X size={18} />
            </button>
            {content}
          </aside>
        </div>
      ) : null}
    </>
  );
}
