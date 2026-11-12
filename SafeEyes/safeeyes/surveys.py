"""Shared daily self-report statistics with stable option identities."""

import copy
import datetime
import json
import os
from pathlib import Path
import tempfile

KINDS = ("none", "distraction", "posture", "tension", "fatigue")
TITLES = ("无", "分心", "坐姿", "身体紧绷", "精神疲劳")
DEFAULTS = {
    "distraction": [("yes", "是"), ("no", "否")],
    "posture": [("good", "良好"), ("bad", "不良")],
    "tension": [("relaxed", "放松"), ("tense", "紧绷")],
    "fatigue": [("alert", "清醒"), ("tired", "疲劳")],
}


def default_options():
    return {
        kind: [{"id": key, "label": label, "active": True} for key, label in items]
        for kind, items in DEFAULTS.items()
    }


def migrate_surveys(config):
    """Retain existing distraction opt-ins and initialize the shared catalog."""
    catalog = config.setdefault("survey_options", {})
    for kind, options in default_options().items():
        catalog.setdefault(kind, options)
    for group in ("short_breaks", "long_breaks"):
        for item in config.get(group, []):
            item.setdefault(
                "question_type",
                "distraction" if item.get("distraction_stats") else "none",
            )


def validate_options(catalog):
    for kind in DEFAULTS:
        options = catalog[kind]
        active = [item for item in options if item.get("active", True)]
        ids = [item["id"] for item in options]
        labels = [item["label"].strip() for item in active]
        if (
            len(active) < 2
            or not all(labels)
            or len(set(labels)) != len(labels)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("每类至少保留两个选项；名称不能为空或重复。")


class SurveyStore:
    """Atomically save all categories and import the old file exactly once."""

    def __init__(self, path: Path, legacy: Path | None = None):
        self.path = path
        self.legacy = legacy

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {"version": 1, "days": {}}
            if self.legacy is not None and self.legacy.exists():
                from safeeyes.distraction import DistractionStore

                for day, counts in DistractionStore(self.legacy)._read().items():
                    data["days"][day] = {"distraction": counts}
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or not isinstance(data.get("days"), dict)
        ):
            raise ValueError("Invalid survey statistics")
        for day, categories in data["days"].items():
            datetime.date.fromisoformat(day)
            if not isinstance(categories, dict):
                raise ValueError("Invalid survey categories")
            for counts in categories.values():
                if not isinstance(counts, dict) or any(
                    type(count) is not int or count < 0 for count in counts.values()
                ):
                    raise ValueError("Invalid survey counts")
        return data

    def today(self, kind):
        return (
            self._read()["days"]
            .get(datetime.date.today().isoformat(), {})
            .get(kind, {})
        )

    def record(self, kind, option_id):
        data = self._read()
        counts = (
            data["days"]
            .setdefault(datetime.date.today().isoformat(), {})
            .setdefault(kind, {})
        )
        counts[option_id] = counts.get(option_id, 0) + 1
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
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


class SurveyPrompt:
    """Share one categorical answer across all windows and pause restoration."""

    def __init__(self, store):
        self.store = store
        self.catalog = default_options()
        self.token = None
        self.answer = None
        self.kind = "none"
        self.enabled = False
        self.error = ""

    def configure(self, catalog):
        self.catalog = copy.deepcopy(catalog or default_options())

    @property
    def choices(self):
        return [
            (item["label"], item["id"])
            for item in self.catalog.get(self.kind, [])
            if item.get("active", True)
        ]

    def begin(self, token, enabled, kind="distraction"):
        if token != self.token:
            self.answer = None
            self.error = ""
        self.token = token
        self.kind = kind
        self.enabled = enabled and kind in DEFAULTS
        if type(self.answer) is bool and kind == "distraction":
            self.answer = "yes" if self.answer else "no"

    def submit(self, token, answer):
        if token != self.token or not self.enabled or self.answer is not None:
            return
        if answer not in dict((key, label) for label, key in self.choices):
            return
        try:
            self.store.record(self.kind, answer)
        except (OSError, ValueError, AttributeError):
            self.error = "保存失败，请重试"
            raise
        self.answer = answer
        self.error = ""

    def answer_label(self):
        return next(
            (
                item["label"]
                for item in self.catalog.get(self.kind, [])
                if item["id"] == self.answer
            ),
            str(self.answer),
        )

    def summary(self):
        try:
            counts = self.store.today(self.kind)
        except (OSError, ValueError, AttributeError):
            return "今日统计读取失败，已有数据未改动"
        total = sum(counts.values())
        if not total:
            return "今日：暂无记录 · 占比 —"
        result = []
        known = set()
        for item in self.catalog.get(self.kind, []):
            key = item["id"]
            known.add(key)
            count = counts.get(key, 0)
            if item.get("active", True) or count:
                suffix = "" if item.get("active", True) else "（已停用）"
                result.append(
                    f"{item['label']}{suffix} {count} 次（{count / total:.0%}）"
                )
        for key in counts.keys() - known:
            result.append(f"已停用选项 {counts[key]} 次（{counts[key] / total:.0%}）")
        return "今日：" + " · ".join(result)
