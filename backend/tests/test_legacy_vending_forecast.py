"""Forecast reconciliation requires source evidence, never timestamp dedup alone."""
import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
from zoneinfo import ZoneInfo

import pytest
from mongomock_motor import AsyncMongoMockClient

from backend import server
from backend.forecasting.demand_models import NativeSalesAccumulator
from backend.forecasting.legacy_vending import LegacyVendingReconciler


END = date(2026, 11, 16)
KEY = server._csv_vending_compatibility_key


def fixture_data(multiplicity=2, copies=4):
    row = dict(codice='AMMS338', data='2026-10-03T12:05:00', quantita=1,
               canale='VENDING', sorgente='CSV_VENDING', importo=5.8, pagamento='CARTE')
    digest = KEY({**row, 'colonna': 'A07'})
    events = [dict(
        csv_event_key=hashlib.sha256(f'original-{i}'.encode()).hexdigest() + ':1',
        csv_event_compatibility_key=digest, csv_accounting_key=f'{digest}:{i}',
        csv_accounting_occurrence=i, data=f'2026-10-03T12:05:{i:02d}', importo=5.8,
        pagamento='CARTE', rebuilt_from_complete_csv=True, file_sha256='a' * 64,
        file_name='complete.csv',
    ) for i in range(1, multiplicity + 1)]
    rows = [{**row, 'id': f'old-{i}'} for i in range(multiplicity * copies)]
    return rows, events, [{'colonna': 'A07'}]


def run(rows, events, columns):
    before = deepcopy((rows, events, columns))
    collector = NativeSalesAccumulator(END)
    reconciler = LegacyVendingReconciler(collector, KEY, [r for r in rows if not r.get('csv_event_key')])
    for row in rows:
        reconciler.add(row)
    report = reconciler.finish(events, columns)
    totals, evidence = collector.finish()
    assert (rows, events, columns) == before
    return report, totals, evidence


def test_preserves_same_minute_multiplicity_and_is_read_only_deterministic():
    data = fixture_data()
    report, totals, evidence = run(*data)
    assert report['matched_rows'] == 8
    assert report['canonical_events'] == 2
    assert report['duplicate_copies'] == 6
    assert sum(totals['AMMS338']['VENDING']) == 2
    assert evidence['AMMS338']['issues'] == []
    assert run(*data) == run(*data)
    assert run(list(reversed(data[0])), list(reversed(data[1])), data[2]) == run(*data)


def test_later_keyed_import_is_not_counted_again():
    rows, events, columns = fixture_data()
    for i in range(2):
        rows.append({**rows[0], 'id': f'keyed-{i}', 'colonna': 'A07',
                     'csv_event_key': hashlib.sha256(f'old-minute-key-{i}'.encode()).hexdigest() + ':1'})
    report, totals, evidence = run(rows, events, columns)
    assert report['legacy_rows'] == 8
    assert report['matched_rows'] == 10
    assert report['canonical_events'] == 2
    assert report['duplicate_copies'] == 8
    assert sum(totals['AMMS338']['VENDING']) == 2
    assert not evidence['AMMS338']['issues']


