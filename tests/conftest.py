"""
Shared test configuration and fixtures.

Uses an in-memory SQLite DB (separate from the production DB) for test isolation.
"""

import pytest

from app.database import Base, engine


@pytest.fixture(scope="session", autouse=True)
def create_test_tables():
    """Create all tables once at session start."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
