import subprocess


def run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    """Run a command; return (returncode, stdout or stderr). Missing binaries return (127, "")."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    return proc.returncode, proc.stdout or proc.stderr
