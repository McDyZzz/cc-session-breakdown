"""`--estimate`: one line and exit 0, or exit 1 with no estimate."""
import time

import pytest

from conftest import assistant_line, calibrated_samples, user_line

SID = "eeee5555-0000-4000-8000-000000000005"


def make_session(env, models):
    now = time.time()
    records = [user_line(SID, now - 300, "Write the changelog entry")]
    for i, m in enumerate(models):
        records.append(assistant_line(SID, now - 200 + i, f"msg_e{i}", m,
                                      inp=10, read=50_000, write=2000, out=800))
    env.session(SID, records)
    return now


def estimate(env, *extra):
    return env.run("--estimate", "--context", "200K", "--turns", "10", "--session", SID, *extra)


# Expected amounts, by hand from the fixture: each call has 50,000 cache-read, 2,000
# 5m-write, and 800 output tokens. Sonnet 5 (base $2, ratio 1): per call at 200K =
# 200,000 x 0.1 + 2,000 x 1.25 + 800 x 5 = 26,500 units; at 20K = 8,500 units.
# Fable 5 (base $10, ratio 5): 132,500 and 42,500 units. 1 unit = $2 / 1e6.


def test_uncalibrated_one_line(env):
    make_session(env, ["claude-opus-5-5", "claude-sonnet-5"])
    res = estimate(env)
    assert res.returncode == 0, res.stderr
    # 2 calls per turn x 10 turns = 20 calls: 530,000 units ($1.06), after 170,000 ($0.34).
    assert res.stdout.splitlines() == [
        "Next 10 turns (about 20 calls, 200K context, Sonnet 5): ≈ 530K weighted units, $1.06; "
        "after /compact to about 20K: ≈ 170K weighted units, $0.34 (not calibrated)"]


def test_calibrated_one_line(env):
    now = make_session(env, ["claude-sonnet-5"])
    env.write_samples(calibrated_samples(now))
    res = estimate(env)
    assert res.returncode == 0, res.stderr
    # 10 calls: 265,000 and 85,000 units; Week·All = 1,000 units per 1%.
    assert res.stdout.splitlines() == [
        "Next 10 turns (about 10 calls, 200K context, Sonnet 5): Week·All ≈ 265.00%; "
        "after /compact to about 20K: Week·All ≈ 85.00%"]


def test_uses_model_of_last_call(env):
    make_session(env, ["claude-sonnet-5", "claude-opus-5-5"])
    line = estimate(env).stdout.strip()
    assert "Opus 5.5" in line and "Sonnet" not in line


def test_fable_session_adds_week_fable(env):
    now = make_session(env, ["claude-fable-5"])
    env.write_samples(calibrated_samples(now, fable=True))
    res = estimate(env)
    assert res.returncode == 0, res.stderr
    # 10 calls: 1,325,000 and 425,000 units; Week·All = 6,000 and Week·Fable = 5,000 units per 1%.
    assert res.stdout.splitlines() == [
        "Next 10 turns (about 10 calls, 200K context, Fable 5): "
        "Week·All ≈ 220.83%, Week·Fable ≈ 265.00%; "
        "after /compact to about 20K: Week·All ≈ 70.83%, Week·Fable ≈ 85.00%"]


def test_unknown_session_exits_1(env):
    make_session(env, ["claude-sonnet-5"])
    res = env.run("--estimate", "--context", "200000", "--session", "ffffffff-no-such-session")
    assert res.returncode == 1
    assert res.stdout.startswith("ERROR")


def test_fable_session_without_samples_leaves_out_week_fable(env):
    # Week·All not calibrated: the line gives weighted units and USD and ends with the
    # not-calibrated suffix. Week·Fable joins only when Week·Fable itself is calibrated.
    make_session(env, ["claude-fable-5"])
    res = estimate(env)
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == [
        "Next 10 turns (about 10 calls, 200K context, Fable 5): ≈ 1.3M weighted units, $2.65; "
        "after /compact to about 20K: ≈ 425K weighted units, $0.85 (not calibrated)"]


def test_week_fable_joins_uncalibrated_line_when_calibrated(env):
    now = make_session(env, ["claude-fable-5"])
    samples = calibrated_samples(now, fable=True)
    for smp in samples:  # keep only the Week·Fable bar: Week·All stays not calibrated
        smp["bars"] = {"week_fable": smp["bars"]["week_fable"]}
    env.write_samples(samples)
    res = estimate(env)
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == [
        "Next 10 turns (about 10 calls, 200K context, Fable 5): "
        "≈ 1.3M weighted units, $2.65, Week·Fable ≈ 265.00%; "
        "after /compact to about 20K: ≈ 425K weighted units, $0.85, Week·Fable ≈ 85.00% (not calibrated)"]


def test_estimate_with_mark_price_checked_is_a_usage_error(env):
    make_session(env, ["claude-sonnet-5"])
    res = estimate(env, "--mark-price-checked")
    assert res.returncode == 2
    assert "usage: ccsb" in res.stderr
    assert res.stdout == ""


@pytest.mark.parametrize("mode", [["--sample-if-due"], ["--record-sample", "usage.txt"]])
def test_estimate_with_a_sample_mode_is_a_usage_error(env, mode):
    make_session(env, ["claude-sonnet-5"])
    res = env.run("--estimate", "--context", "200K", *mode)
    assert res.returncode == 2
    assert "usage: ccsb" in res.stderr
    assert "cannot be used together" in res.stderr
    assert res.stdout == ""
    assert env.samples() == []
    assert not env.fake_log.exists()


def test_estimate_without_context_is_a_usage_error(env):
    make_session(env, ["claude-sonnet-5"])
    res = env.run("--estimate", "--session", SID)
    assert res.returncode == 2
    assert "usage: ccsb" in res.stderr
    assert res.stdout == ""
