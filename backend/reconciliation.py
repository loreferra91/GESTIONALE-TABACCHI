"""Read-only operational checks; never infer stock or cash from unrelated histories."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal

if __package__:
    from .money import money_decimal
else:
    from money import money_decimal


async def reconcile(database, payment_bucket, detail_limit=100):
    findings = []
    counts = Counter()
    scanned = Counter()

    def issue(kind, collection, document, message, **values):
        counts[kind] += 1
        if len(findings) < detail_limit:
            findings.append({
                "tipo": kind, "raccolta": collection,
                "riferimento": str(document.get("id") or document.get("codice") or document.get("_id") or ""),
                "messaggio": message, **values,
            })

    def amount(collection, doc, field, signed=False):
        try:
            value = money_decimal(doc.get(field), nonnegative=not signed)
            raw = Decimal(str(doc[field]).strip().replace(",", "."))
            # Ignore harmless binary-double tails, not real fractions of a cent.
            if abs(raw - value) > Decimal("0.000000001"):
                issue("FRAZIONI_CENTESIMO", collection, doc,
                      f"{field}: valore storico con frazioni di centesimo; verificare la fonte.",
                      campo=field, valore=str(doc[field]), normalizzato=float(value))
            return value
        except (ValueError, TypeError):
            issue("IMPORTO_INVALIDO", collection, doc,
                  f"{field}: importo mancante, non numerico, non finito o fuori limite.", campo=field)
            return None

    def stock(collection, doc, field):
        raw = doc.get(field, 0)
        try:
            value = Decimal(str(raw))
            if isinstance(raw, bool) or not value.is_finite() or value < 0 or value != value.to_integral_value():
                raise ValueError()
            return int(value)
        except (ValueError, ArithmeticError):
            issue("GIACENZA_INVALIDA", collection, doc, f"{field}: la giacenza deve essere intera e non negativa.")
            return None

    products = defaultdict(list)
    async for doc in database.prodotti.find({}):
        scanned["prodotti"] += 1
        amount("prodotti", doc, "prezzo")
        stock("prodotti", doc, "giacenza_negozio")
        value = stock("prodotti", doc, "giacenza_vending")
        products[str(doc.get("codice") or "").strip()].append((doc, value))
    for code, candidates in products.items():
        if len(candidates) > 1:
            issue("CODICE_PRODOTTO_DUPLICATO", "prodotti", candidates[0][0],
                  f"Codice {code} presente in {len(candidates)} prodotti: confronto giacenze non eseguito.")
    columns = defaultdict(list)
    async for doc in database.vending.find({}):
        scanned["vending"] += 1
        value = stock("vending", doc, "giacenza")
        code = str(doc.get("codice") or "").strip()
        if code:
            columns[code].append(value)
            if code not in products:
                issue("COLONNA_ORFANA", "vending", doc, f"Il prodotto {code} associato alla colonna non esiste.")
    compared_products = 0
    for code, values in columns.items():
        candidates = products.get(code, [])
        # Ambiguous or malformed data cannot establish an expected quantity.
        if len(candidates) != 1 or None in values or candidates[0][1] is None:
            continue
        doc, saved = candidates[0]
        expected = sum(values)
        compared_products += 1
        if saved != expected:
            issue("GIACENZA_VENDING", "prodotti", doc,
                  f"{code}: giacenza prodotto {saved}, somma colonne {expected}.",
                  registrato=saved, atteso=expected, differenza=saved - expected)

    events = defaultdict(list)
    async for doc in database.vending_accounting_events.find({}):
        scanned["vending_accounting_events"] += 1
        value = amount("vending_accounting_events", doc, "importo")
        for field in ("csv_event_key", "csv_accounting_key"):
            if isinstance(doc.get(field), str) and doc[field]:
                key = (field, doc[field])
                if events[key]:
                    issue("IDENTITA_CSV_DUPLICATA", "vending_accounting_events", doc,
                          f"Identità {field} presente in più eventi: confronto ambiguo.")
                events[key].append((doc, value))
    compared_sales = unmatched_sales = 0
    async for doc in database.vendite.find({}):
        scanned["vendite"] += 1
        value = amount("vendite", doc, "importo")
        if doc.get("sorgente") != "CSV_VENDING":
            continue
        matches = []
        for field in ("csv_event_key", "csv_accounting_key"):
            if isinstance(doc.get(field), str) and events.get((field, doc[field])):
                matches = events[(field, doc[field])]
                break
        if len(matches) != 1:
            unmatched_sales += 1
            continue
        event, expected = matches[0]
        if value is None or expected is None:
            continue
        compared_sales += 1
        if value != expected or payment_bucket(doc.get("pagamento")) != payment_bucket(event.get("pagamento")):
            issue("IMPORTO_PAGAMENTO_CSV", "vendite", doc,
                  "La vendita e lo stesso evento contabile hanno importo o pagamento diversi.",
                  registrato=float(value), atteso=float(expected), differenza=float(value - expected),
                  pagamento_vendita=doc.get("pagamento"), pagamento_registro=event.get("pagamento"))

    for name, field, signed in (
        ("versamenti", "importo", False), ("prelievi_vending", "importo", False),
        ("scontrini_vending", "importo", False), ("cassa", "importo", True),
        ("storico_ordini", "prezzo", False), ("listino_adm", "prezzo", False),
        ("rettifiche_venduti", "importo_contabile", True),
        ("db_storico_vend", "importo", True),
    ):
        async for doc in database[name].find({}):
            scanned[name] += 1
            if name == "rettifiche_venduti" and field not in doc:
                continue  # Legacy corrections deliberately have no monetary effect.
            amount(name, doc, field, signed)

    async for doc in database.vending_accounting_state.find({}):
        scanned["vending_accounting_state"] += 1
        for bucket, value in (doc.get("baseline_importi_pagamenti") or {}).items():
            amount("vending_accounting_state", {**doc, "importo": value}, "importo")
    async for doc in database.parametri.find({"nome": "GIACENZA_INIZIALE_CONTANTI_VENDING"}):
        scanned["parametri"] += 1
        amount("parametri", doc, "valore")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "esito": "da_verificare" if counts else "nessuna_discrepanza_rilevata",
        "totale_segnalazioni": sum(counts.values()), "conteggi": dict(counts),
        "dettagli": findings, "dettagli_omessi": sum(counts.values()) - len(findings),
        "documenti_esaminati": dict(scanned), "prodotti_confrontati": compared_products,
        "vendite_csv_confrontate": compared_sales, "vendite_csv_non_confrontabili": unmatched_sales,
        "copertura": [
            "Importi di prodotti, listino, vendite, ordini storici, movimenti, rettifiche, storico negozio, baseline contabile e fondo iniziale vending.",
            "Giacenze non negative e intere; cache vending confrontata con le colonne associate.",
            "Importo e pagamento delle vendite CSV con un evento contabile univoco.",
        ],
        "limiti": [
            "Controllo in sola lettura, non certificazione contabile. Ripetere a operazioni ferme: le letture non sono una fotografia transazionale.",
            "Le giacenze si confrontano solo per prodotti univoci con colonne associate; nessuna formula acquistati meno venduti.",
            "Vendite CSV senza un evento univoco non sono errori: possono appartenere alla baseline storica. Eventi senza vendita sono ammessi dall'allineamento solo contabile.",
            "Non si confronta la cassa tecnica legacy con i saldi: baseline e copertura temporale non sono equivalenti. Non è un conteggio fisico del contante.",
        ],
    }
