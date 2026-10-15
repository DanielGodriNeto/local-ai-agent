import pytest

import orchestrator


@pytest.fixture(autouse=True)
def _log_elsewhere(monkeypatch, tmp_path):
    """Tests never write to the real agent.log.jsonl."""
    monkeypatch.setattr(orchestrator, "HERE", tmp_path)
