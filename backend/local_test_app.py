"""Applicazione locale isolata con MongoDB simulato in memoria."""

import os

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_auto_order_local_test")
os.environ.pop("APP_USERNAME", None)
os.environ.pop("APP_PASSWORD", None)

from mongomock_motor import AsyncMongoMockClient

from backend import server


mock_client = AsyncMongoMockClient()
server.client = mock_client
server.db = mock_client[os.environ["DB_NAME"]]
app = server.app
