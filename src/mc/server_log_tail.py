from __future__ import annotations

from pathlib import Path

_PREFIX_SAMPLE_BYTES = 4096


class LogTailer:
    def __init__(self, log_path: Path) -> None:
        self._log_path = log_path
        self._offset = 0
        self._partial = ""
        self._file_id: tuple[int, int] | None = None
        self._prefix_sample: bytes | None = None
        self.last_skipped_count = 0

    def read_new_lines(self, limit: int | None = None) -> list[str]:
        self.last_skipped_count = 0
        if not self._log_path.is_file():
            return []

        try:
            stat_result = self._log_path.stat()
        except OSError:
            return []
        current_size = stat_result.st_size
        current_file_id = _file_id(stat_result)

        if self._file_id is not None and current_file_id != self._file_id:
            self.reset()
        self._file_id = current_file_id

        if self._offset > 0 and self._prefix_sample:
            current_prefix_sample = _prefix_sample(self._log_path, len(self._prefix_sample))
            if current_prefix_sample is not None and current_prefix_sample != self._prefix_sample:
                self.reset()
                self._file_id = current_file_id

        if current_size < self._offset:
            self.reset()
            self._file_id = current_file_id

        if current_size == self._offset:
            return []

        try:
            with self._log_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
                self._offset = handle.tell()
        except OSError:
            return []

        if not chunk:
            return []

        text = self._partial + chunk
        if text.endswith(("\n", "\r")):
            complete_text = text
            self._partial = ""
        else:
            split_at = max(text.rfind("\n"), text.rfind("\r"))
            if split_at == -1:
                self._partial = text
                return []
            complete_text = text[: split_at + 1]
            self._partial = text[split_at + 1 :]

        lines = _clean_lines(complete_text.splitlines())
        if limit is not None and limit >= 0 and len(lines) > limit:
            self.last_skipped_count = len(lines) - limit
            return lines[-limit:]
        return lines

    def read_last_lines(self, limit: int = 300) -> list[str]:
        if not self._log_path.is_file():
            return []

        try:
            with self._log_path.open("r", encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
                self._offset = handle.tell()
                self._file_id = _file_id(self._log_path.stat())
                self._prefix_sample = _prefix_sample(self._log_path)
        except OSError:
            return []

        self._partial = ""
        return _clean_lines(lines[-max(0, limit) :])

    def reset(self) -> None:
        self._offset = 0
        self._partial = ""
        self._file_id = None
        self._prefix_sample = None
        self.last_skipped_count = 0

    def seek_to_end(self) -> None:
        self._partial = ""
        self.last_skipped_count = 0
        if not self._log_path.is_file():
            self._offset = 0
            self._file_id = None
            self._prefix_sample = None
            return
        try:
            stat_result = self._log_path.stat()
            self._offset = stat_result.st_size
            self._file_id = _file_id(stat_result)
            self._prefix_sample = _prefix_sample(self._log_path)
        except OSError:
            self._offset = 0
            self._file_id = None
            self._prefix_sample = None


def _clean_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    for line in lines:
        value = line.rstrip("\r\n")
        if value:
            cleaned.append(value)
    return cleaned


def _file_id(stat_result: object) -> tuple[int, int]:
    return (
        int(getattr(stat_result, "st_dev", 0)),
        int(getattr(stat_result, "st_ino", 0)),
    )


def _prefix_sample(path: Path, size: int = _PREFIX_SAMPLE_BYTES) -> bytes | None:
    try:
        with path.open("rb") as handle:
            return handle.read(size)
    except OSError:
        return None
