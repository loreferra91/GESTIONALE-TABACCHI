"""Compatibility with mongomock 4.3, not a change to the production driver.

PyMongo >=4.11 passes sort=None for bulk updates. mongomock has not yet
added that optional argument. Never emulate a non-None sort silently.
"""
import inspect
import os

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from mongomock.collection import BulkOperationBuilder


@pytest.fixture(autouse=True)
def mongomock_optional_bulk_sort(monkeypatch):
    original = BulkOperationBuilder.add_update
    if "sort" in inspect.signature(original).parameters:
        return

    def add_update(self, *args, sort=None, **kwargs):
        if sort is not None:
            raise NotImplementedError("mongomock does not support sorted bulk updates")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(BulkOperationBuilder, "add_update", add_update)
