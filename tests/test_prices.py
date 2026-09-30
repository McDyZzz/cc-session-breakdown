"""Price table: model resolution, fast mode, weighted units, and pricing.override.json."""
import json
import os
import time

import pytest

from conftest import assistant_line, user_line


def test_resolve_model_ignores_date_suffix(mod):
    assert mod.resolve_model("claude-haiku-4-5-20251001") == ("claude-haiku-4-5", True)


def test_resolve_model_ignores_1m_suffix(mod):
    assert mod.resolve_model("claude-opus-5-5[1m]") == ("claude-opus-5-5", True)
    assert mod.resolve_model("claude-sonnet-4-5-20250929[1m]") == ("claude-sonnet-4-5", True)


def test_unknown_model_uses_newest_of_its_family(mod):
    assert mod.resolve_model("claude-opus-9-9") == ("claude-opus-5-5", False)
    assert mod.resolve_model("claude-fable-7") == ("claude-fable-5-1", False)
    assert mod.resolve_model("claude-haiku-6") == ("claude-haiku-4-5", False)


def test_model_of_unknown_family_uses_sonnet_5(mod):
    assert mod.resolve_model("claude-lyric-1") == ("claude-sonnet-5", False)
    assert mod.resolve_model("some-other-model") == ("claude-sonnet-5", False)


def test_fast_multiplier(mod):
    fast = {"input_tokens": 1000, "speed": "fast"}
    assert mod.speed_factor("claude-opus-5-5", fast) == 2.0
    assert mod.speed_factor("claude-opus-5-5", {"input_tokens": 1000}) == 1.0
    # A model without fast_multiplier in the table stays at 1.
    assert mod.speed_factor("claude-sonnet-5", fast) == 1.0
    std_units, _ = mod.weigh("claude-opus-5-5", {"input_tokens": 1000})
    fast_units, _ = mod.weigh("claude-opus-5-5", fast)
    assert fast_units == pytest.approx(2 * std_units)


def test_weighted_units_formula(mod):
    usage = {"input_tokens": 1000, "output_tokens": 10, "cache_read_input_tokens": 1000,
             "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 200}}
    # Opus 5.5: base 4.0 $/MTok (ratio 2 to Sonnet 5), write_5m 1.25, write_1h 2, read 0.05, out 5.
    ie = 1000 + 100 * 1.25 + 200 * 2.0 + 1000 * 0.05 + 10 * 5.0
    units, tokens = mod.weigh("claude-opus-5-5", usage)
    assert tokens == pytest.approx(ie)
    assert units == pytest.approx(ie * 2.0)
    # Sonnet 5 is the unit: 1 input token = 1 unit.
    assert mod.weigh("claude-sonnet-5", {"input_tokens": 500})[0] == pytest.approx(500)
    # Fable 5: base 10 $/MTok = 5 units per input token.
    assert mod.weigh("claude-fable-5", {"input_tokens": 100})[0] == pytest.approx(500)


def test_legacy_cache_creation_field_counts_as_5m_write(mod):
    usage = {"input_tokens": 0, "cache_creation_input_tokens": 400}
    assert mod.weigh("claude-sonnet-5", usage)[0] == pytest.approx(400 * 1.25)


def test_web_search_units(mod):
    usage = {"input_tokens": 0, "server_tool_use": {"web_search_requests": 3}}
    # $0.01 per request; 1 unit = $2 / 1e6.
    assert mod.search_units(usage) == pytest.approx(3 * 0.01 * 1e6 / 2.0)
    assert mod.weigh("claude-sonnet-5", usage)[0] == pytest.approx(15_000)


def write_override(mod, obj_or_text):
    os.makedirs(mod.DATA_DIR, exist_ok=True)
    text = obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text)
    with open(os.path.join(mod.DATA_DIR, "pricing.override.json"), "w") as f:
        f.write(text)


NEW_OPUS = {"input": 6.0, "write_5m": 1.25, "write_1h": 2.0, "cache_read": 0.1, "output": 5.0}


