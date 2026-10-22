import uuid

import pytest
from pymongo import MongoClient

from app import create_app
from config import Config


@pytest.fixture
def app():
    db_name = f"survey_rebuild_test_{uuid.uuid4().hex[:8]}"
    client = MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=5000)
    app = create_app(
        {
            "TESTING": True,
            "MONGO_DB": db_name,
            "CSRF_ENABLED": False,
            "SECRET_KEY": "test-secret-key",
        }
    )
    yield app
    app.extensions["mongo_client"].drop_database(db_name)
    app.extensions["mongo_client"].close()


@pytest.fixture
def db(app):
    return app.extensions["mongo_db"]


@pytest.fixture
def client(app):
    return app.test_client()
