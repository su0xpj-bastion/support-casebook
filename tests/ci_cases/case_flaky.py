import os
from pathlib import Path


def test_fails_on_first_attempt_only():
    # The counter file stands in for whatever made the first attempt fail (a race, a slow runner).
    counter = Path(os.environ["CASEBOOK_FLAKY_COUNTER"])
    n = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(n + 1))
    assert n > 0, "first attempt fails"
