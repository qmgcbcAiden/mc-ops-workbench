from __future__ import annotations

from types import SimpleNamespace

from src.service import system_service as system_service_module
from src.service.system_service import FIRST_SYSTEM_CPU_SAMPLE_SECONDS, SystemService


class _RepoStub:
    def __init__(self) -> None:
        self.samples: list[dict] = []

    def insert_sample(self, sample: dict) -> int:
        self.samples.append(sample)
        return len(self.samples)

    def list_recent(self, limit: int = 120) -> list[dict]:
        del limit
        return list(self.samples)


class _ProcessStub:
    def __init__(self, pid: int, rss: int, cpu_samples: list[float]) -> None:
        self.pid = pid
        self._rss = rss
        self._cpu_samples = cpu_samples
        self._cpu_index = 0

    def memory_info(self):
        return SimpleNamespace(rss=self._rss)

    def cpu_times(self):
        sample = self._cpu_samples[min(self._cpu_index, len(self._cpu_samples) - 1)]
        self._cpu_index += 1
        return SimpleNamespace(user=sample, system=0.0)


def test_process_metrics_aggregate_process_tree_and_prime_cpu(monkeypatch) -> None:
    repo = _RepoStub()
    service = SystemService(repo)
    root = _ProcessStub(pid=100, rss=256 * 1024 * 1024, cpu_samples=[1.00, 1.02])
    child = _ProcessStub(pid=101, rss=768 * 1024 * 1024, cpu_samples=[2.00, 2.08])
    monotonic_values = iter([10.0, 10.1])

    monkeypatch.setattr(service, "_process_tree", lambda server_pid: [root, child])
    monkeypatch.setattr(system_service_module.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(system_service_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        system_service_module.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(
            total=10 * 1024 * 1024 * 1024,
            used=5 * 1024 * 1024 * 1024,
            percent=50.0,
        ),
    )
    monkeypatch.setattr(system_service_module.psutil, "cpu_percent", lambda interval=0.0: 42.0)
    monkeypatch.setattr(system_service_module.psutil, "cpu_count", lambda logical=True: 4)

    sample = service.capture_metrics(server_pid=100)

    assert sample["server_pid"] == 100
    assert sample["cpu_percent"] == 42.0
    assert sample["server_cpu_percent"] == 25.0
    assert sample["memory_percent"] == 50.0
    assert sample["memory_used_mb"] == 5120.0
    assert sample["memory_total_mb"] == 10240.0
    assert sample["server_memory_used_mb"] == 1024.0
    assert sample["server_memory_percent"] == 10.0
    assert repo.samples == [sample]


def test_system_metrics_include_zero_server_process_fields(monkeypatch) -> None:
    repo = _RepoStub()
    service = SystemService(repo)
    monkeypatch.setattr(system_service_module.psutil, "cpu_percent", lambda interval=0.0: 13.0)
    monkeypatch.setattr(
        system_service_module.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(
            total=8 * 1024 * 1024 * 1024,
            used=2 * 1024 * 1024 * 1024,
            percent=25.0,
        ),
    )

    sample = service.capture_metrics()

    assert sample["cpu_percent"] == 13.0
    assert sample["memory_percent"] == 25.0
    assert sample["server_cpu_percent"] == 0.0
    assert sample["server_memory_percent"] == 0.0
    assert sample["server_memory_used_mb"] == 0.0
    assert sample["server_pid"] is None


def test_first_system_cpu_sample_uses_short_measured_interval(monkeypatch) -> None:
    repo = _RepoStub()
    service = SystemService(repo)
    intervals: list[float] = []

    def cpu_percent(interval=0.0):
        intervals.append(interval)
        return 0.0 if interval == 0.0 else 17.5

    monkeypatch.setattr(system_service_module.psutil, "cpu_percent", cpu_percent)
    monkeypatch.setattr(
        system_service_module.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(
            total=8 * 1024 * 1024 * 1024,
            used=2 * 1024 * 1024 * 1024,
            percent=25.0,
        ),
    )

    first = service.capture_metrics()
    first_intervals = list(intervals)
    intervals.clear()
    second = service.capture_metrics()

    assert first["cpu_percent"] == 17.5
    assert second["cpu_percent"] == 0.0
    assert first_intervals == [FIRST_SYSTEM_CPU_SAMPLE_SECONDS]
    assert intervals == [0.0]
    assert repo.samples == [first, second]


def test_first_system_cpu_sample_interval_constant_is_short() -> None:
    assert FIRST_SYSTEM_CPU_SAMPLE_SECONDS <= 0.2
