import socket, subprocess, sys

def test_port_in_use_exits_with_message():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.listen(1)
    try:
        r = subprocess.run(
            [sys.executable, "-m", "axiom.api", "--port", str(port)],
            capture_output=True, text=True, timeout=45,
        )
    finally:
        s.close()
    assert r.returncode != 0
    assert "in use" in (r.stderr + r.stdout).lower()
