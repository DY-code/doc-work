"""Check shared categories, stable choices and migration without losing old counts."""

import json

import pytest
from safeeyes.tests.test_activity import timer as timer_fixture, start_break

from safeeyes.surveys import (
    SurveyStore,
    SurveyPrompt,
    default_options,
    migrate_surveys,
    validate_options,
)

timer = timer_fixture


def test_settings_migration_retains_opt_in_and_is_idempotent():
    config = {
        "short_breaks": [
            {"name": "renamed", "distraction_stats": True},
            {"name": "other", "distraction_stats": False},
        ]
    }
    migrate_surveys(config)
    first, second = config["short_breaks"]
    assert first["question_type"] == "distraction"
    assert second["question_type"] == "none"
    first["question_type"] = "posture"
    migrate_surveys(config)
    assert first["question_type"] == "posture"
    assert len(config["survey_options"]) == 4


@pytest.mark.parametrize("upgrade", [False, True])
def test_archived_builtin_survives_reload_and_upgrade(tmp_path, monkeypatch, upgrade):
    from safeeyes import utility
    from safeeyes.configuration import Config
    from safeeyes.surveys import question_choices

    data = utility.load_json(utility.SYSTEM_CONFIG_FILE_PATH)
    data["survey_archived"] = ["distraction"]
    data["short_breaks"] = [{"name": "旧提醒", "distraction_stats": True}]
    if upgrade:
        data["meta"]["config_version"] = "0.0.1"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(utility, "CONFIG_FILE_PATH", str(path))
    monkeypatch.setattr(utility, "merge_plugins", lambda config: None)
    monkeypatch.setattr(Config, "_create_startup_entry", lambda **kwargs: None)
    loaded = Config.load()
    assert loaded.get("short_breaks")[0]["question_type"] == "none"
    assert loaded.get("short_breaks")[0]["distraction_stats"] is False
    assert "distraction" in loaded.get("survey_options")
    assert "distraction" in loaded.get("survey_questions")
    assert "distraction" not in dict(question_choices(loaded))
    prompt = SurveyPrompt(SurveyStore(tmp_path / "stats.json"))
    prompt.configure(
        loaded.get("survey_options"),
        loaded.get("survey_questions"),
        loaded.get("survey_archived"),
    )
    prompt.begin("one", True, "distraction")
    assert not prompt.enabled


def test_custom_question_and_renaming_keep_statistical_identity(tmp_path):
    catalog = default_options()
    catalog["custom_neck"] = [
        {"id": "none", "label": "无", "active": True},
        {"id": "mild", "label": "轻微", "active": True},
        {"id": "severe", "label": "明显", "active": True},
    ]
    store = SurveyStore(tmp_path / "stats.json")
    prompt = SurveyPrompt(store)
    prompt.configure(catalog, {"custom_neck": "颈肩是否不舒服？"})
    prompt.begin("one", True, "custom_neck")
    assert prompt.enabled
    assert prompt.question == "颈肩是否不舒服？"
    prompt.submit("one", "mild")
    restored = SurveyPrompt(store)
    restored.configure(catalog, {"custom_neck": "你的颈肩感觉如何？"})
    restored.begin("two", True, "custom_neck")
    assert restored.question == "你的颈肩感觉如何？"
    assert "轻微 1 次" in restored.summary()
    restored.submit("two", "none")
    assert store.today("custom_neck") == {"mild": 1, "none": 1}