def test_valid_override_replaces_model_and_web_search_price(mod):
    write_override(mod, {"web_search_usd_per_request": 0.02,
                         "models": {"claude-opus-5-5": NEW_OPUS,
                                    "claude-example-1": dict(NEW_OPUS, input=1.0)}})
    table = mod.price_table()
    assert table["override_error"] is None
    assert table["models"]["claude-opus-5-5"] == (6.0, 1.25, 2.0, 0.1, 5.0)
    # The entry is replaced as a whole: its bundled fast_multiplier is gone.
    assert table["fast"]["claude-opus-5-5"] == 1.0
    assert table["models"]["claude-example-1"][0] == 1.0
    assert table["search_usd"] == 0.02
    # Models only in the bundled table stay.
    assert table["models"]["claude-sonnet-5"] == (2.0, 1.25, 2.0, 0.1, 5.0)
    assert table["source"].endswith("+ pricing.override.json")


@pytest.mark.parametrize("content", [
    "{not json",
    {"models": {"claude-opus-5-5": NEW_OPUS, "claude-bad": {"input": "6"}}},
    {"models": []},
    {"web_search_usd_per_request": "cheap"},
    {"models": {"claude-opus-5-5": dict(NEW_OPUS, fast_multiplier=True)}},
    [1, 2, 3],
    {"models": {"claude-opus-5-5": dict(NEW_OPUS, input=True)}},
    {"models": {"claude-opus-5-5": dict(NEW_OPUS, input=0)}},
    {"models": {"claude-opus-5-5": dict(NEW_OPUS, input=-6.0)}},
    {"models": {"claude-opus-5-5": dict(NEW_OPUS, write_5m=0)}},
    {"models": {"claude-opus-5-5": dict(NEW_OPUS, write_1h=-2.0)}},
    {"models": {"claude-opus-5-5": dict(NEW_OPUS, cache_read=0.0)}},
    {"models": {"claude-opus-5-5": dict(NEW_OPUS, output=-5)}},
    {"models": {"claude-opus-5-5": dict(NEW_OPUS, fast_multiplier=0)}},
    {"models": {"claude-opus-5-5": dict(NEW_OPUS, fast_multiplier=-2.0)}},
    '{"models": {"claude-opus-5-5": {"input": NaN, "write_5m": 1.25, "write_1h": 2.0, "cache_read": 0.1, "output": 5.0}}}',
    '{"models": {"claude-opus-5-5": {"input": Infinity, "write_5m": 1.25, "write_1h": 2.0, "cache_read": 0.1, "output": 5.0}}}',
    '{"models": {"claude-opus-5-5": {"input": 6.0, "write_5m": 1.25, "write_1h": 2.0, "cache_read": 0.1, "output": -Infinity}}}',
    '{"models": {"claude-opus-5-5": {"input": 1e999, "write_5m": 1.25, "write_1h": 2.0, "cache_read": 0.1, "output": 5.0}}}',
    '{"models": {"claude-opus-5-5": {"input": 1' + "0" * 400 + ', "write_5m": 1.25, "write_1h": 2.0, "cache_read": 0.1, "output": 5.0}}}',
    {"models": {"claude-opus-5-5": dict(NEW_OPUS, input=1e308)}},
    {"models": {"claude-opus-5-5": dict(NEW_OPUS, input=5e-324)}},
    {"models": {"claude-opus-5-5": dict(NEW_OPUS, output=1e308)}},
    {"models": {"claude-opus-5-5": dict(NEW_OPUS, cache_read=5e-324)}},
    {"web_search_usd_per_request": 1e308},
    {"web_search_usd_per_request": -0.01},
    {"web_search_usd_per_request": True},
    '{"web_search_usd_per_request": NaN}',
    '{"web_search_usd_per_request": Infinity}',
    '{"web_search_usd_per_request": 1' + "0" * 400 + '}',
])
def test_invalid_override_is_ignored_as_a_whole(mod, content):
    write_override(mod, content)
    table = mod.price_table()
    assert table["override_error"]
    assert table["models"]["claude-opus-5-5"][0] == 4.0
    assert table["search_usd"] == 0.01
    assert "override" not in table["source"]


# Each limit with a value on it (valid, limits are inclusive) and a value just past it.
RANGE_CASES = [("input", 0.001, 0.00099), ("input", 10000, 10000.01)]
for _f in ("write_5m", "write_1h", "cache_read", "output", "fast_multiplier"):
    RANGE_CASES += [(_f, 0.001, 0.00099), (_f, 1000, 1000.01)]


