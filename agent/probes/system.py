import os
import shutil
import time
from pathlib import Path


def parse_meminfo(text: str) -> tuple[int, int]:
    fields = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if rest.strip():
            fields[key] = int(rest.split()[0])
    total = fields["MemTotal"] // 1024
    return total, total - fields["MemAvailable"] // 1024


CPU_SAMPLE_S = 1.0


def parse_cpu_stat(text: str) -> tuple[int, int]:
    """(busy, total) jiffies from the aggregate "cpu" line of /proc/stat; idle and iowait count as not busy."""
    values = [int(v) for v in text.splitlines()[0].split()[1:]]
    total = sum(values[:8])
    return total - values[3] - values[4], total


def cpu_percent(before: tuple[int, int], after: tuple[int, int]) -> float | None:
    total = after[1] - before[1]
    return round(100 * (after[0] - before[0]) / total, 1) if total > 0 else None


def _cpu_usage() -> float | None:
    stat = Path("/proc/stat")
    if not stat.exists():
        return None
    before = parse_cpu_stat(stat.read_text())
    time.sleep(CPU_SAMPLE_S)
    return cpu_percent(before, parse_cpu_stat(stat.read_text()))


def collect(disk_path: str) -> dict:
    metrics: dict = {"load": [round(x, 2) for x in os.getloadavg()], "cpu_cores": os.cpu_count() or 1}
    cpu = _cpu_usage()
    if cpu is not None:
        metrics["cpu_pct"] = cpu
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        metrics["mem_total_mb"], metrics["mem_used_mb"] = parse_meminfo(meminfo.read_text())
    disk = shutil.disk_usage(disk_path)
    metrics["disk_total_gb"] = round(disk.total / 1e9, 1)
    metrics["disk_used_gb"] = round(disk.used / 1e9, 1)
    return metrics
