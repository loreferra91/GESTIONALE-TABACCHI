# God Services — Gestionale Tabacchi

## Problem statement
"trasformalo in un gestionale" — trasformare un file Excel .xlsm di gestione tabaccheria italiana (con distributore vending) in un web gestionale completo.

## Stack
FastAPI + MongoDB (motor) + React (CRA) + Tailwind + Shadcn UI + Phosphor icons.

## User personas
- Titolare tabaccheria (single-user, no login).

## Core requirements
- Prodotti (CRUD + ricerca/filtro categoria)
- Listino ADM (4104 articoli, ricerca)
- Magazzino/Pivot (KPI, stato OK/ESAURITO/FERMO/LENTO)
- Vending: 83 colonne (A01…M12) con giacenza/capacità/soglia, ricarica
- Vendite giornaliere (auto-scala giacenza + venduti per canale)
- Auto-Order (SOGLIA 35%, LOTTO per categoria, FAST/SLOW mover)
- Storico ordini (con inserimento manuale + generato da auto-order)
- Cassa (entrate/uscite con saldo)
- Parametri configurabili
- Import Excel (RIEP_VENDITA sheet)

## Implementato (2026-02-18)
- Backend: /api/prodotti, /listino, /vendite, /vending (+ricarica), /ordini, /cassa, /parametri, /auto-order (+conferma), /pivot, /dashboard, /import/excel
- Seed automatico da seed_data.json: 227 prodotti, 4104 listino ADM, 83 colonne vending, 594 ordini storici, 11 parametri
- Frontend: 10 pagine con sidebar dark accent, KPI cards, tabelle dense, griglia vending color-coded, forms inline

## Backlog / Next
- P1: Export PDF/Excel ordini
- P1: Report vendite settimanali/mensili (grafici)
- P2: Multi-utente con ruoli
- P2: Notifiche riordino via email
