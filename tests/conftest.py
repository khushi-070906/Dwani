"""Shared test setup.

The online app caps new conversations per client address (api.NEW_PER_MINUTE). That counter lives in a module-level
dict, so without this every test shares one "address" and the twenty-first session in a run is refused -- a test fails
because of the tests that ran before it. Each test starts with an empty counter instead.
"""
import pytest


@pytest.fixture(autouse=True)
def _fresh_rate_limit_counters():
    from dwaniforms import api
    api._new_by_ip.clear()
    yield
    api._new_by_ip.clear()
