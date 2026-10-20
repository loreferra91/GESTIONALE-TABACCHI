import { ArrowLeft } from "@phosphor-icons/react";
import { useLocation, useNavigate } from "react-router-dom";
import Sidebar from "./Sidebar";
import DataStatusBar from "./DataStatusBar";
import GlobalSearch from "./GlobalSearch";

export default function Layout({ children, title, subtitle, actions, statusMode = "full", workspace = false }) {
  const location = useLocation();
  const navigate = useNavigate();

  const goBack = () => {
    if (location.key === "default") {
      navigate("/");
      return;
    }
    navigate(-1);
  };

  return (
    <div className="min-h-screen bg-[#F8F9FA]">
      <Sidebar />
      <header className="fixed top-0 left-0 right-0 z-30 flex h-16 items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 pl-16 md:left-[272px] md:px-7 lg:h-[84px]">
        {!workspace ? <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={goBack}
            data-testid="back-button"
            aria-label="Torna indietro"
            title="Torna indietro"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-600 transition-colors hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-300"
          >
            <ArrowLeft size={18} weight="bold" />
          </button>
          <div className="min-w-0">
            <div className="overline truncate">{subtitle || "gestionale"}</div>
            <h1 className="truncate font-heading text-lg font-black tracking-tight text-slate-900" data-testid="page-title">
              {title}
            </h1>
          </div>
        </div> : null}
        <div className={`hidden flex-1 lg:block ${workspace ? "mx-auto max-w-2xl" : "max-w-lg"}`}><GlobalSearch /></div>
        <div className="flex items-center gap-2">{actions}</div>
      </header>
      <main className="pt-16 md:ml-[272px] lg:pt-[84px]">
        <div className={`animate-fade-in-up p-4 md:p-6 ${workspace ? "lg:px-[30px] lg:py-5" : "lg:p-8"}`}>
          <div className="lg:hidden mb-4"><GlobalSearch /></div>
          {statusMode !== "hidden" ? <DataStatusBar compact={statusMode === "compact"} /> : null}
          {children}
        </div>
      </main>
    </div>
  );
}
