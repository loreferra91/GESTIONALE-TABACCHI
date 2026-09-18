import Sidebar from "./Sidebar";

export default function Layout({ children, title, subtitle, actions }) {
  return (
    <div className="min-h-screen bg-[#F8F9FA]">
      <Sidebar />
      <header className="fixed top-0 left-0 md:left-64 right-0 h-16 bg-white border-b border-slate-200 z-30 flex items-center justify-between px-6">
        <div>
          <div className="overline">{subtitle || "gestionale"}</div>
          <h1 className="font-heading text-lg font-black tracking-tight text-slate-900" data-testid="page-title">
            {title}
          </h1>
        </div>
        <div className="flex items-center gap-2">{actions}</div>
      </header>
      <main className="md:ml-64 pt-16">
        <div className="p-6 lg:p-8 animate-fade-in-up">{children}</div>
      </main>
    </div>
  );
}
