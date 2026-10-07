import { ArrowLeft } from "@phosphor-icons/react";
import { useLocation, useNavigate } from "react-router-dom";
import Sidebar from "./Sidebar";
import DataStatusBar from "./DataStatusBar";
import GlobalSearch from "./GlobalSearch";

export default function Layout({ children, title, subtitle, actions, statusMode = "full" }) {
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
      <header className="fixed top-0 left-0 md:left-64 right-0 h-16 bg-white border-b border-slate-200 z-30 flex items-center justify-between gap-3 px-4 pl-16 md:px-6">
        <div className="flex min-w-0 items-center gap-3">
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
        </div>
        <div className="hidden lg:block flex-1 max-w-lg"><GlobalSearch /></div>
        <div className="flex items-center gap-2">{actions}</div>
      </header>
      <main className="md:ml-64 pt-16">
        <div className="p-4 md:p-6 lg:p-8 animate-fade-in-up">
          <div className="lg:hidden mb-4"><GlobalSearch /></div>
          <DataStatusBar compact={statusMode === "compact"} />
          {children}
        </div>
      </main>
    </div>
  );
}
