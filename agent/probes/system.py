import os
import shutil
from pathlib import Path


def parse_meminfo(text: str) -> tuple[int, int]:
    fields = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if rest.strip():
            fields[key] = int(rest.split()[0])
    total = fields["MemTotal"] // 1024
    return total, total - fields["MemAvailable"] // 1024


def collect(disk_path: str) -> dict:
    metrics: dict = {"load": [round(x, 2) for x in os.getloadavg()]}
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        metrics["mem_total_mb"], metrics["mem_used_mb"] = parse_meminfo(meminfo.read_text())
    disk = shutil.disk_usage(disk_path)
    metrics["disk_total_gb"] = round(disk.total / 1e9, 1)
    metrics["disk_used_gb"] = round(disk.used / 1e9, 1)
    return metrics
