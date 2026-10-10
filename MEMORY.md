# MEMORY.md - Durable Project Decisions

## Gestionale Tabacchi

- Dal 2026-10-10 il **Prelievo da vending** contabile è netto degli scontrini:
  `prelievo netto = totale prelievi registrati - totale scontrini vending`.
  Esempio di accettazione: `3.527,05 € - 327,90 € = 3.199,15 €`.
- La giacenza fisica della vending continua a sottrarre il prelievo lordo
  registrato; il saldo combinato di negozio e vending usa invece il prelievo
  netto. Questa decisione sostituisce la regola annotata il 2026-10-07 che
  lasciava gli scontrini separati anche dal prelievo e dal saldo combinato.
