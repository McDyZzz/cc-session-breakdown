"""Quota coefficients: weighted units per 1% of each bar, from sample pairs."""
import pytest

from conftest import sample

T0 = 1_790_000_000.0
RESET = T0 + 86400


def week(*points, state_id="state-a"):
    """Samples with only a Week·All bar: points are (sonnet input tokens, pct)."""
    return [sample(T0 + i * 600, units, {"week_all": (pct, RESET)}, state_id=state_id)
            for i, (units, pct) in enumerate(points)]


def test_zero_intervals_is_uncalibrated(mod):
    coefs = mod.compute_coefficients(week((0, 10.0)), {})
    assert coefs["week_all"] == {"value": None, "source": "uncalibrated", "intervals": 0}
    assert coefs["5h"]["value"] is None
    assert mod.compute_coefficients([], None)["week_fable"]["source"] == "uncalibrated"


def test_one_interval_is_single(mod):
    coefs = mod.compute_coefficients(week((0, 10.0), (3000, 13.0)), {})
    assert coefs["week_all"] == {"value": pytest.approx(1000.0), "source": "single", "intervals": 1}


def test_two_intervals_give_p75(mod):
    samples = week((0, 10.0), (3000, 13.0), (9000, 16.0))  # 1000 and 2000 per 1%
    coefs = mod.compute_coefficients(samples, {})
    assert coefs["week_all"]["source"] == "p75"
    assert coefs["week_all"]["intervals"] == 2
    assert coefs["week_all"]["value"] == pytest.approx(1750.0)


def test_rise_below_minimum_is_skipped(mod):
    # 10 -> 12 is below the 3-point minimum, so the pair is 10 -> 14 with 4000 units.
    samples = week((0, 10.0), (1000, 12.0), (4000, 14.0))
    coefs = mod.compute_coefficients(samples, {})
    assert coefs["week_all"]["intervals"] == 1
    assert coefs["week_all"]["value"] == pytest.approx(1000.0)


def test_5h_needs_five_points(mod):
    samples = [sample(T0, 0, {"5h": (10.0, RESET)}), sample(T0 + 600, 4000, {"5h": (14.0, RESET)})]
    assert mod.compute_coefficients(samples, {})["5h"]["value"] is None


def test_pairs_across_reset_windows_or_states_are_not_used(mod):
    other_window = [sample(T0, 0, {"week_all": (10.0, RESET)}),
                    sample(T0 + 600, 3000, {"week_all": (13.0, RESET + 7 * 86400)})]
    assert mod.compute_coefficients(other_window, {})["week_all"]["value"] is None
    other_state = week((0, 10.0)) + week((3000, 13.0), state_id="state-b")
    other_state[1]["t"] = T0 + 600
    assert mod.compute_coefficients(other_state, {})["week_all"]["value"] is None


def test_last_value_is_kept_without_new_intervals(mod):
    previous = {"week_all": {"value": 1234.0, "source": "p75", "intervals": 3}}
    coefs = mod.compute_coefficients([], previous)
    assert coefs["week_all"] == {"value": 1234.0, "source": "saved", "intervals": 0}


def test_week_fable_counts_fable_units_only(mod):
    samples = [sample(T0, 0, {"week_fable": (1.0, RESET)}),
               sample(T0 + 600, 50_000, {"week_fable": (4.0, RESET)}, fable_input=3000)]
    # 3000 Fable 5 input tokens = 15000 units over 3 points; Sonnet units do not count.
    assert mod.compute_coefficients(samples, {})["week_fable"]["value"] == pytest.approx(5000.0)
