"""
Restricts anyio's pytest plugin to the asyncio backend.

Without this, anyio's pytest plugin parametrizes every `@pytest.mark.anyio`
test across every backend it can detect -- asyncio *and* trio -- and fails
outright on the trio parametrization if trio isn't installed. Nothing in
this project uses or needs trio (pipeline.py, server.py, and the tests are
all plain `asyncio`), so trio has no reason to be a test dependency at all.
"""

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"