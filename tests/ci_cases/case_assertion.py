from casebook import rules


def test_spend_cap_is_retryable():
    # Deliberately wrong expectation: a regression always fails the same way.
    v = rules.classify({"provider": "anthropic", "http": {"status": 429, "retry_after": None},
                        "error": {"details_error_code": "enforced_spend_limit_reached"}})
    assert v.retryable == "Y"