def test_guidance_is_randomized_once_only_after_successful_answer(
    tmp_path, monkeypatch
):
    from unittest.mock import Mock

    choose = Mock(side_effect=lambda phrases: phrases[-1])
    monkeypatch.setattr("safeeyes.surveys.random.choice", choose)
    catalog = default_options()
    catalog["tension"][1]["guidance"] = ["放松肩膀", "缓慢呼气\n松开下颌"]
    prompt = SurveyPrompt(SurveyStore(tmp_path / "stats.json"))
    prompt.configure(catalog)
    prompt.begin("one", True, "tension")
    assert not prompt.guidance
    prompt.submit("stale", "tense")
    choose.assert_not_called()
    with monkeypatch.context() as patch:
        patch.setattr(prompt.store, "record", Mock(side_effect=OSError("disk full")))
        with pytest.raises(OSError):
            prompt.submit("one", "tense")
    choose.assert_not_called()
    assert not prompt.guidance
    prompt.submit("one", "tense")
    assert prompt.guidance == "缓慢呼气\n松开下颌"
    prompt.begin("one", True, "tension")
    prompt.submit("one", "relaxed")
    choose.assert_called_once_with(["放松肩膀", "缓慢呼气\n松开下颌"])
    assert prompt.guidance == "缓慢呼气\n松开下颌"
    prompt.begin("two", True, "tension")
    assert not prompt.guidance
    prompt.submit("two", "relaxed")
    assert not prompt.guidance
    assert choose.call_count == 1


def test_one_guidance_phrase_and_option_rename_keep_the_association(tmp_path):
    catalog = default_options()
    catalog["posture"][1].update(label="需要调整", guidance=["坐正，放松肩膀"])
    prompt = SurveyPrompt(SurveyStore(tmp_path / "stats.json"))
    prompt.configure(catalog)
    prompt.begin("one", True, "posture")
    prompt.submit("one", "bad")
    assert prompt.guidance == "坐正，放松肩膀"


def test_legacy_import_preserves_all_days_and_never_imports_twice(
    tmp_path, time_machine
):
    time_machine.move_to("2026-11-12 12:00:00", tick=False)
    legacy = tmp_path / "distraction_stats.json"
    legacy.write_text(
        json.dumps(
            {
                "2026-11-11": {"yes": 3, "no": 5},
                "2026-11-12": {"yes": 1, "no": 2},
            }
        )
    )
    store = SurveyStore(tmp_path / "health_stats.json", legacy)
    assert store.today("distraction") == {"yes": 1, "no": 2}
    store.record("posture", "bad")
    store.record("distraction", "no")
    restored = SurveyStore(store.path, legacy)
    assert restored.today("distraction") == {"yes": 1, "no": 3}
    assert restored.today("posture") == {"bad": 1}
    assert (
        json.loads(store.path.read_text())["days"]["2026-11-11"]["distraction"]["yes"]
        == 3
    )
    assert json.loads(legacy.read_text())["2026-11-12"]["no"] == 2


def test_custom_levels_rename_deletion_and_new_identity(tmp_path):
    prompt = SurveyPrompt(SurveyStore(tmp_path / "stats.json"))
    catalog = default_options()
    catalog["tension"].append({"id": "mild", "label": "轻度紧绷", "active": True})
    prompt.configure(catalog)
    prompt.begin("one", True, "tension")
    prompt.submit("one", "mild")
    prompt.begin("two", True, "tension")
    prompt.submit("two", "relaxed")
    assert "50%" in prompt.summary()
    catalog["tension"][-1]["label"] = "稍微紧绷"
    catalog["tension"][-1]["active"] = False
    prompt.configure(catalog)
    assert "稍微紧绷（已停用） 1 次" in prompt.summary()
    assert "mild" not in dict((key, label) for label, key in prompt.choices)
    catalog["tension"].append({"id": "new-id", "label": "稍微紧绷", "active": True})
    prompt.configure(catalog)
    prompt.begin("three", True, "tension")
    prompt.submit("three", "new-id")
    assert prompt.store.today("tension") == {"mild": 1, "relaxed": 1, "new-id": 1}


@pytest.mark.parametrize("kind", ["distraction", "posture", "tension", "fatigue"])
def test_all_categories_deduplicate_redraw_and_stale_callbacks(tmp_path, kind):
    prompt = SurveyPrompt(SurveyStore(tmp_path / "stats.json"))
    prompt.begin("one", True, kind)
    first, second = [value for label, value in prompt.choices]
    prompt.submit("one", first)
    prompt.begin("one", True, kind)
    prompt.submit("one", second)
    assert prompt.store.today(kind) == {first: 1}
    prompt.begin("two", True, kind)
    prompt.submit("one", second)
    assert prompt.answer is None
    prompt.submit("two", "unknown")
    assert prompt.answer is None


