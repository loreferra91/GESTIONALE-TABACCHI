# Auto-Order 3.1: historical vending reconciliation

Older CSV imports can have no `csv_event_key`, while a later complete import has
already recorded the same sales. Counting both inflates demand; rejecting every
such product makes the operational 3.1 draft incomplete.

The comparison/confirmation path now reconciles only its forecast input against
`vending_accounting_events` rebuilt from complete CSV files. It uses the existing
CSV parser's compatibility hash: product, minute, column, price and payment.
It requires one unambiguous column, complete accounting occurrence numbers,
unique original event keys and file provenance. Multiple legitimate sales in
the same minute remain separate events. A later keyed import is included in the
same reconciliation group and is not counted twice.

Missing/partial/conflicting evidence retains the original quality guard. No
sales, inventory, cash or supplier records are updated. The response reports
matched rows, canonical events, duplicate copies, unresolved rows and affected
products. A digest of the matched original evidence participates in the policy
snapshot; confirmation rebuilds the forecast from current server data.

The normal sales cursor remains streamed. Only legacy groups and their matching
keyed rows are buffered; the rebuilt ledger is read only when needed.

Existing vending stock above a configured refill ceiling remains real stock.
The simulation consumes it normally and allows refills only after sufficient
space becomes available, capped by the configured ceiling. Invalid/missing
capacity continues to be guarded. This does not alter physical stock or limits.

Validation covers same-minute multiplicity, replayed keyed imports, absent or
conflicting evidence, unchanged inputs/database records, policy snapshot
invalidation, real route readiness and over-capacity depletion/refill bounds.
