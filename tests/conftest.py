import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def private_data_in_tmp(tmp_path, monkeypatch):
    """Keep cookies, history and settings of every test inside its own temp folder."""
    monkeypatch.setenv("SOCIAL_SAVER_HOME", str(tmp_path / "home"))
