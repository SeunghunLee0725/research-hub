from dataclasses import dataclass, field


@dataclass(frozen=True)
class Schedule:
    intervals: dict[str, int]
    last_run: dict[str, float] = field(default_factory=dict)

    def due(self, now: float) -> tuple[str, ...]:
        return tuple(job for job, every in self.intervals.items()
                     if job not in self.last_run or now - self.last_run[job] >= every)

    def mark(self, jobs: tuple[str, ...], now: float) -> "Schedule":
        return Schedule(self.intervals, {**self.last_run, **{job: now for job in jobs}})
