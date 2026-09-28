from pathlib import PurePath

from agent.probes.run import run


def _int(value: str) -> int | None:
    value = value.strip()
    return int(float(value)) if value.replace(".", "", 1).isdigit() else None


def parse_gpu_csv(text: str) -> list[dict]:
    gpus = []
    for line in text.splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) != 4:
            continue
        gpus.append({"name": cols[0], "util_pct": _int(cols[1]),
                     "mem_used_mb": _int(cols[2]), "mem_total_mb": _int(cols[3])})
    return gpus


def parse_gpu_procs(text: str) -> list[dict]:
    procs = []
    for line in text.splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) != 3 or not cols[0].isdigit():
            continue
        procs.append({"pid": int(cols[0]), "name": PurePath(cols[1]).name, "mem_mb": _int(cols[2])})
    return procs


def collect() -> dict:
    rc, out = run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                   "--format=csv,noheader,nounits"])
    if rc != 0:
        return {"gpus": [], "gpu_procs": []}
    _, procs = run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                    "--format=csv,noheader,nounits"])
    return {"gpus": parse_gpu_csv(out), "gpu_procs": parse_gpu_procs(procs)[:64]}
