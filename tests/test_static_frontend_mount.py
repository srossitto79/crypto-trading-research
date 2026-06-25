import tempfile, pathlib, importlib
from fastapi.testclient import TestClient

def test_frontend_served_from_env_path(monkeypatch):
    tmp = tempfile.mkdtemp()
    pathlib.Path(tmp, "index.html").write_text("<h1>Axiom static</h1>")
    monkeypatch.setenv("AXIOM_FRONTEND_DIR", tmp)
    import axiom.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    r = client.get("/")
    assert r.status_code == 200
    assert "axiom static" in r.text

def test_no_mount_when_env_unset(monkeypatch):
    monkeypatch.delenv("AXIOM_FRONTEND_DIR", raising=False)
    import axiom.api as api
    importlib.reload(api)
    client = TestClient(api.app)
    r = client.get("/")
    assert "axiom static" not in r.text
