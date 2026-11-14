import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Tests never touch the real cache and never hit the network."""
    from collections import OrderedDict
    from nadir import config, field, pipeline
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pipeline, "_sessions", {})
    monkeypatch.setattr(field, "_fields", OrderedDict())
    monkeypatch.setenv("NADIR_DETECT_PROCESSES", "1")

    def no_network(*a, **k):
        raise AssertionError("Unit tests must not access the network")
    import requests
    monkeypatch.setattr(requests, "get", no_network)
