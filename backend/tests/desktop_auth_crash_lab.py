"""CI-only authenticated MongoDB replica test, including abrupt restart.

Invoked in two phases by GitHub Actions against a separately prepared
keyfile-authenticated disposable container. Never run against production.
"""
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from pymongo import MongoClient
from pymongo.errors import OperationFailure

from desktop_runtime.mongo_auth import bootstrap_first_users, authenticated_uri, verify_authenticated_access

URI = "mongodb://127.0.0.1:27020/?directConnection=true"
REP = "rsDesktopAuthCert"
DB = "desktop_auth_cert_test"
ADMIN_SECRET = "ci-only-admin-secret-mongodb-not-production-1234567"
APP_SECRET = "ci-only-app-secret-mongodb-not-production-7654321"


def guard():
    if os.environ.get("CERT_DESKTOP_AUTH_ALLOW_TEST") != "1":
        raise RuntimeError("Auth lab is not enabled")
    if os.environ.get("CERT_AUTH_MONGO_URL") != URI:
        raise RuntimeError("Auth lab refuses any URL except fixed disposable loopback target")


def initialize():
    client = MongoClient(URI, serverSelectionTimeoutMS=3000)
    try:
        authenticated = bootstrap_first_users(
            client, replica_set=REP, database=DB,
            admin_password=ADMIN_SECRET, app_password=APP_SECRET,
            member_host="127.0.0.1:27020")
    finally:
        client.close()
    auth = MongoClient(authenticated, serverSelectionTimeoutMS=3000)
    try:
        with auth.start_session() as session:
            with session.start_transaction():
                auth[DB]["crash_receipts"].insert_one(
                    {"_id": "pending-auth-test", "status": "PENDING", "amount_cents": 1234},
                    session=session)
                auth[DB]["crash_receipts"].insert_one(
                    {"_id": "staged-auth-test", "status": "STAGED", "amount_cents": 5678},
                    session=session)
        auth[DB]["crash_receipts"].create_index("status")
        assert auth[DB]["crash_receipts"].count_documents({}) == 2
    finally:
        auth.close()


def after_crash():
    uri = authenticated_uri(27020, DB, APP_SECRET, REP)
    verify_authenticated_access(uri, expected_replica=REP, database=DB)
    auth = MongoClient(uri, serverSelectionTimeoutMS=3000)
    anonymous = MongoClient(URI, serverSelectionTimeoutMS=3000)
    try:
        rows = {r["_id"]: r for r in auth[DB]["crash_receipts"].find({})}
        assert rows["pending-auth-test"]["status"] == "PENDING"
        assert rows["staged-auth-test"]["amount_cents"] == 5678
        assert "status_1" in auth[DB]["crash_receipts"].index_information()
        try:
            anonymous[DB]["crash_receipts"].find_one()
            raise AssertionError("Unauthenticated data read was allowed")
        except OperationFailure as exc:
            assert exc.code == 13 or "auth" in str(exc).lower()
        # Recovery transaction can commit after the hard crash.
        with auth.start_session() as session:
            with session.start_transaction():
                auth[DB]["crash_receipts"].update_one(
                    {"_id": "pending-auth-test", "status": "PENDING"},
                    {"$set": {"recovery_verified": True}}, session=session)
        assert auth[DB]["crash_receipts"].find_one(
            {"_id": "pending-auth-test"})["recovery_verified"] is True
    finally:
        anonymous.close()
        auth.close()


if __name__ == "__main__":
    guard()
    if len(sys.argv) != 2 or sys.argv[1] not in {"initialize", "verify"}:
        raise SystemExit("Usage: python -m backend.tests.desktop_auth_crash_lab initialize|verify")
    (initialize if sys.argv[1] == "initialize" else after_crash)()
    print("AUTHENTICATED CRASH RECOVERY CERT: PASS")
