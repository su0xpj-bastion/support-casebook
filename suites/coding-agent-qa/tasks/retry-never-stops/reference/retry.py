class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


def call_with_retry(fn, max_attempts=3, sleep=lambda s: None):
    for attempt in range(max_attempts):
        try:
            return fn()
        except TransientError:
            if attempt == max_attempts - 1:
                raise
            sleep(2 ** attempt)
