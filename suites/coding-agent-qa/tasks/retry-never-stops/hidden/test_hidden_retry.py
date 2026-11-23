import pytest
from retry import PermanentError, TransientError, call_with_retry

def test_permanent_is_not_retried():
    calls = []
    def boom():
        calls.append(1)
        raise PermanentError()
    with pytest.raises(PermanentError):
        call_with_retry(boom, max_attempts=5)
    assert len(calls) == 1

def test_succeeds_after_transient():
    state = {"n": 0}
    def flaky():
        state["n"] += 1
        if state["n"] < 3:
            raise TransientError()
        return "ok"
    assert call_with_retry(flaky, max_attempts=3) == "ok"

def test_backoff_doubles_and_no_sleep_after_last():
    slept = []
    with pytest.raises(TransientError):
        call_with_retry(lambda: (_ for _ in ()).throw(TransientError()), max_attempts=4, sleep=slept.append)
    assert slept == [1, 2, 4]
