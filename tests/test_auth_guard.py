"""Wrong-password limit and the response headers (auth_guard.py)."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import auth_guard as ag  # noqa: E402


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def test_the_ninth_wrong_try_in_a_row_is_refused_until_the_oldest_ages_out():
    clk = Clock(); lim = ag.FailureLimiter(limit=8, window=900, clock=clk)
    for _ in range(8):
        assert lim.retry_after("a@b.com") == 0
        lim.record_failure("a@b.com"); clk.t += 1
    wait = lim.retry_after("a@b.com")
    assert 800 < wait <= 900, wait
    clk.t += wait
    assert lim.retry_after("a@b.com") == 0           # the first failure has aged out


def test_one_account_being_guessed_does_not_block_another():
    clk = Clock(); lim = ag.FailureLimiter(limit=2, window=60, clock=clk)
    lim.record_failure("a"); lim.record_failure("a")
    assert lim.retry_after("a") and lim.retry_after("b") == 0


def test_a_correct_login_clears_the_count():
    lim = ag.FailureLimiter(limit=2, window=60, clock=Clock())
    lim.record_failure("a"); lim.record_failure("a"); lim.clear("a")
    assert lim.retry_after("a") == 0


def test_the_table_cannot_grow_without_end():
    clk = Clock(); lim = ag.FailureLimiter(limit=1, window=900, max_keys=50, clock=clk)
    for i in range(500):
        lim.record_failure(f"k{i}")
    assert len(lim._fails) <= 50


def test_the_login_checks_the_limit_before_any_password_and_records_each_failure():
    src = (ROOT / "license_server.py").read_text(encoding="utf-8")
    body = src[src.index('@app.post("/login")'):src.index('@app.post("/logout")')]
    assert body.index("retry_after") < body.index("verify_password"), "the limit must be checked before a password is tried"
    assert "429" in body and "Retry-After" in body
    assert body.count("record_failure") == 2 and "LOGIN_PER_EMAIL.clear" in body
    assert "auth_guard.SECURITY_HEADERS" in src and '@app.middleware("http")' in src


def test_the_headers_forbid_framing_and_type_guessing():
    h = ag.SECURITY_HEADERS
    assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff" and "Referrer-Policy" in h