@pytest.mark.parametrize('fault', [
    'no_ledger', 'provenance', 'file_hash', 'event_key', 'duplicate_event',
    'occurrence_gap', 'accounting_key', 'price', 'payment', 'timestamp',
    'quantity', 'channel', 'duplicate_id', 'missing_id', 'unknown_column',
    'ambiguous_column', 'partial_keyed', 'conflicting_code',
])
def test_uncertain_evidence_keeps_quality_guard(fault):
    rows, events, columns = fixture_data()
    if fault == 'no_ledger': events = []
    if fault == 'provenance': events[0]['rebuilt_from_complete_csv'] = False
    if fault == 'file_hash': events[0]['file_sha256'] = ''
    if fault == 'event_key': events[0]['csv_event_key'] = 'invented'
    if fault == 'duplicate_event': events.append(deepcopy(events[0]))
    if fault == 'occurrence_gap': events[0]['csv_accounting_occurrence'] = 3
    if fault == 'accounting_key': events[0]['csv_accounting_key'] = 'wrong'
    if fault == 'price': events[0]['importo'] = 8
    if fault == 'payment': events[0]['pagamento'] = 'CONTANTI'
    if fault == 'timestamp': events[0]['data'] = '2026-10-04T12:05:01'
    if fault == 'quantity': rows[0]['quantita'] = 2
    if fault == 'channel': rows[0]['canale'] = 'NEGOZIO'
    if fault == 'duplicate_id': rows[0]['id'] = rows[1]['id']
    if fault == 'missing_id': rows[0].pop('id')
    if fault == 'unknown_column': columns = [{'colonna': 'B02'}]
    if fault == 'ambiguous_column':
        columns.append({'colonna': 'B02'})
        events.append({**events[0], 'csv_event_compatibility_key': KEY({**rows[0], 'colonna': 'B02'})})
    if fault == 'partial_keyed':
        rows.append({**rows[0], 'id': 'keyed', 'colonna': 'A07', 'csv_event_key': 'b' * 64 + ':1'})
    if fault == 'conflicting_code': rows[0]['codice'] = '338'
    report, totals, evidence = run(rows, events, columns)
    assert report['matched_rows'] == 0
    assert report['unresolved_rows'] == len(rows)
    assert 'CSV_SENZA_EVENT_KEY' in evidence['AMMS338']['issues']


def test_route_uses_reconciliation_and_invalidates_snapshot_without_mutations(monkeypatch):
    db = AsyncMongoMockClient().forecast_test
    monkeypatch.setattr(server, 'db', db)
    today = datetime.now(ZoneInfo('Europe/Rome')).date()

    async def exercise():
        rows, events, columns = fixture_data()
        day = (today - timedelta(days=40)).isoformat()
        for row in rows:
            row['data'] = day + 'T12:05:00'
        digest = KEY({**rows[0], 'colonna': 'A07'})
        for i, event in enumerate(events, 1):
            event.update(data=f'{day}T12:05:{i:02d}', csv_event_compatibility_key=digest,
                         csv_accounting_key=f'{digest}:{i}')
        await db.vendite.insert_many(rows + [
            dict(id=f'shop-{i}', codice='AMMS338', data=(today - timedelta(days=i)).isoformat(),
                 quantita=2, canale='NEGOZIO', sorgente='MANUALE') for i in range(90)
        ])
        await db.vending.insert_one({**columns[0], 'codice': 'AMMS338', 'capacita_max': 20})
        await db.vending_accounting_events.insert_many(events)
        ao = dict(planning_day=today.isoformat(), snapshot_key='inventory', data_quality='DATI_AFFIDABILI',
                  parametri={'AUTO_ORDER_3_1_ENABLED': 1, 'AUTO_ORDER_WEEKLY_FRIDAY': 1},
                  righe=[dict(codice='AMMS338', giacenza_negozio=2, giacenza_vending=0,
                              lotto_ordine=10, qta_da_ordinare=20)], esclusi=[])
        before = await db.vendite.find({}, {'_id': 0}).to_list(None)
        result = await server.auto_order_module['build_comparison'](ao)
        assert result['operational_ready'], result['operational_blockers']
        assert result['rows'][0]['quantita_suggerita'] > 0
        assert result['legacy_vending_reconciliation']['canonical_events'] == 2
        assert await db.vendite.find({}, {'_id': 0}).to_list(None) == before
        again = await server.auto_order_module['build_comparison'](ao)
        assert result['policy_snapshot_key'] == again['policy_snapshot_key']
        await db.vending_accounting_events.update_one({}, {'$set': {'file_sha256': 'b' * 64}})
        different_source = await server.auto_order_module['build_comparison'](ao)
        assert different_source['rows'] == result['rows']
        assert different_source['policy_snapshot_key'] != result['policy_snapshot_key']
        await db.vending_accounting_events.update_one({}, {'$set': {'rebuilt_from_complete_csv': False}})
        broken = await server.auto_order_module['build_comparison'](ao)
        assert not broken['operational_ready']
        assert broken['policy_snapshot_key'] != result['policy_snapshot_key']
        assert 'CSV_SENZA_EVENT_KEY' in broken['rows'][0]['anomalie']
    asyncio.run(exercise())