def test_bad_file_is_preserved_and_error_allows_retry(tmp_path):
    path = tmp_path / "stats.json"
    path.write_text("[]")
    prompt = SurveyPrompt(SurveyStore(path))
    prompt.begin("one", True, "fatigue")
    with pytest.raises(ValueError):
        prompt.submit("one", "tired")
    assert prompt.answer is None
    assert prompt.error
    assert path.read_text() == "[]"
    assert "读取失败" in prompt.summary()


def test_option_validation_requires_two_nonempty_distinct_active_labels():
    catalog = default_options()
    catalog["posture"][0]["active"] = False
    with pytest.raises(ValueError):
        validate_options(catalog)


def test_custom_choices_survive_configuration_version_upgrade(tmp_path, monkeypatch):
    from safeeyes import utility
    from safeeyes.configuration import Config

    data = utility.load_json(utility.SYSTEM_CONFIG_FILE_PATH)
    data["meta"]["config_version"] = "0.0.1"
    data["survey_options"] = default_options()
    data["survey_options"]["fatigue"].append(
        {"id": "custom", "label": "轻度疲劳", "active": True}
    )
    data["survey_options"]["custom_neck"] = default_options()["distraction"]
    data["survey_questions"]["custom_neck"] = "颈肩是否不舒服？"
    data["survey_questions"]["fatigue"] = "你现在累了吗？"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(utility, "CONFIG_FILE_PATH", str(path))
    monkeypatch.setattr(utility, "merge_plugins", lambda config: None)
    monkeypatch.setattr(Config, "_create_startup_entry", lambda **kwargs: None)
    loaded = Config.load()
    assert loaded.get("survey_options")["fatigue"][-1]["id"] == "custom"
    assert "custom_neck" in loaded.get("survey_options")
    assert loaded.get("survey_questions")["custom_neck"] == "颈肩是否不舒服？"
    assert loaded.get("survey_questions")["fatigue"] == "你现在累了吗？"
    catalog = default_options()
    catalog["posture"][0]["label"] = "不良"
    with pytest.raises(ValueError):
        validate_options(catalog)


def test_new_day_and_disabled_prompt(tmp_path, time_machine):
    time_machine.move_to("2026-11-12 12:00:00", tick=False)
    prompt = SurveyPrompt(SurveyStore(tmp_path / "stats.json"))
    prompt.begin("one", False, "posture")
    prompt.submit("one", "bad")
    assert not prompt.store.path.exists()
    prompt.begin("two", True, "posture")
    prompt.submit("two", "bad")
    time_machine.shift(86400)
    assert "暂无记录" in prompt.summary()


def test_categorical_answer_survives_progress_checkpoint_restore(timer, tmp_path):
    from safeeyes.activity import ActivityController
    from safeeyes.progress import ProgressFile

    start_break(timer)
    token = timer.core._taking_break.occurrence_id
    store = SurveyStore(tmp_path / "stats.json")
    prompt = SurveyPrompt(store)
    catalog = default_options()
    catalog["posture"][1]["guidance"] = ["坐正后放松肩膀\n保持自然呼吸"]
    prompt.configure(catalog)
    prompt.begin(token, True, "posture")
    prompt.submit(token, "bad")
    path = tmp_path / "timer.json"
    file = ProgressFile(
        path, timer.controller, lambda: timer.cfg, prompt, boot=lambda: "old"
    )
    file.save(True)
    timer.core.stop()
    timer.core.initialize(timer.cfg)
    activity = ActivityController(timer.core, lambda: 1200, timer.pause, timer.resume)
    restored = SurveyPrompt(store)
    timer.shift(10)
    assert ProgressFile(
        path, activity, lambda: timer.cfg, restored, boot=lambda: "new"
    ).restore()
    restored.begin(timer.core._taking_break.occurrence_id, True, "posture")
    assert restored.answer == "bad"
    assert restored.guidance == "坐正后放松肩膀\n保持自然呼吸"
    restored.submit(token, "good")
    assert store.today("posture") == {"bad": 1}
