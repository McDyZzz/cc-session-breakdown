"""The /usage text parser and reset-time parsing. The labels are pinned as exact text."""
from datetime import datetime, timezone

from conftest import USAGE_TEXT

# 2026-09-29 10:00 UTC
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc).timestamp()


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc).timestamp()


def test_all_three_bars(mod):
    bars = mod.parse_usage(USAGE_TEXT, NOW)
    assert set(bars) == {"5h", "week_all", "week_fable"}
    assert bars["5h"]["pct"] == 12.0
    assert bars["5h"]["reset"] == "3pm (UTC)"
    assert bars["5h"]["reset_ts"] == utc(2026, 9, 29, 15, 0)
    assert bars["week_all"]["pct"] == 40.0
    assert bars["week_all"]["reset_ts"] == utc(2026, 10, 2, 9, 0)
    assert bars["week_fable"]["pct"] == 7.0


def test_no_fable_bar_keeps_sample(mod):
    text = "\n".join(l for l in USAGE_TEXT.splitlines() if "Fable" not in l)
    bars = mod.parse_usage(text, NOW)
    assert set(bars) == {"5h", "week_all"}


def test_unknown_weekly_bar_is_ignored(mod):
    text = USAGE_TEXT + "Current week (Opus): 12% used · resets Oct 2 at 9am (UTC)\n"
    bars = mod.parse_usage(text, NOW)
    assert set(bars) == {"5h", "week_all", "week_fable"}
    assert bars["week_all"]["pct"] == 40.0


def test_decimal_percent_and_no_reset(mod):
    text = "Current session: 2.5% used\nCurrent week (all models): 0% used\n"
    bars = mod.parse_usage(text, NOW)
    assert bars["5h"] == {"pct": 2.5, "reset": "", "reset_ts": None}
    assert bars["week_all"]["pct"] == 0.0


def test_missing_current_session_gives_none(mod):
    text = "\n".join(l for l in USAGE_TEXT.splitlines() if "Current session" not in l)
    assert mod.parse_usage(text, NOW) is None


def test_missing_week_all_gives_none(mod):
    text = "\n".join(l for l in USAGE_TEXT.splitlines() if "all models" not in l)
    assert mod.parse_usage(text, NOW) is None


def test_empty_text_gives_none(mod):
    assert mod.parse_usage("", NOW) is None


def test_parse_reset_with_date_and_zone(mod):
    assert mod.parse_reset("Oct 2 at 9am (UTC)", NOW) == utc(2026, 10, 2, 9, 0)
    assert mod.parse_reset("Sep 27 at 1:09am (UTC)", NOW) == utc(2026, 9, 27, 1, 9)
    # Named zone: Melbourne is UTC+10 on 2026-09-27 01:09 (daylight time starts Oct 4).
    assert mod.parse_reset("Sep 27 at 1:09am (Australia/Melbourne)", NOW) == utc(2026, 9, 26, 15, 9)


def test_parse_reset_picks_nearest_year(mod):
    # Early January seen from late December is next year.
    dec = utc(2026, 12, 30, 12, 0)
    assert mod.parse_reset("Jan 2 at 9am (UTC)", dec) == utc(2027, 1, 2, 9, 0)


def test_parse_reset_time_only(mod):
    assert mod.parse_reset("3pm (UTC)", NOW) == utc(2026, 9, 29, 15, 0)
    assert mod.parse_reset("12am (UTC)", NOW) == utc(2026, 9, 30, 0, 0)
    # Up to 10 minutes in the past stays today; older moves to tomorrow.
    assert mod.parse_reset("9:55am (UTC)", NOW) == utc(2026, 9, 29, 9, 55)
    assert mod.parse_reset("9am (UTC)", NOW) == utc(2026, 9, 30, 9, 0)


def test_parse_reset_bad_input(mod):
    assert mod.parse_reset("", NOW) is None
    assert mod.parse_reset("soon", NOW) is None
