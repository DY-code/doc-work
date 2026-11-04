"""Coordinate system and idle pauses without losing scheduler progress."""

import datetime
import logging
from typing import Callable

from safeeyes.core import SafeEyesCore


def prepare_session(session: dict) -> None:
    """Do not restore yesterday's break queue on a fresh application start."""
    today = datetime.date.today().isoformat()
    if session.get("last_active_date") != today:
        session.pop("break", None)
    session["last_active_date"] = today


class ActivityController:
    """Merge pause reasons; only the last cleared reason may resume the timer."""

    def __init__(
        self,
        core: SafeEyesCore,
        short_interval: Callable[[], float],
        on_pause: Callable[[], None],
        on_resume: Callable[[], None],
    ) -> None:
        self.core = core
        self.short_interval = short_interval
        self.on_pause = on_pause
        self.on_resume = on_resume
        self.reasons: set[str] = set()
        self.paused_since: datetime.datetime | None = None
        self.idle_since: datetime.datetime | None = None
        self.system_pause_seen = False

    @property
    def paused(self) -> bool:
        return self.paused_since is not None

    def set_reason(self, reason: str, active: bool, credit_seconds: float = 0) -> None:
        """Apply an idempotent state notification from a monitor."""
        if active:
            if reason in self.reasons:
                return
            self.reasons.add(reason)
            if reason == "idle":
                self.idle_since = datetime.datetime.now() - datetime.timedelta(
                    seconds=credit_seconds
                )
            else:
                self.system_pause_seen = True
            self.ensure_paused(credit_seconds if reason == "idle" else 0)
        else:
            if reason not in self.reasons:
                return
            self.reasons.remove(reason)
            if not self.reasons:
                self._resume()

    def ensure_paused(self, credit_seconds: float = 0) -> None:
        """Also respect an existing lock or blank screen when manually enabled."""
        if not self.reasons or self.paused:
            return
        if self.core.pause_timer(credit_seconds):
            self.paused_since = (
                (self.idle_since or datetime.datetime.now())
                if self.reasons == {"idle"}
                else datetime.datetime.now()
            )
            self.core.context["automatic_pause"] = True
            self.core.context.session["last_active_date"] = (
                self.paused_since.date().isoformat()
            )
            logging.info("Pause timer: %s", sorted(self.reasons))
            self.on_pause()

    def _resume(self) -> None:
        if self.paused_since is None:
            self.system_pause_seen = False
            self.idle_since = None
            return
        now = datetime.datetime.now()
        new_day = now.date() != self.paused_since.date()
        idle_duration = (
            max(0, (now - self.idle_since).total_seconds())
            if self.idle_since is not None
            else 0
        )
        long_idle = (
            not self.system_pause_seen
            and self.idle_since is not None
            and idle_duration >= self.short_interval()
        )
        self.core.context["idle_period"] = idle_duration
        self.core.context["automatic_pause"] = False
        self.paused_since = None
        self.system_pause_seen = False
        if new_day or long_idle:
            self.core.restart_timer(new_day, self.idle_since)
        else:
            self.core.resume_timer()
        self.idle_since = None
        self.core.context.session["last_active_date"] = now.date().isoformat()
        logging.info("Resume timer (new day: %s)", new_day)
        self.on_resume()

    def cancel(self) -> None:
        """Manual disable/reconfiguration takes precedence over automatic resume."""
        self.paused_since = None
        self.idle_since = None
        self.system_pause_seen = bool(self.reasons - {"idle"})
        self.reasons.discard("idle")
        self.core.context["automatic_pause"] = False
        self.core.discard_pause()
