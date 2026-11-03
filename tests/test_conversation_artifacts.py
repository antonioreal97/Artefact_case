"""Offline structure/privacy checks; live observations are never replayed or graded."""

import json
import re
from pathlib import Path

import pytest

from scripts.demo_conversations import _english

ROOT = Path(__file__).resolve().parents[1] / "examples/conversations"
LIVE = Path(__file__).resolve().parents[1] / "evals/live/english-conversations"


def test_untranslated_demo_dialogue_fails_export():
    with pytest.raises(ValueError, match="Missing demo English translation"):
        _english("An unexpected reply with no fixture translation")


def test_live_recordings_preserve_api_fields_and_omit_personal_inputs():
    paths = sorted(path for path in LIVE.glob("*.md") if path.name != "README.md")
    assert len(paths) == 2
    for path in paths:
        content = path.read_text()
        record = json.loads(path.with_suffix(".json").read_text())
        assert "real model" in content
        assert "gpt-5-mini" in content
        assert re.search(r"commerce-privacy-v\d+", content)
        assert "non-deterministic" in content and "recorded once" in content
        assert "not asserted by tests" in content
        assert 3 <= len(record["turns"]) <= 4
        assert content.count("## Turn ") == len(record["turns"])
        for turn in record["turns"]:
            assert set(turn) == {
                "customer",
                "reply",
                "welcome_message",
                "tools_used",
                "sources",
                "handoff",
                "grounded",
            }
            assert turn["reply"] in content
            assert turn["customer"] in content
            assert not re.search(r"@|\b(?:order|pedido)\s*#?\s*\d+", turn["customer"], re.I)
            assert "[[" not in turn["reply"]
            if turn["welcome_message"]:
                assert turn["welcome_message"] in content
    for path in LIVE.rglob("*.json"):
        for turn in json.loads(path.read_text())["turns"]:
            assert "@" not in turn["customer"]
            assert not re.search(r"\b(?:order|pedido)\s*#?\s*\d+", turn["customer"], re.I)
