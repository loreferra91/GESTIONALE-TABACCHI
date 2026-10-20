import React from "react";
import "./App.css";
import { BrowserRouter, Navigate, Routes, Route } from "react-router-dom";
import { Toaster } from "sonner";
import Dashboard from "./pages/Dashboard";
import Prodotti from "./pages/Prodotti";
import Listino from "./pages/Listino";
import Vending from "./pages/Vending";
import Vendite from "./pages/Vendite";
import AutoOrder from "./pages/AutoOrder";
import Ordini from "./pages/Ordini";
import Cassa from "./pages/Cassa";
import Versamenti from "./pages/Versamenti";
import PrelieviVending from "./pages/PrelieviVending";
import ScontriniVending from "./pages/ScontriniVending";
import Parametri from "./pages/Parametri";
import CassaRapida from "./pages/CassaRapida";
import Carico from "./pages/Carico";
import Anomalie from "./pages/Anomalie";
import Report from "./pages/Report";
import Contabilita from "./pages/Contabilita";
import SmartVenue from "./pages/SmartVenue";
import SmartVenueControllo from "./pages/SmartVenueControllo";
import GiacenzeReali from "./pages/GiacenzeReali";

class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("Errore non gestito nell'interfaccia", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="min-h-screen bg-slate-50 p-6 flex items-center justify-center">
        <section className="w-full max-w-xl rounded-lg border border-red-200 bg-white p-6 shadow-sm">
          <p className="text-xs font-bold uppercase tracking-wider text-red-700">Errore interfaccia</p>
          <h1 className="mt-2 text-xl font-bold text-slate-900">La pagina non può essere visualizzata</h1>
          <p className="mt-2 text-sm text-slate-600">Ricarica la pagina. Se il problema continua, comunica questo dettaglio:</p>
          <pre className="mt-3 overflow-auto rounded bg-slate-900 p-3 text-xs text-white">{String(this.state.error?.message || this.state.error)}</pre>
          <button type="button" onClick={() => window.location.reload()} className="mt-4 rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white">
            Ricarica pagina
          </button>
        </section>
      </main>
    );
  }
}

function App() {
  return (
    <AppErrorBoundary>
      <div className="App">
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/prodotti" element={<Prodotti />} />
          <Route path="/listino" element={<Listino />} />
          <Route path="/magazzino" element={<Navigate to="/giacenze-reali?tab=analisi" replace />} />
          <Route path="/vending" element={<Vending />} />
          <Route path="/smart-venue" element={<SmartVenue />} />
          <Route path="/smart-venue-controllo" element={<SmartVenueControllo />} />
          <Route path="/giacenze-reali" element={<GiacenzeReali />} />
          <Route path="/vendite" element={<Vendite />} />
          <Route path="/auto-order" element={<AutoOrder />} />
          <Route path="/anomalie" element={<Anomalie />} />
          <Route path="/carico" element={<Carico />} />
          <Route path="/ordini" element={<Ordini />} />
          <Route path="/report" element={<Report />} />
          <Route path="/contabilita" element={<Contabilita />} />
          <Route path="/cassa" element={<Cassa />} />
          <Route path="/versamenti" element={<Versamenti />} />
          <Route path="/prelievi-vending" element={<PrelieviVending />} />
          <Route path="/scontrini-vending" element={<ScontriniVending />} />
          <Route path="/cassa-rapida" element={<CassaRapida />} />
          <Route path="/parametri" element={<Parametri />} />
        </Routes>
      </BrowserRouter>
      <Toaster position="top-right" richColors />
      </div>
    </AppErrorBoundary>
  );
}

export default App;
