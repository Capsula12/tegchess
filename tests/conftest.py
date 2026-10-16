import json
from pathlib import Path

import pytest

from tegchess import db

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def raw_games() -> list[dict]:
    with open(FIXTURES / "german11_blitz.ndjson", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@pytest.fixture
def con(tmp_path):
    c = db.connect(tmp_path / "test.duckdb")
    yield c
    c.close()
