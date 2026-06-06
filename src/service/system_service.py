from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

import psutil

from src.repositories._time import utc_now_iso
from src.repositories.metric_repository import MetricRepository

FIRST_PROCESS_CPU_SAMPLE_SECONDS = 0.1
FIRST_SYSTEM_CPU_SAMPLE_SECONDS = 0.2


@dataclass(frozen=True)
class _CpuSample:
    captured_at: float
    cpu_seconds: float


class SystemService:
    def __init__(self, metric_repository: MetricRepository):
        self.metric_repository = metric_repository
        self._process_cpu_samples: dict[int, _CpuSample] = {}
        self._system_cpu_sample_ready = False

    def capture_metrics(self, server_pid: int | None = None) -> dict:
        sample = self._system_metrics()
        sample.update(
            self._server_process_metrics(server_pid, total_memory_mb=sample["memory_total_mb"])
        )
        self.metric_repository.insert_sample(sample)
        return sample

    def get_recent_metrics(self, limit: int = 120) -> list[dict]:
        samples = self.metric_repository.list_recent(limit=limit)
        if not samples:
            return [self.capture_metrics()]
        return samples

    def get_server_status(self) -> dict:
        return {
            "status": "unknown",
            "pid": None,
            "label": "未知",
        }

    def _server_process_metrics(self, server_pid: int | None, total_memory_mb: float) -> dict:
        default = {
            "server_cpu_percent": 0.0,
            "server_memory_percent": 0.0,
            "server_memory_used_mb": 0.0,
            "server_pid": server_pid,
        }
        if server_pid is None:
            return default

        try:
            processes = self._process_tree(server_pid)
            if not processes:
                self._process_cpu_samples.pop(server_pid, None)
                return default

            memory_used_bytes = _sum_memory_rss(processes)
            if memory_used_bytes <= 0:
                self._process_cpu_samples.pop(server_pid, None)
                return default

            cpu_percent = self._process_cpu_percent(server_pid, processes)
            if cpu_percent is None:
                time.sleep(FIRST_PROCESS_CPU_SAMPLE_SECONDS)
                processes = self._process_tree(server_pid)
                cpu_percent = self._process_cpu_percent(server_pid, processes) or 0.0

            total_memory_bytes = total_memory_mb * 1024 * 1024
            return {
                "server_cpu_percent": round(max(0.0, float(cpu_percent)), 1),
                "server_memory_percent": round(memory_used_bytes / total_memory_bytes * 100, 2),
                "server_memory_used_mb": round(memory_used_bytes / 1024 / 1024, 1),
                "server_pid": server_pid,
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            self._process_cpu_samples.pop(server_pid, None)
            return default

    def _process_tree(self, server_pid: int) -> list[psutil.Process]:
        root = psutil.Process(server_pid)
        processes = [root]
        try:
            processes.extend(root.children(recursive=True))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
        return _dedupe_processes(processes)

    def _process_cpu_percent(self, server_pid: int, processes: Iterable[psutil.Process]) -> float | None:
        captured_at = time.monotonic()
        cpu_seconds = _sum_cpu_seconds(processes)
        previous = self._process_cpu_samples.get(server_pid)
        self._process_cpu_samples[server_pid] = _CpuSample(captured_at, cpu_seconds)
        if previous is None:
            return None

        elapsed = captured_at - previous.captured_at
        cpu_delta = cpu_seconds - previous.cpu_seconds
        if elapsed <= 0 or cpu_delta < 0:
            return None
        return _normalize_process_cpu_percent(cpu_delta / elapsed * 100)

    def _system_metrics(self) -> dict:
        memory = psutil.virtual_memory()
        if not self._system_cpu_sample_ready:
            cpu_percent = float(psutil.cpu_percent(interval=FIRST_SYSTEM_CPU_SAMPLE_SECONDS))
            self._system_cpu_sample_ready = True
        else:
            cpu_percent = float(psutil.cpu_percent(interval=0.0))
        return {
            "captured_at": utc_now_iso(),
            "cpu_percent": cpu_percent,
            "memory_percent": float(memory.percent),
            "memory_used_mb": round(memory.used / 1024 / 1024, 1),
            "memory_total_mb": round(memory.total / 1024 / 1024, 1),
            "server_pid": None,
        }


def _dedupe_processes(processes: Iterable[psutil.Process]) -> list[psutil.Process]:
    result: list[psutil.Process] = []
    seen: set[int] = set()
    for process in processes:
        pid = getattr(process, "pid", None)
        if pid is None or pid in seen:
            continue
        seen.add(pid)
        result.append(process)
    return result


def _sum_memory_rss(processes: Iterable[psutil.Process]) -> int:
    total = 0
    for process in processes:
        try:
            total += int(process.memory_info().rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return total


def _sum_cpu_seconds(processes: Iterable[psutil.Process]) -> float:
    total = 0.0
    for process in processes:
        try:
            cpu_times = process.cpu_times()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        total += float(cpu_times.user) + float(cpu_times.system)
    return total


def _normalize_process_cpu_percent(raw_percent: float) -> float:
    cpu_count = psutil.cpu_count(logical=True) or 1
    return min(max(raw_percent / cpu_count, 0.0), 100.0)
