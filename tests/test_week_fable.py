"""The Week·Fable rule: shown only for a session with Fable calls while the samples hold a
Week·Fable bar (or no sample exists yet)."""
import time

from conftest import assistant_line, calibrated_samples, sample, user_line

SID = "dddd4444-0000-4000-8000-000000000004"


def make_session(env, models):
    now = time.time()
    records = [user_line(SID, now - 300, "Summarize the meeting notes")]
    for i, m in enumerate(models):
        records.append(assistant_line(SID, now - 200 + i, f"msg_f{i}", m, inp=2000, out=100))
    env.session(SID, records)
    return now


def fable_lines(env):
    res = env.run("--session", SID)
    assert res.returncode == 0, res.stderr
    return [l for l in res.stdout.splitlines() if "Week·Fable" in l]


def test_fable_session_without_samples_is_not_calibrated(env):
    make_session(env, ["claude-fable-5", "claude-sonnet-5"])
    lines = fable_lines(env)
    assert "- Week·Fable: not calibrated" in lines
    assert any(l.startswith("**Fable**") and "Week·Fable not calibrated" in l for l in lines)


def test_fable_bar_with_too_few_intervals_is_not_calibrated(env):
    now = make_session(env, ["claude-fable-5"])
    env.write_samples([sample(now - 60, 0, {"5h": (1.0, now + 3600), "week_all": (1.0, now + 86400),
                                            "week_fable": (1.0, now + 86400)})])
    assert "- Week·Fable: not calibrated" in fable_lines(env)


def test_calibrated_fable_bar(env):
    now = make_session(env, ["claude-fable-5"])
    env.write_samples(calibrated_samples(now, fable=True))
    # 2000 input + 100 output x 5 = 2500 tokens x 5 (Fable 5 ratio) = 12500 units / 5000.
    assert "- Week·Fable: ≈ 2.50%" in fable_lines(env)


def test_samples_without_fable_bar_hide_it(env):
    now = make_session(env, ["claude-fable-5"])
    env.write_samples(calibrated_samples(now, fable=False))
    assert fable_lines(env) == []


def test_session_without_fable_calls_hides_it(env):
    now = make_session(env, ["claude-opus-5-5"])
    env.write_samples(calibrated_samples(now, fable=True))
    assert fable_lines(env) == []
