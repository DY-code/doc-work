"""Verify persistent daily counts, opt-in migration and occurrence deduplication."""

import json

import pytest

from safeeyes.distraction import (
    DistractionPrompt,
    DistractionStore,
    migrate_distraction_config,
)


def test_migration_only_enables_original_question_once():
    config = {
        "short_breaks": [
            {"name": "你分心了吗？"},
            {"name": "喝点水吧"},
            {"name": "你分心了吗?", "distraction_stats": False},
        ]
    }
    migrate_distraction_config(config)
    question, other, disabled = config["short_breaks"]
    assert question["distraction_stats"] is True
    assert other["distraction_stats"] is False
    assert disabled["distraction_stats"] is False
    question["name"] = "刚才是否走神？"
    question["distraction_stats"] = False
    config["short_breaks"].append({"name": "你分心了吗？"})
    migrate_distraction_config(config)
    assert question["distraction_stats"] is False
    assert "distraction_stats" not in config["short_breaks"][-1]


def test_loading_config_persists_migration_and_preserves_manual_disable(
    tmp_path, monkeypatch
):
    from safeeyes import utility
    from safeeyes.configuration import Config

    config_path = tmp_path / "safeeyes.json"
    data = utility.load_json(utility.SYSTEM_CONFIG_FILE_PATH)
    data["short_breaks"] = [{"name": "你分心了吗？"}]
    config_path.write_text(json.dumps(data))
    monkeypatch.setattr(utility, "CONFIG_FILE_PATH", str(config_path))
    monkeypatch.setattr(utility, "merge_plugins", lambda config: None)
    monkeypatch.setattr(Config, "_create_startup_entry", lambda **kwargs: None)
    config = Config.load()
    assert config.get("short_breaks")[0]["distraction_stats"] is True
    assert json.loads(config_path.read_text())["distraction_stats_migrated"] is True
    config.get("short_breaks")[0].update(name="改过的标语", distraction_stats=False)
    config.save()
    loaded = Config.load()
    assert loaded.get("short_breaks")[0]["distraction_stats"] is False


def test_daily_counts_survive_restart_and_separate_days(tmp_path, time_machine):
    store = DistractionStore(tmp_path / "stats.json")
    time_machine.move_to("2026-11-10 12:00:00", tick=False)
    assert store.today() == (0, 0)
    store.record(True)
    store.record(False)
    store.record(False)
    assert DistractionStore(store.path).today() == (1, 2)
    assert "33%" in DistractionPrompt(store).summary()
    time_machine.shift(86400)
    assert store.today() == (0, 0)
    store.record(True)
    assert store.today() == (1, 0)
    assert len(json.loads(store.path.read_text())) == 2


def test_one_answer_per_occurrence_including_redraw_and_sleep(tmp_path):
    prompt = DistractionPrompt(DistractionStore(tmp_path / "stats.json"))
    prompt.begin("first", True)
    assert "暂无记录" in prompt.summary()
    prompt.submit("first", False)
    prompt.begin("first", True)  # Recreated windows after a system pause.
    prompt.submit("first", True)  # A second display or a queued click.
    assert prompt.answer is False
    assert prompt.store.today() == (0, 1)
    prompt.begin("second", True)
    prompt.submit("first", True)  # A stale window callback.
    assert prompt.answer is None
    prompt.submit("second", True)
    assert prompt.store.today() == (1, 1)


def test_unanswered_and_disabled_prompts_do_not_create_statistics(tmp_path):
    prompt = DistractionPrompt(DistractionStore(tmp_path / "stats.json"))
    prompt.begin("unanswered", True)
    prompt.begin("disabled", False)
    prompt.submit("disabled", True)
    assert not prompt.store.path.exists()


@pytest.mark.parametrize(
    "contents", ["broken json", "[]", '{"2026-11-10": {"yes": -1, "no": 0}}']
)
def test_bad_data_is_preserved_and_answer_is_not_marked_saved(tmp_path, contents):
    path = tmp_path / "stats.json"
    path.write_text(contents)
    prompt = DistractionPrompt(DistractionStore(path))
    prompt.begin("first", True)
    with pytest.raises(ValueError):
        prompt.submit("first", True)
    assert prompt.answer is None
    assert prompt.error
    assert path.read_text() == contents
    assert "读取失败" in prompt.summary()


def test_failed_write_can_be_retried_without_double_count(tmp_path, monkeypatch):
    prompt = DistractionPrompt(DistractionStore(tmp_path / "stats.json"))
    prompt.begin("first", True)
    with monkeypatch.context() as patch:
        patch.setattr(
            "safeeyes.distraction.os.replace",
            lambda *args: ((_ for _ in ()).throw(OSError("disk full"))),
        )
        with pytest.raises(OSError):
            prompt.submit("first", True)
    assert prompt.answer is None
    assert list(tmp_path.iterdir()) == []
    prompt.submit("first", True)
    assert prompt.store.today() == (1, 0)
