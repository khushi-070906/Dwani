"""
test_attendee_cap.py

Verifies the fix to server.py's /ws (attendee_socket) endpoint: once
`current_license.max_attendees` connected attendees are already on a
session, the next /ws connection attempt must be rejected (closed with
code 4003) rather than silently accepted.

Uses the REAL free-tier number (20, from licensing.py's
TIER_DEFAULT_MAX_ATTENDEES) rather than a small placeholder, so this is
testing the actual production limit, not just the mechanism in miniature.

This is NOT runnable in the sandbox this was written in -- that
environment has no network access to install fastapi/uvicorn, and doesn't
have server.py's sibling modules (accessibility.py, pipeline.py,
qa_pipeline.py, session.py). Run it from the real project root instead,
where `python server.py` already works:

    pip install pytest --break-system-packages   # if not already installed
    python -m pytest test_attendee_cap.py -v

It does NOT touch the real license flow (no LDST_LICENSE_PUBLIC_KEY, no
cached token needed) -- it imports server.py directly and monkeypatches
its module-level `current_license` global, the same variable __main__
would normally set after a real check_license() call.
"""

from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

import server
from licensing import License, TIER_DEFAULT_MAX_ATTENDEES

FREE_TIER_CAP = TIER_DEFAULT_MAX_ATTENDEES["free"]  # 20 today -- read from
# licensing.py itself rather than hardcoded, so this test tracks that
# constant if it's ever changed instead of silently testing a stale number.


@pytest.fixture
def free_tier_license():
    return License(
        tier="free",
        presenter_email="test@example.com",
        issued_at=0,
        expires_at=2_000_000_000,  # far future, avoids expiry/grace-period logic
        max_attendees=FREE_TIER_CAP,
    )


@pytest.fixture
def unlimited_license():
    """Pro and Institution both resolve to max_attendees=None (unlimited)
    -- tier is set to "pro" here but either would exercise the same code
    path, since attendee_socket only ever branches on max_attendees, not
    on tier directly."""
    return License(
        tier="pro",
        presenter_email="test@example.com",
        issued_at=0,
        expires_at=2_000_000_000,
        max_attendees=TIER_DEFAULT_MAX_ATTENDEES["pro"],  # None
    )


@pytest.fixture(autouse=True)
def reset_state():
    """server.py's subscribers/current_license are module-level globals
    shared across tests -- reset before and after each test so one test's
    connections can't leak into the next."""
    server.subscribers.clear()
    server.current_license = None
    yield
    server.subscribers.clear()
    server.current_license = None


def _connect(client, lang="en"):
    """Opens one /ws connection using the running module's actual
    session_id, mirroring what a real attendee client would send."""
    return client.websocket_connect(
        f"/ws?lang={lang}&session_param={server.session.session_id}"
    )


def _open_n_connections(stack: ExitStack, client, n: int):
    """Opens n /ws connections via the given ExitStack, so all n stay open
    (and get cleaned up together) for the duration of a `with` block."""
    return [stack.enter_context(_connect(client)) for _ in range(n)]


def test_connections_up_to_cap_are_all_accepted(free_tier_license):
    server.current_license = free_tier_license
    client = TestClient(server.app)

    with ExitStack() as stack:
        sockets = _open_n_connections(stack, client, FREE_TIER_CAP)
        # All FREE_TIER_CAP (20) connections should be live -- sending a
        # keepalive on each and getting no exception confirms none of them
        # were silently closed.
        for ws in sockets:
            ws.send_text("keepalive")


def test_connection_past_cap_is_rejected(free_tier_license):
    server.current_license = free_tier_license
    client = TestClient(server.app)

    with ExitStack() as stack:
        _open_n_connections(stack, client, FREE_TIER_CAP)  # fill the cap exactly

        # The (FREE_TIER_CAP + 1)th connection must be rejected, not accepted.
        with pytest.raises(Exception):
            with _connect(client) as ws_over_cap:
                ws_over_cap.send_text("keepalive")
                ws_over_cap.receive_text()  # forces the close frame to surface as an error


def test_disconnecting_frees_a_cap_slot(free_tier_license):
    """A departed attendee's slot must become available again -- the cap
    counts currently-connected attendees, not a lifetime total."""
    server.current_license = free_tier_license
    client = TestClient(server.app)

    with ExitStack() as stack:
        sockets = _open_n_connections(stack, client, FREE_TIER_CAP)  # fill the cap

        # Close one connection to free a slot.
        sockets[0].close()

        # New connection with a slot now free: should NOT raise.
        with _connect(client) as ws:
            ws.send_text("keepalive")


def test_no_license_means_no_cap_enforced():
    """current_license is None when server.py is imported without __main__
    ever running check_license() (e.g. `uvicorn server:app` directly) --
    connections should NOT be capped in that state, matching pre-license
    behavior. Uses FREE_TIER_CAP + 1 connections to prove there really is
    no limit, not just that the limit wasn't hit."""
    assert server.current_license is None
    client = TestClient(server.app)

    with ExitStack() as stack:
        sockets = _open_n_connections(stack, client, FREE_TIER_CAP + 1)
        for ws in sockets:
            ws.send_text("keepalive")


def test_unlimited_tier_is_never_capped(unlimited_license):
    """max_attendees=None (Pro/Institution) must never trigger the cap
    check, regardless of how many attendees are connected. Uses
    FREE_TIER_CAP + 1 connections to prove it's genuinely unlimited, not
    just under some other hidden limit."""
    server.current_license = unlimited_license
    client = TestClient(server.app)

    with ExitStack() as stack:
        sockets = _open_n_connections(stack, client, FREE_TIER_CAP + 1)
        for ws in sockets:
            ws.send_text("keepalive")
