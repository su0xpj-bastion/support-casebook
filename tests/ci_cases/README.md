Small pytest files that each produce one real CI failure. They are not collected by the main suite
(pytest only collects `test_*.py`); `tests/test_ci.py` runs each one in a subprocess and checks that
`casebook.ci.signatures.classify` names the right signature for the output.
