class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


def call_with_retry(fn, max_attempts=3, sleep=lambda s: None):
    attempt = 0
    while attempt < 50:
        try:
            return fn()
        except Exception:
            attempt += 1
            sleep(2 ** attempt)
    return None