@pytest.mark.parametrize("field,inside,outside", RANGE_CASES)
def test_override_price_range_limits(mod, field, inside, outside):
    write_override(mod, {"models": {"claude-opus-5-5": dict(NEW_OPUS, **{field: inside})}})
    assert mod.price_table()["override_error"] is None
    mod._PRICE_TABLE, mod._RESOLVED = None, {}
    write_override(mod, {"models": {"claude-opus-5-5": dict(NEW_OPUS, **{field: outside})}})
    table = mod.price_table()
    assert table["override_error"]
    assert table["models"]["claude-opus-5-5"][0] == 4.0


@pytest.mark.parametrize("inside,outside", [(0, -0.00001), (1000, 1000.01)])
def test_override_web_search_range_limits(mod, inside, outside):
    write_override(mod, {"web_search_usd_per_request": inside})
    assert mod.price_table()["override_error"] is None
    mod._PRICE_TABLE, mod._RESOLVED = None, {}
    write_override(mod, {"web_search_usd_per_request": outside})
    table = mod.price_table()
    assert table["override_error"]
    assert table["search_usd"] == 0.01


def test_free_web_search_override_is_valid(mod):
    write_override(mod, {"web_search_usd_per_request": 0})
    table = mod.price_table()
    assert table["override_error"] is None
    assert table["search_usd"] == 0.0


def test_coefficients_follow_override_changes(mod):
    # Week·All from two samples: 3,000 Opus 5.5 input tokens over 3 points.
    t0 = time.time() - 1200
    reset = t0 + 86400
    cum = lambda n: {"claude-opus-5-5": {"std": [n, 0, 0, 0, 0], "fast": [0] * 5, "ws": 0}}
    samples = [{"v": 2, "t": t0 + i * 600, "state_id": "state-a", "cum": cum(n),
                "bars": {"week_all": {"pct": pct, "reset": "", "reset_ts": reset}}}
               for i, (n, pct) in enumerate([(0, 10.0), (3000, 13.0)])]
    with open(os.path.join(mod.DATA_DIR, "samples.jsonl"), "w") as f:
        f.write("".join(json.dumps(x) + "\n" for x in samples))

    def week_all():
        mod._PRICE_TABLE, mod._RESOLVED = None, {}
        return mod.calibrated_coefficients()["week_all"]["value"]

    # Bundled Opus 5.5 base $4 = 2 units per token: 6,000 units / 3 points.
    assert week_all() == pytest.approx(2000.0)
    write_override(mod, {"models": {"claude-opus-5-5": NEW_OPUS}})  # base $6 = 3 units
    assert week_all() == pytest.approx(3000.0)
    os.remove(os.path.join(mod.DATA_DIR, "pricing.override.json"))
    assert week_all() == pytest.approx(2000.0)


def test_invalid_override_prints_warning_line(env):
    now = time.time()
    env.data.mkdir(parents=True)
    (env.data / "pricing.override.json").write_text('{"models": {"claude-opus-5-5": {"input": 1}}}')
    env.session("aaaa1111-0000-4000-8000-000000000001",
                [user_line("aaaa1111-0000-4000-8000-000000000001", now - 60, "Tidy the build script"),
                 assistant_line("aaaa1111-0000-4000-8000-000000000001", now - 50, "msg_a1", "claude-sonnet-5")])
    res = env.run("--session", "aaaa1111-0000-4000-8000-000000000001")
    assert res.returncode == 0, res.stderr
    lines = [l for l in res.stdout.splitlines() if "pricing.override.json is invalid and was ignored" in l]
    assert len(lines) == 1


def test_invalid_override_warning_stays_on_one_line(env):
    now = time.time()
    sid = "aaaa1111-0000-4000-8000-000000000002"
    env.data.mkdir(parents=True)
    (env.data / "pricing.override.json").write_text(
        json.dumps({"models": {"claude-bad\nsecond line\nthird line": {"input": 1}}}))
    env.session(sid, [user_line(sid, now - 60, "Tidy the build script"),
                      assistant_line(sid, now - 50, "msg_a2", "claude-sonnet-5")])
    res = env.run("--session", sid)
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    idx = [i for i, l in enumerate(lines) if "pricing.override.json is invalid and was ignored" in l]
    assert len(idx) == 1
    assert "claude-bad second line third line" in lines[idx[0]]
    assert not any(l.startswith(("second line", "third line")) for l in lines)
