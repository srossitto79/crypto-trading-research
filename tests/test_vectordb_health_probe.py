from __future__ import annotations

from types import SimpleNamespace


def test_check_chroma_available_uses_valid_probe_payload(monkeypatch):
    import axiom.vectordb as vectordb

    # This test exercises the subprocess probe path. Importing Axiom.api sets
    # AXIOM_DISABLE_CHROMA_IN_PROCESS on Windows, which short-circuits the probe
    # to False; clear it so the probe runs deterministically regardless of which
    # tests ran first.
    monkeypatch.delenv("AXIOM_DISABLE_CHROMA_IN_PROCESS", raising=False)

    captured: dict[str, object] = {}
    monkeypatch.setattr(vectordb, "_chroma_available", None)

    def _fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        return SimpleNamespace(returncode=0, stdout="OK\n", stderr="")

    monkeypatch.setattr(vectordb.subprocess, "run", _fake_run)

    try:
        assert vectordb._check_chroma_available() is True
    finally:
        vectordb._chroma_available = None

    script = str(captured["cmd"][2])
    assert vectordb._HEALTH_COLLECTION_NAME in script
    assert f"ids=['{vectordb._HEALTH_DOCUMENT_ID}']" in script
    assert "metadatas=[{}]" not in script
    assert "'source': 'AXIOM_health_check'" in script
    assert "'_health_check'" not in script
