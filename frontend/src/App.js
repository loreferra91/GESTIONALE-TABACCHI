import "./App.css";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "sonner";
import Dashboard from "./pages/Dashboard";
import Prodotti from "./pages/Prodotti";
import Listino from "./pages/Listino";
import Magazzino from "./pages/Magazzino";
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

function App() {
  return (
    <div className="App">
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/prodotti" element={<Prodotti />} />
          <Route path="/listino" element={<Listino />} />
          <Route path="/magazzino" element={<Magazzino />} />
          <Route path="/vending" element={<Vending />} />
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
  );
}

export default App;
