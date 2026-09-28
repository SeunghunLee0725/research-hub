"""research-hub node agent: reports machine state to the hub. Standard library only."""
import argparse
import logging
import shutil
import socket
import sys
import time
from pathlib import Path

from agent import __version__
from agent.client import HubClient, HubError
from agent.config import load_config
from agent.executor import Executor
from agent.probes import gpu, logins, quota, system
from agent.schedule import Schedule

INTERVALS = {"heartbeat": 30, "logins": 300, "quota": 600}
log = logging.getLogger("research-hub-agent")


def _binary(name: str, home: Path) -> str:
    local = home / ".local/bin" / name
    return str(local) if local.exists() else shutil.which(name) or name


def _codex_bin(cfg, home: Path) -> str:
    return cfg.codex_bin or _binary("codex", home)


def _job(name: str, cfg, home: Path) -> list[tuple[str, dict]]:
    claude_bin, codex_bin = _binary("claude", home), _codex_bin(cfg, home)
    if name == "heartbeat":
        metrics = {**system.collect(cfg.disk_path), **gpu.collect()}
        return [("/agent/v1/heartbeat", {"agent_version": __version__, "hostname": socket.gethostname(),
                                         "metrics": metrics})]
    if name == "logins":
        return [("/agent/v1/logins", logins.collect(claude_bin, codex_bin, home))]
    return [("/agent/v1/quota", quota.safe("claude", lambda: quota.claude_usage(home))),
            ("/agent/v1/quota", quota.safe("codex", lambda: quota.codex_usage(codex_bin)))]


def loop(cfg, home: Path) -> None:
    client, schedule = HubClient(cfg.hub_url, cfg.token), Schedule(INTERVALS)
    executor = Executor(client, home / ".local/state/research-hub", _binary("claude", home), cfg.model,
                        codex_bin=_codex_bin(cfg, home), codex_model=cfg.codex_model,
                        codex_sandbox=cfg.codex_sandbox) if cfg.execute else None
    while True:
        now = time.monotonic()
        if executor is not None:
            try:
                executor.tick(time.time())
            except Exception:
                log.exception("executor tick failed")
        due = schedule.due(now)
        for name in due:
            try:
                for path, payload in _job(name, cfg, home):
                    client.post(path, payload)
            except HubError as exc:
                log.warning("%s 전송 실패: %s", name, exc)
            except Exception:
                log.exception("%s 수집 실패", name)
        schedule = schedule.mark(due, now)
        time.sleep(5)


def main() -> int:
    parser = argparse.ArgumentParser(prog="research-hub-agent")
    parser.add_argument("--config", type=Path, default=Path.home() / ".config/research-hub/agent.env")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        log.error("설정 오류: %s", exc)
        return 2
    loop(cfg, Path.home())
    return 0


if __name__ == "__main__":
    sys.exit(main())
