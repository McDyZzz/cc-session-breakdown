"""Model families, versions, display order, and context windows."""
import pytest


@pytest.mark.parametrize("model, fam", [
    ("claude-fable-5-1", "Fable"),
    ("claude-opus-5-5[1m]", "Opus"),
    ("claude-sonnet-4-5-20250929", "Sonnet"),
    ("claude-haiku-4-5-20251001", "Haiku"),
    ("claude-mythos-5", "Mythos"),
    ("claude-lyric-1", "Other"),
    ("", "Other"),
])
def test_family(mod, model, fam):
    assert mod.family(model) == fam


@pytest.mark.parametrize("model, version", [
    ("claude-opus-5-5", "5.5"),
    ("claude-opus-5", "5"),
    ("claude-haiku-4-5-20251001", "4.5"),
    ("claude-opus-5-5[1m]", "5.5"),
    ("claude-sonnet-5-20260101", "5"),
    ("gpt-like-model", ""),
])
def test_model_version(mod, model, version):
    assert mod.model_version(model) == version


def test_family_order(mod):
    fams = ["Other", "Haiku", "Mythos", "Sonnet", "Fable", "Opus"]
    units = {"Other": 5.0, "Mythos": 50.0}
    assert mod.family_order(fams, units) == ["Fable", "Opus", "Sonnet", "Haiku", "Mythos", "Other"]


def test_family_title_lists_versions_newest_first(mod):
    assert mod.family_title("Opus", {"claude-opus-5", "claude-opus-5-5"}) == "Opus 5.5 + 5"
    assert mod.family_title("Opus", {"claude-opus-5-5", "claude-opus-5-5[1m]"}) == "Opus"


@pytest.mark.parametrize("model, window", [
    ("claude-haiku-4-5", 200_000),
    ("claude-haiku-4-5-20251001", 200_000),
    ("claude-opus-4-5", 200_000),
    ("claude-opus-4-5-20251101", 200_000),
    ("claude-sonnet-4-5", 200_000),
    ("claude-sonnet-4-5[1m]", 1_000_000),
    ("claude-opus-4-6", 1_000_000),
    ("claude-opus-5-5", 1_000_000),
    ("claude-sonnet-5", 1_000_000),
    ("claude-fable-5-1", 1_000_000),
    ("claude-lyric-1", None),
])
def test_context_window(mod, model, window):
    assert mod.context_window(model) == window
