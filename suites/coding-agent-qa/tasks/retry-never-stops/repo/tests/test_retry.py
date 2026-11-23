import pytest
from retry import TransientError, call_with_retry

def test_gives_up():
    calls = []
    def boom():
        calls.append(1)
        raise TransientError()
    with pytest.raises(TransientError):
        call_with_retry(boom, max_attempts=3)
    assert len(calls) == 3
