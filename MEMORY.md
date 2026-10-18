# MEMORY.md - Durable Project Decisions

## Gestionale Tabacchi

- Dal 2026-10-10 il **Prelievo da vending** contabile è netto degli scontrini:
  `prelievo netto = totale prelievi registrati - totale scontrini vending`.
  Esempio di accettazione: `3.527,05 € - 327,90 € = 3.199,15 €`.
- La giacenza fisica della vending continua a sottrarre il prelievo lordo
  registrato; il saldo combinato di negozio e vending usa invece il prelievo
  netto. Questa decisione sostituisce la regola annotata il 2026-10-07 che
  lasciava gli scontrini separati anche dal prelievo e dal saldo combinato.
- Dal 2026-10-14 ogni carico merce incrementa anche il saldo Smart Venue dei
  prodotti gia presenti in Smart Venue, oltre a `giacenza_negozio` e
  `acquistati`. Il carico Auto-Order del 12/10/2026 è stato riallineato con una
  migrazione idempotente di 27 righe/810 pezzi e backup preventivo.
- Dal 2026-10-15 i carichi merce bulk sono idempotenti tramite
  `ricezioni_merce.idempotency_key`; ogni riga Smart Venue conserva nello
  storico ordini una conferma esterna separata, che non modifica le scorte.
  La pagina **GIACENZE REALI** usa `prodotti.giacenza_negozio` e la somma delle
  colonne `vending`, registra le correzioni in `rettifiche_inventario` senza
  creare vendite, e offre le stampe A4 inventario in bianco e giacenze attuali.
- Dal 2026-10-15 la contabilità CSV vending è un registro incrementale: ogni
  export mobile aggiunge soltanto gli eventi nuovi tramite `csv_event_key` e
  non elimina più le vendite uscite dalla finestra delle ultime 1.000 righe.
  I backup MongoDB interni hanno retention di 3 copie e gli import Excel usano
  snapshot parziali delle sole collezioni modificate, per restare entro Atlas M0.
- Dal 2026-10-16 nella pagina **Prodotti** il totale Venduti apre un editor in
  cui è modificabile soltanto `venduti_negozio`; `venduti_vending` resta in sola
  lettura e il totale è calcolato. Le correzioni sono protette da confronto col
  valore atteso, non cambiano le giacenze e vengono registrate in
  `rettifiche_venduti`.
- Dal 2026-10-18 l'integrità vendite è fail-closed: le vendite manuali usano una
  chiave idempotente e non possono portare la scorta sotto zero; bulk e CSV
  rifiutano l'intero caricamento se una riga, un prodotto o una colonna non è
  valido; gli annullamenti sono vincolati alla sorgente e transazionali su
  MongoDB. I backup non hanno più il limite di 25.000 documenti, includono
  checksum e lock di restore. In produzione autenticazione completa e CORS non
  wildcard sono requisiti di avvio.
