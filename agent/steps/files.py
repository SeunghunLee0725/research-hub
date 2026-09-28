from pathlib import Path


def write_step_files(workdir: str, files: dict[str, str]) -> None:
    """Write files the hub attached to a step (e.g. a person's answers). Paths must stay inside the workdir."""
    root = Path(workdir).resolve()
    for relative, content in files.items():
        target = (root / relative).resolve()
        if Path(relative).is_absolute() or ".." in Path(relative).parts or root not in target.parents:
            raise ValueError(f"작업 디렉터리 밖 경로는 쓸 수 없습니다: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
