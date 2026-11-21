"""B2 freshness checks are entirely synthetic: no Atlas, AWS or B2 access."""
from datetime import datetime, timedelta, timezone
import json

from scripts import b2_backup_monitor as monitor

NOW = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)
ENDPOINT = "https://s3.eu-central-003.backblazeb2.com"
BUCKET = "safe-cert-bucket"
PREFIX = "gestionale/staging"
FILENAME = "gestionale_staging-20261121T080000Z-aabbccddeeff.archive.gz.gpg"


def _manifest(*, verified_at=None, database="gestionale_staging"):
    return {
        "filename": FILENAME,
        "sha256": "a" * 64,
        "bytes": 4096,
        "database": database,
        "status": "encrypted",
        "offcluster_verified": True,
        "offcluster_destination": f"s3://{BUCKET}/{PREFIX}/{FILENAME}",
        "offcluster_verified_at": (
            verified_at or NOW - timedelta(hours=1)
        ).isoformat(),
        "created_at": (NOW - timedelta(hours=2)).isoformat(),
        "restore_certified": False,
    }


def _run(manifest=None, *, head_size=4096, network_error=False):
    calls = []

    def fake_aws(args, *, endpoint):
        calls.append((args, endpoint))
        if network_error:
            raise RuntimeError("Simulated no connection")
        if args[0] == "s3":
            return json.dumps(manifest or _manifest())
        if args[0] == "s3api":
            return json.dumps({"ContentLength": head_size})
        raise AssertionError("Unexpected command")

    return calls, fake_aws


def _check(aws_call, **overrides):
    return monitor.check_remote_backup(
        db_name="gestionale_staging", bucket=BUCKET,
        prefix=PREFIX, endpoint=ENDPOINT, now=NOW,
        aws_call=aws_call, **overrides,
    )


def test_verified_recent_remote_archive_is_healthy():
    calls, fake = _run()
    result = _check(fake)
    assert result["backup_fresh"] is True
    assert result["archive_present"] is True
    assert result["archive_size_verified"] is True
    assert result["restore_certified"] is False
    assert result["reason"] == "ok"
    assert len(calls) == 2
    assert calls[0][0][-1] == "--only-show-errors"
    assert calls[1][0][:2] == ["s3api", "head-object"]


def test_stale_manifest_rejected_without_remote_head():
    calls, fake = _run(_manifest(verified_at=NOW - timedelta(hours=37)))
    result = _check(fake)
    assert result["backup_fresh"] is False
    assert result["reason"] == "stale_or_future_manifest"
    assert len(calls) == 1


def test_archive_size_mismatch_fails_closed():
    _, fake = _run(head_size=2048)
    result = _check(fake)
    assert result["backup_fresh"] is False
    assert result["archive_size_verified"] is False


def test_manifest_cannot_point_to_other_location_or_database():
    for mutation in (
        {"database": "gestionale"},
        {"offcluster_destination": "s3://another/private/archive.gz.gpg"},
        {"filename": "../attack.archive.gz.gpg"},
        {"sha256": "invalid"},
        {"offcluster_verified": False},
    ):
        doc = {**_manifest(), **mutation}
        calls, fake = _run(doc)
        result = _check(fake)
        assert result["backup_fresh"] is False
        assert len(calls) == 1


def test_errors_are_generic_and_fail_closed():
    _, fake = _run(network_error=True)
    result = _check(fake)
    assert result["backup_fresh"] is False
    assert result["reason"] == "verification_failed"
    assert "connection" not in str(result).lower()


def test_rejects_non_official_endpoint_without_network():
    calls, fake = _run()
    result = monitor.check_remote_backup(
        db_name="gestionale_staging", bucket=BUCKET, prefix=PREFIX,
        endpoint="https://untrusted.example.com", aws_call=fake,
    )
    assert result["backup_fresh"] is False
    assert calls == []


def test_future_dated_manifest_rejected():
    calls, fake = _run(_manifest(verified_at=NOW + timedelta(hours=1)))
    result = _check(fake)
    assert result["backup_fresh"] is False
    assert len(calls) == 1
