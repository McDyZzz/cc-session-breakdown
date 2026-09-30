"""Report rendering through the command line, in the uncalibrated, calibrated, and
quota-read-failed states."""
import json
import time

from conftest import assistant_line, calibrated_samples, sample, user_line

SID = "cccc3333-0000-4000-8000-000000000003"


def make_session(env, text="Please tidy the build script", model="claude-sonnet-5"):
    now = time.time()
    env.session(SID, [
        user_line(SID, now - 120, text),
        assistant_line(SID, now - 110, "msg_r1", model, inp=5000, out=1000, tools=["Bash"]),
        assistant_line(SID, now - 100, "msg_r2", model, inp=3000, out=200),
    ])
    return now


def report(env):
    res = env.run("--session", SID)
    assert res.returncode == 0, res.stderr
    return res.stdout


def test_uncalibrated_en(env):
    make_session(env)
    out = report(env)
    lines = out.splitlines()
    assert lines[:2] == ["REPORT", "lang: en"]
    assert "- 5h: not calibrated" in lines
    assert "- Week·All: not calibrated" in lines
    assert "**Sonnet** (Week·All not calibrated, Share 100.00%)" in lines
    assert "| Run command | 10K | - | 71.43% | ✓ |" in lines
    assert "A not-calibrated bar needs more /usage samples." in out
    assert "Calibrated from 0 samples." in out
    # 5000 + 3000 input + 1200 output x 5 = 14000 units = $0.028.
    assert "- API cost: $0.03" in lines


def test_uncalibrated_zh(env):
    make_session(env, text="请帮我整理一下构建脚本")
    out = report(env)
    lines = out.splitlines()
    assert lines[1] == "lang: zh"
    assert "- 5h：未校准" in lines
    assert "- 周·全部：未校准" in lines
    assert "标为未校准的额度条需要更多 /usage 样本。" in out


def test_calibrated_prints_percentages(env):
    now = make_session(env)
    env.write_samples(calibrated_samples(now))
    out = report(env)
    lines = out.splitlines()
    # 14000 units at 1000 units per 1%.
    assert "- 5h: ≈ 14.00%" in lines
    assert "- Week·All: ≈ 14.00%" in lines
    assert "**Sonnet** (Week·All 14.00%, Share 100.00%)" in lines
    assert "| Run command | 10K | 10.00% | 71.43% | ✓ |" in lines
    assert "not calibrated" not in out
    assert "Calibrated from 2 samples." in out


def test_footer_says_one_sample(env):
    now = make_session(env)
    env.write_samples([sample(now - 60, 0, {"5h": (1.0, now + 3600), "week_all": (1.0, now + 86400)})])
    out = report(env)
    assert "Calibrated from 1 sample." in out
    assert "1 samples" not in out


def test_failed_sample_prints_quota_read_failed(env):
    now = make_session(env)
    env.write_samples(calibrated_samples(now))
    (env.data / "last_attempt.json").write_text(json.dumps({"t": now, "ok": False, "error": "x"}))
    lines = report(env).splitlines()
    assert "Quota read failed" in lines
    assert not any(l.startswith("- 5h") or l.startswith("- Week·All") for l in lines)
    assert "- API cost: $0.03" in lines
    # Tables still use the saved coefficients.
    assert "| Run command | 10K | 10.00% | 71.43% | ✓ |" in lines


def test_failed_sample_without_coefficients_does_not_crash(env):
    now = make_session(env)
    env.data.mkdir(parents=True)
    (env.data / "last_attempt.json").write_text(json.dumps({"t": now, "ok": False}))
    lines = report(env).splitlines()
    assert "Quota read failed" in lines
    assert "| Run command | 10K | - | 71.43% | ✓ |" in lines


def test_unknown_model_warning_and_price_check(env):
    make_session(env, model="claude-opus-9-9")
    out = report(env)
    lines = out.splitlines()
    assert lines[2].startswith("PRICE_CHECK: due (unknown model claude-opus-9-9")
    assert ("⚠️ Unknown model claude-opus-9-9: priced as claude-opus-5-5 for now. "
            "Please add it to pricing.override.json.") in lines
