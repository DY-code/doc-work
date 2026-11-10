"""Persist daily distraction answers independently of session state."""

import datetime
import json
import os
from pathlib import Path
import tempfile


def migrate_distraction_config(config: dict) -> None:
    """Enable the existing question once; later wording never controls the switch."""
    if config.get("distraction_stats_migrated", False):
        return
    for group in ("short_breaks", "long_breaks"):
        for item in config.get(group, []):
            item.setdefault(
                "distraction_stats",
                item["name"].strip().rstrip("？?") == "你分心了吗",
            )
    config["distraction_stats_migrated"] = True


class DistractionStore:
    """Read and atomically update daily counts; never overwrite unreadable data."""

    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise ValueError("Invalid distraction statistics")
        for day, counts in data.items():
            datetime.date.fromisoformat(day)
            if not isinstance(counts, dict) or any(
                type(counts.get(key)) is not int or counts[key] < 0
                for key in ("yes", "no")
            ):
                raise ValueError("Invalid distraction counts")
        return data

    def today(self) -> tuple[int, int]:
        counts = self._read().get(datetime.date.today().isoformat(), {})
        return counts.get("yes", 0), counts.get("no", 0)

    def record(self, distracted: bool) -> None:
        data = self._read()
        counts = data.setdefault(datetime.date.today().isoformat(), {"yes": 0, "no": 0})
        counts["yes" if distracted else "no"] += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as stream:
                temporary = stream.name
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)


class DistractionPrompt:
    """Share one answer across all displays and redraws of a break occurrence."""

    def __init__(self, store: DistractionStore):
        self.store = store
        self.token = None
        self.enabled = False
        self.answer: bool | None = None
        self.error = ""

    def begin(self, token: str, enabled: bool) -> None:
        if token != self.token:
            self.answer = None
            self.error = ""
        self.token = token
        self.enabled = enabled

    def submit(self, token: str, distracted: bool) -> None:
        if token != self.token or not self.enabled or self.answer is not None:
            return
        try:
            self.store.record(distracted)
        except (OSError, ValueError):
            self.error = "保存失败，请重试"
            raise
        self.answer = distracted
        self.error = ""

    def summary(self) -> str:
        try:
            yes, no = self.store.today()
        except (OSError, ValueError):
            return "今日统计读取失败，已有数据未改动"
        if yes + no == 0:
            return "今日：暂无记录 · 分心比例 —"
        return f"今日：分心 {yes} 次 · 未分心 {no} 次 · 分心比例 {yes / (yes + no):.0%}"
