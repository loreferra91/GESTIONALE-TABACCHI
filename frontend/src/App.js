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
import Parametri from "./pages/Parametri";
import CassaRapida from "./pages/CassaRapida";
import Carico from "./pages/Carico";

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
          <Route path="/carico" element={<Carico />} />
          <Route path="/ordini" element={<Ordini />} />
          <Route path="/cassa" element={<Cassa />} />
          <Route path="/cassa-rapida" element={<CassaRapida />} />
          <Route path="/parametri" element={<Parametri />} />
        </Routes>
      </BrowserRouter>
      <Toaster position="top-right" richColors />
    </div>
  );
}

export default App;
