"""Independent, atomic scheduler checkpoints for shutdown and crash recovery."""

import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile


def boot_id() -> str:
    """Identify a real system reboot rather than an ordinary application restart."""
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


class ProgressFile:
    """Persist scheduler and activity state separately from optional session data."""

    def __init__(self, path, activity, config, prompt=None, boot=boot_id):
        self.path = Path(path)
        self.activity = activity
        self.core = activity.core
        self.config = config
        self.prompt = prompt
        self.boot = boot

    def _fingerprint(self):
        keys = (
            "short_breaks",
            "long_breaks",
            "short_break_interval",
            "long_break_interval",
            "short_break_duration",
            "long_break_duration",
            "random_order",
            "pre_break_warning_time",
            "postpone_duration",
            "postpone_unit",
        )
        text = json.dumps({key: self.config().get(key) for key in keys}, sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()

    def save(self, enabled: bool, closed=False) -> None:
        """Checkpoint periodically and before any destructive shutdown cleanup."""
        progress = self.core.export_progress()
        if enabled and progress is None:
            return  # A scheduler hook may run before its next timeout is installed.
        data = {
            "version": 1,
            "boot": self.boot(),
            "config": self._fingerprint(),
            "saved_at": datetime.datetime.now().isoformat(),
            "enabled": enabled,
            "closed": closed,
            "progress": progress,
            "paused_since": self.activity.paused_since.isoformat()
            if self.activity.paused_since
            else None,
            "away_since": self.activity.away_since.isoformat()
            if self.activity.away_since
            else None,
            "away_duration": self.activity.away_duration,
            "system_pause": self.activity.system_pause_seen,
            "idle_since": self.activity.idle_since.isoformat()
            if self.activity.idle_since
            else None,
            "prompt": {"token": self.prompt.token, "answer": self.prompt.answer}
            if self.prompt
            else None,
        }
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as stream:
                temporary = stream.name
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            logging.exception("Could not save scheduler progress")
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def restore(self) -> bool:
        """Recover a frozen timer while honoring current lock and display states."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                data["version"] != 1
                or not data["enabled"]
                or data["config"] != self._fingerprint()
            ):
                return False
            rebooted = data["boot"] != self.boot()
            if not rebooted and data["closed"]:
                return False  # Preserve the existing behavior of explicitly quitting.
            saved = datetime.datetime.fromisoformat(data["saved_at"])
            paused = datetime.datetime.fromisoformat(
                data["paused_since"] or data["saved_at"]
            )
            away = (
                datetime.datetime.fromisoformat(data["away_since"])
                if data["away_since"]
                else None
            )
            idle = (
                datetime.datetime.fromisoformat(data["idle_since"])
                if data["idle_since"]
                else None
            )
            if any(
                value and value.tzinfo is not None
                for value in (saved, paused, away, idle)
            ):
                return False
            duration = data["away_duration"]
            if type(duration) not in (int, float) or not 0 <= duration < 315360000:
                return False
            self.core.restore_progress(data["progress"])
        except FileNotFoundError:
            return False
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            logging.exception("Ignoring incompatible scheduler checkpoint")
            return False
        activity = self.activity
        activity.paused_since = paused
        activity.idle_since = idle
        activity.system_pause_seen = bool(
            data["system_pause"] or rebooted or activity.reasons
        )
        activity.away_since = away or (saved if rebooted else activity.away_since)
        activity.away_duration = duration
        self.core.context["automatic_pause"] = True
        if self.prompt and isinstance(data.get("prompt"), dict):
            answer = data["prompt"].get("answer")
            if answer is None or type(answer) in (bool, str):
                self.prompt.token = data["prompt"].get("token")
                self.prompt.answer = answer
        # The artificial reason is cleared only after the real startup states exist.
        activity.reasons.add("offline")
        activity.on_pause()
        activity.set_reason("offline", False)
        return True
