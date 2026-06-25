from __future__ import annotations

from axiom import sandbox


def test_sandbox_repo_root_points_to_project_root():
    assert (sandbox.REPO_ROOT / "Axiom").is_dir()


def test_run_code_uses_shell_false_for_posix_execution(monkeypatch):
    calls: dict[str, object] = {}

    class _FakeCompleted:
        stdout = ""
        stderr = ""
        returncode = 0

    class _FakeResource:
        RLIMIT_CPU = 0
        RLIMIT_AS = 1
        RLIMIT_FSIZE = 2
        RLIMIT_NOFILE = 3

        @staticmethod
        def setrlimit(_resource_type: int, _limits: tuple[int, int]) -> None:
            return None

    def _fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls.update(kwargs)
        return _FakeCompleted()

    monkeypatch.setattr(sandbox, "IS_WINDOWS", False)
    monkeypatch.setattr(sandbox, "resource", _FakeResource, raising=False)
    monkeypatch.setattr(sandbox.subprocess, "run", _fake_run)

    result = sandbox.run_code("print('hello')")

    assert result["returncode"] == 0
    assert calls["shell"] is False
    assert isinstance(calls["cmd"], list)
    assert calls["cmd"][0] == sandbox.PYTHON_EXE


def test_run_code_caps_blas_threads_in_posix_env(monkeypatch):
    """POSIX sandbox subprocess env caps BLAS thread pools.

    Without this, OpenBLAS/MKL allocate one workspace per CPU core on
    NumPy/pandas import and blow the memory cap on many-core hosts.
    """
    captured: dict[str, object] = {}

    class _FakeCompleted:
        stdout = ""
        stderr = ""
        returncode = 0

    class _FakeResource:
        RLIMIT_CPU = 0
        RLIMIT_AS = 1
        RLIMIT_FSIZE = 2
        RLIMIT_NOFILE = 3

        @staticmethod
        def setrlimit(_type: int, _limits: tuple[int, int]) -> None:
            return None

    def _fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _FakeCompleted()

    monkeypatch.setattr(sandbox, "IS_WINDOWS", False)
    monkeypatch.setattr(sandbox, "resource", _FakeResource, raising=False)
    monkeypatch.setattr(sandbox.subprocess, "run", _fake_run)

    sandbox.run_code("print('x')")

    env = captured["env"]
    for var in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        assert env.get(var) == "1", f"{var} not capped: {env.get(var)!r}"
