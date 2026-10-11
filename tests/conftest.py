from __future__ import annotations

import pytest

from database import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "concert-radar.sqlite3")
