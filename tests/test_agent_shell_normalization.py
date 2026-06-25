from axiom.agents.context import _normalize_legacy_paths


def test_normalize_legacy_paths_translates_windows_ls_and_home_alias():
    result = _normalize_legacy_paths(
        "ls -la ~/.Axiom/workspace/",
        is_windows=True,
        home=r"C:\Users\trader",
    )

    assert result == "dir C:/Users/trader/.Axiom/workspace/"


def test_normalize_legacy_paths_translates_windows_null_redirection():
    result = _normalize_legacy_paths(
        'cd ~/.Axiom && ls -la 2>/dev/null || echo "No .Axiom directory"',
        is_windows=True,
        home=r"C:\Users\trader",
    )

    assert "cd C:/Users/trader/.Axiom" in result
    assert "&& dir 2>nul" in result
    assert "/dev/null" not in result


def test_normalize_legacy_paths_translates_unix_find_to_powershell():
    result = _normalize_legacy_paths(
        'find ~/.Axiom/workspace -name "*S00038*" -o -name "*post*mortem*" 2>/dev/null | head -20',
        is_windows=True,
        home=r"C:\Users\trader",
    )

    assert result.startswith("powershell -NoProfile -Command ")
    assert "Get-ChildItem -Path 'C:\\Users\\trader\\.Axiom\\workspace'" in result
    assert "'*S00038*'" in result
    assert "'*post*mortem*'" in result
    assert "Select-Object -First 20" in result
    assert "ExpandProperty FullName" in result


def test_normalize_legacy_paths_keeps_posix_commands_but_repairs_legacy_home():
    result = _normalize_legacy_paths(
        "ls -la ~/judex/workspace/",
        is_windows=False,
        home="/home/tester",
    )

    assert result == "ls -la ~/.Axiom/workspace/"
