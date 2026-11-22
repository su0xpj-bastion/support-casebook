from __future__ import annotations

import pytest

from casebook.mockserver import MockServer

pytest_plugins = ["casebook.ci.pytest_plugin"]


@pytest.fixture(scope="session")
def mock() -> MockServer:
    with MockServer() as srv:
        yield srv
