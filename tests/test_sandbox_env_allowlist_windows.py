"""H-1 regression: the Windows run_code branch must not leak secret-bearing
environment variables into AI-generated / prompt-injectable subprocess code.

Before the fix the Windows branch did ``env = os.environ.copy()`` and passed
every parent var (ANTHROPIC_API_KEY, AXIOM_HL_API_SECRET, AXIOM_ENCRYPTION_KEY,
…) straight through. It now routes through env_allowlist.build_subprocess_env,
which drops secret-shaped names while preserving PYTHONPATH + BLAS caps.

Runs on every platform: the Windows code path is exercised by forcing
``sandbox.IS_WINDOWS = True`` and faking Popen + the Job Object plumbing, so no
actual subprocess or ctypes call happens.
"""
from __future__ import annotations

from axiom import sandbox


class _FakeProc:
    pid = 4321
    returncode = 0

    def communicate(self, timeout=None):
        return ("", "")

    def kill(self):  # pragma: no cover - timeout path not exercised here
        return None


def _force_windows(monkeypatch, captured):
    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(sandbox, "IS_WINDOWS", True)
    monkeypatch.setattr(sandbox, "_create_windows_job_object", lambda _mb: (None, None))
    monkeypatch.setattr(sandbox, "_close_job", lambda *_a, **_k: None)
    monkeypatch.setattr(sandbox.subprocess, "Popen", _fake_popen)


def test_windows_run_code_strips_secret_env_vars(monkeypatch):
    secrets = {
        "ANTHROPIC_API_KEY": "sk-ant-should-not-leak",
        "OPENAI_API_KEY": "sk-should-not-leak",
        "AXIOM_HL_API_SECRET": "0x" + "a" * 64,
        "AXIOM_ENCRYPTION_KEY": "ZmVybmV0LWtleS1ub3QtbGVhaw==",
        "AXIOM_OPERATOR_KEY": "operator-token",
        "GITHUB_WEBHOOK_SECRET": "whsec_nope",
    }
    for k, v in secrets.items():
        monkeypatch.setenv(k, v)

    captured: dict[str, object] = {}
    _force_windows(monkeypatch, captured)

    sandbox.run_code("print('hello')")

    env = captured["env"]
    for name in secrets:
        assert name not in env, f"secret {name} leaked into sandbox env"
    # And no value leaked under a renamed key either.
    leaked_values = set(secrets.values())
    assert leaked_values.isdisjoint(set(env.values())), "a secret value leaked"


def test_windows_run_code_preserves_pythonpath_and_blas(monkeypatch):
    captured: dict[str, object] = {}
    _force_windows(monkeypatch, captured)

    sandbox.run_code("print('x')")

    env = captured["env"]
    # repo root is on PYTHONPATH so the child can import axiom.*
    assert str(sandbox.REPO_ROOT) in env["PYTHONPATH"]
    for var in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        assert env.get(var) == "1", f"{var} not capped: {env.get(var)!r}"


def test_windows_run_code_blas_honours_parent_override(monkeypatch):
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "3")
    captured: dict[str, object] = {}
    _force_windows(monkeypatch, captured)

    sandbox.run_code("print('x')")

    env = captured["env"]
    assert env.get("OPENBLAS_NUM_THREADS") == "3"
