"""The sampling switch (config.json), `--sampling on|off|status`, `--delete-samples`, and the
report and estimate while sampling is off. All data is made up."""
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

import pytest

from conftest import (SCRIPT, assistant_line, calibrated_samples, make_exec, run_sampler,
                      user_line, wait_for)

SID = "ffff6666-0000-4000-8000-000000000006"
OFF_FOOTER_EN = "Sampling is off, so quota % is hidden. Turn it on with /ccsb:sampling on."
OFF_FOOTER_ZH = "采样已关闭，不显示额度 %。用 /ccsb:sampling on 打开。"


def set_sampling(env, state):
    res = env.run("--sampling", state)
    assert res.returncode == 0, res.stderr
    assert res.stdout == f"Sampling: {state}\n"


def make_session(env, text="Please tidy the build script", model="claude-sonnet-5"):
    now = time.time()
    env.session(SID, [
        user_line(SID, now - 120, text),
        assistant_line(SID, now - 110, "msg_s1", model, inp=5000, out=1000, tools=["Bash"]),
        assistant_line(SID, now - 100, "msg_s2", model, inp=3000, out=200),
    ])
    return now


def report(env, *extra):
    res = env.run("--session", SID, *extra)
    assert res.returncode == 0, res.stderr
    return res.stdout


# ---------------------------------------------------------------------------------------
# The switch

def config_path(mod):
    return os.path.join(mod.DATA_DIR, "config.json")


@pytest.mark.parametrize("text, on", [
    (None, True), ("{}", True), ('{"other": 1}', True), ('{"sampling": true}', True),
    ("not json", True), ('["sampling"]', True), ('{"sampling": false}', False)])
def test_switch_is_on_unless_config_says_false(mod, text, on):
    if text is not None:
        with open(config_path(mod), "w") as f:
            f.write(text)
    assert mod.sampling_enabled() is on


def test_set_sampling_keeps_other_keys(mod):
    with open(config_path(mod), "w") as f:
        f.write('{"other": 1}')
    mod.set_sampling(False)
    with open(config_path(mod)) as f:
        text = f.read()
    assert json.loads(text) == {"other": 1, "sampling": False}
    assert re.search(r'"sampling"\s*:\s*false', text)  # the form the Stop hook matches
    assert not mod.sampling_enabled()
    mod.set_sampling(True)
    with open(config_path(mod)) as f:
        assert json.load(f) == {"other": 1, "sampling": True}
    assert sorted(os.listdir(mod.DATA_DIR)) == [".lock", "config.json"]  # no temp file left


def test_cli_turns_sampling_off_and_on(env):
    set_sampling(env, "off")
    assert json.loads((env.data / "config.json").read_text()) == {"sampling": False}
    set_sampling(env, "on")
    assert json.loads((env.data / "config.json").read_text()) == {"sampling": True}


# ---------------------------------------------------------------------------------------
# No sample while off

def fake_python(env, tmp_path):
    """Make the hook's python3 a stub that only leaves a marker file."""
    marker = tmp_path / "python-started"
    make_exec(env.pybin / "python3", f"#!/bin/sh\n: > '{marker}'\n")
    return marker


@pytest.mark.parametrize("text", ['{"sampling":false}', '{\n  "other": 1,\n  "sampling" :\tfalse\n}'])
def test_hook_starts_no_python_when_off(env, tmp_path, text):
    marker = fake_python(env, tmp_path)
    env.data.mkdir()
    (env.data / "config.json").write_text(text)
    res = run_sampler(env)
    assert res.returncode == 0, res.stderr
    assert not wait_for(marker, 1.5)


def test_hook_starts_python_when_on(env, tmp_path):
    marker = fake_python(env, tmp_path)
    env.data.mkdir()
    (env.data / "config.json").write_text('{"sampling": true}')
    res = run_sampler(env)
    assert res.returncode == 0, res.stderr
    assert wait_for(marker, 30)


def test_hook_reads_the_switch_the_cli_writes(env, tmp_path):
    set_sampling(env, "off")
    marker = fake_python(env, tmp_path)
    assert run_sampler(env).returncode == 0
    assert not wait_for(marker, 1.5)


def test_hook_reads_the_default_data_dir(env, tmp_path):
    # An empty CCSB_DATA_DIR means the default folder, as on the Python side.
    marker = fake_python(env, tmp_path)
    default = env.home / ".claude" / "cc-session-breakdown"
    default.mkdir(parents=True)
    (default / "config.json").write_text('{"sampling":false}')
    assert run_sampler(env, CCSB_DATA_DIR="").returncode == 0
    assert not wait_for(marker, 1.5)


def test_sample_if_due_does_nothing_when_off_even_with_force(env):
    set_sampling(env, "off")
    for args in (["--sample-if-due"], ["--sample-if-due", "--force"]):
        res = env.run(*args)
        assert res.returncode == 0, res.stderr
    assert env.samples() == []
    assert not env.fake_log.exists()
    assert sorted(p.name for p in env.data.iterdir()) == [".lock", "config.json"]


def test_trigger_sample_starts_nothing_when_off(mod, monkeypatch):
    started = []
    monkeypatch.setattr(mod.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    mod.set_sampling(False)
    mod.trigger_sample()
    assert started == []
    mod.set_sampling(True)
    mod.trigger_sample()
    assert len(started) == 1 and started[0][-2:] == ["--sample-if-due", "--force"]


def test_current_session_report_takes_no_sample_when_off(env):
    make_session(env)
    set_sampling(env, "off")
    res = env.run(CLAUDE_CODE_SESSION_ID=SID)
    assert res.returncode == 0, res.stderr
    assert res.stdout.startswith("REPORT")
    assert not wait_for(env.fake_log, 1.5)


# ---------------------------------------------------------------------------------------
# Report while off

@pytest.mark.parametrize("calibrated", [True, False])
def test_report_off_en_shows_no_quota(env, calibrated):
    now = make_session(env)
    if calibrated:
        env.write_samples(calibrated_samples(now))
        (env.data / "last_attempt.json").write_text(json.dumps({"t": now, "ok": False}))
    set_sampling(env, "off")
    out = report(env)
    lines = out.splitlines()
    assert lines[:2] == ["REPORT", "lang: en"]
    for gone in ("5h", "Week·", "not calibrated", "Quota read failed", "Calibrated from"):
        assert gone not in out
    assert lines[2:5] == ["**📊 Session breakdown (estimate)**", "", "- API cost: $0.03"]
    assert "**Sonnet** (Share 100.00%)" in lines
    assert "| Work type | Tokens | Share | Main |" in lines
    assert "|---|--:|--:|:-:|" in lines
    assert "| Run command | 10K | 71.43% | ✓ |" in lines
    assert ("Share = this row's weekly quota ÷ this session's weekly quota (weighted by model "
            "price, not token count). API cost = this session's tokens at API list prices.") in lines
    assert OFF_FOOTER_EN in lines


def test_report_off_zh_shows_no_quota(env):
    now = make_session(env, text="请帮我整理一下构建脚本")
    env.write_samples(calibrated_samples(now))
    set_sampling(env, "off")
    out = report(env)
    lines = out.splitlines()
    assert lines[1] == "lang: zh"
    for gone in ("5h", "周·", "未校准", "额度读取失败", "系数来自"):
        assert gone not in out
    assert "**Sonnet**（占比 100.00%）" in lines
    assert "| 工作类型 | token | 占比 | 主session |" in lines
    assert "| 跑命令 | 10K | 71.43% | ✓ |" in lines
    assert OFF_FOOTER_ZH in lines


def test_turning_sampling_on_again_restores_quota(env):
    now = make_session(env)
    env.write_samples(calibrated_samples(now))
    set_sampling(env, "off")
    assert "Week·All" not in report(env)
    set_sampling(env, "on")
    lines = report(env).splitlines()
    assert "- Week·All: ≈ 14.00%" in lines
    assert "| Run command | 10K | 10.00% | 71.43% | ✓ |" in lines
    assert OFF_FOOTER_EN not in lines


def test_json_has_sampling_flag(env):
    make_session(env)
    assert json.loads(report(env, "--json"))["sampling"] is True
    set_sampling(env, "off")
    assert json.loads(report(env, "--json"))["sampling"] is False


def full_analysis():
    """A made-up analysis with every block: two model groups, one task, one cold start,
    and screenshots above the 10% line."""
    return {
        "total": 10_000.0, "cost_usd": 0.02,
        "fam_units": {"Fable": 4000.0, "Sonnet": 6000.0},
        "fam_models": {"Fable": {"claude-fable-5"}, "Sonnet": {"claude-sonnet-5"}},
        "rows": {("Fable", "think", True): (4000.0, 800.0), ("Sonnet", "web", False): (6000.0, 6000.0)},
        "tasks": {"agent-1": {"units": 6000.0, "fams": {"Sonnet": 6000.0}, "agents": ["agent-1"]}},
        "agents": {"agent-1": {"meta": {"description": "Compare two hosting plans"}, "streams": []}},
        "cold": {"events": 1, "fam": {"Fable": 1000.0}},
        "shots": {("computer_use", "Fable"): {"units": 2000.0, "n": 4}},
        "context": {"used": 50_000, "window": 1_000_000, "model": "claude-fable-5"},
        "calls": [{"model": "claude-fable-5"}, {"model": "claude-sonnet-5"}],
        "bad": 0,
    }


COEFS = {"5h": 1000.0, "week_all": 1000.0, "week_fable": 500.0, "fable_bar": True,
         "samples": 2, "quota_failed": False, "sources": {}}


def test_render_on_has_week_all_in_every_table(mod):
    lines, ctx = mod.render(full_analysis(), "en", COEFS, True)
    for expected in ("- 5h: ≈ 10.00%", "- Week·All: ≈ 10.00%", "- Week·Fable: ≈ 8.00%",
                     "**Fable** (Week·All 4.00%, Week·Fable 8.00%, Share 40.00%)",
                     "| Work type | Tokens | Week·All | Share | Main |",
                     "| Think/reply | 800 | 4.00% | 40.00% | ✓ |",
                     "| # | Model | Week·All | Share | Task |",
                     "| 1 | Sonnet | 6.00% | 60.00% | {{TASK_1}} |",
                     "| Model | Week·All | Share |", "| Fable | 1.00% | 10.00% |",
                     "| Source | Model | Images | Week·All | Share |",
                     "| computer-use | Fable | 4 | 2.00% | 20.00% |"):
        assert expected in lines
    assert ctx[1].startswith("TASK_1 | Compare two hosting plans")


def test_render_off_drops_week_all_from_every_table(mod):
    lines, ctx = mod.render(full_analysis(), "en", COEFS, False)
    text = "\n".join(lines)
    assert "Week·" not in text and "5h" not in text
    for expected in ("**Fable** (Share 40.00%)",
                     "| Work type | Tokens | Share | Main |", "|---|--:|--:|:-:|",
                     "| Think/reply | 800 | 40.00% | ✓ |",
                     "| # | Model | Share | Task |", "|--:|---|--:|---|",
                     "| 1 | Sonnet | 60.00% | {{TASK_1}} |",
                     "**Cold-start waste** (back after >1h idle, 1 time)",
                     "| Model | Share |", "|---|--:|", "| Fable | 10.00% |",
                     "**⚠️ High screenshot cost** (4 images, share 20.00%)",
                     "| Source | Model | Images | Share |", "|---|---|--:|--:|",
                     "| computer-use | Fable | 4 | 20.00% |",
                     OFF_FOOTER_EN):
        assert expected in lines
    assert ctx[1].startswith("TASK_1 | Compare two hosting plans")


def test_render_off_zh_drops_week_all_from_every_table(mod):
    lines, _ctx = mod.render(full_analysis(), "zh", COEFS, False)
    text = "\n".join(lines)
    assert "周·" not in text and "5h" not in text
    for expected in ("**Fable**（占比 40.00%）", "| 工作类型 | token | 占比 | 主session |",
                     "| # | 模型 | 占比 | 任务 |", "| 模型 | 占比 |", "| 来源 | 模型 | 张数 | 占比 |",
                     OFF_FOOTER_ZH):
        assert expected in lines


# ---------------------------------------------------------------------------------------
# Estimate while off

def make_estimate_session(env, model, text="Write the changelog entry"):
    now = time.time()
    env.session(SID, [user_line(SID, now - 300, text),
                      assistant_line(SID, now - 200, "msg_e0", model, inp=10, read=50_000,
                                     write=2000, out=800)])
    return now


def estimate(env):
    res = env.run("--estimate", "--context", "200K", "--turns", "10", "--session", SID)
    assert res.returncode == 0, res.stderr
    return res.stdout.splitlines()


def test_estimate_off_gives_units_and_usd(env):
    # Amounts as in test_estimate.py: 265,000 units at 200K and 85,000 after /compact.
    now = make_estimate_session(env, "claude-sonnet-5")
    env.write_samples(calibrated_samples(now))
    set_sampling(env, "off")
    assert estimate(env) == [
        "Next 10 turns (about 10 calls, 200K context, Sonnet 5): ≈ 265K weighted units, $0.53; "
        "after /compact to about 20K: ≈ 85K weighted units, $0.17 (sampling off)"]


def test_estimate_off_leaves_out_calibrated_week_fable(env):
    now = make_estimate_session(env, "claude-fable-5")
    env.write_samples(calibrated_samples(now, fable=True))
    set_sampling(env, "off")
    assert estimate(env) == [
        "Next 10 turns (about 10 calls, 200K context, Fable 5): ≈ 1.3M weighted units, $2.65; "
        "after /compact to about 20K: ≈ 425K weighted units, $0.85 (sampling off)"]


def test_estimate_off_zh(env):
    make_estimate_session(env, "claude-sonnet-5", text="写一下更新日志")
    set_sampling(env, "off")
    [line] = estimate(env)
    assert line.endswith("（采样已关闭）")
    assert "周·" not in line and "未校准" not in line


# ---------------------------------------------------------------------------------------
# --delete-samples

DELETED = ["samples.jsonl", "coefficients.json", "last_attempt.json", "last-sample.stamp"]
KEPT = ["state.json", "config.json", "pricing.override.json", "price-check.json", "error.log",
        ".lock", "sampler.lock"]


def fill_data(env):
    """Every data file, with sampling off. samples.jsonl is 2048 bytes."""
    env.data.mkdir(parents=True, exist_ok=True)
    for name in KEPT + DELETED:
        (env.data / name).write_text("")
    (env.data / "samples.jsonl").write_text("x" * 2048)
    (env.data / "coefficients.json").write_text("{}")
    (env.data / "last_attempt.json").write_text('{"ok":true}')
    (env.data / "state.json").write_text('{"v":3}')
    (env.data / "config.json").write_text('{"sampling":false}')


def files(env):
    return {p.name: p.read_bytes() for p in env.data.iterdir()}


LISTING = ["- samples.jsonl (2.0 KB)", "- coefficients.json (2 B)",
           "- last_attempt.json (11 B)", "- last-sample.stamp (0 B)"]


def test_delete_dry_run_deletes_nothing(env):
    fill_data(env)
    before = files(env)
    res = env.run("--delete-samples")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == ([f"Would delete in {env.data}:"] + LISTING
                                       + ["Dry run, nothing deleted. Add --yes to delete these files."])
    assert files(env) == before


def test_delete_yes_removes_samples_and_calibration_only(env):
    fill_data(env)
    before = files(env)
    res = env.run("--delete-samples", "--yes")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == [f"Deleted in {env.data}:"] + LISTING + ["Sampling: off"]
    assert files(env) == {name: before[name] for name in KEPT}


def test_delete_with_nothing_to_delete(env):
    res = env.run("--delete-samples", "--yes")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == [f"Nothing to delete in {env.data}", "Sampling: on"]
    assert not env.data.exists()


def test_delete_waits_for_a_running_sample(env):
    fill_data(env)
    fd = os.open(str(env.data / "sampler.lock"), os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)  # what a running sample_if_due holds
    try:
        proc = subprocess.Popen([sys.executable, SCRIPT, "--delete-samples", "--yes"], env=env.env(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(1.0)
        assert proc.poll() is None
        assert (env.data / "samples.jsonl").exists()
    finally:
        os.close(fd)
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, err
    assert out.startswith("Deleted in")
    assert not (env.data / "samples.jsonl").exists()


def coef_path(mod):
    return os.path.join(mod.DATA_DIR, "coefficients.json")


@pytest.mark.parametrize("change", ["new sample", "samples file gone"])
def test_delete_between_compute_and_cache_write_is_not_undone(mod, monkeypatch, change):
    samples = calibrated_samples(time.time())
    mod.save_samples(samples)
    assert mod.calibrated_coefficients()["week_all"]["value"] is not None  # cache written
    samples_path = os.path.join(mod.DATA_DIR, "samples.jsonl")
    if change == "new sample":
        mod.save_samples(samples + [dict(samples[-1], t=samples[-1]["t"] + 60)])
        os.utime(samples_path, (time.time() + 5, time.time() + 5))
    else:
        os.remove(samples_path)  # the cache key misses; the saved values carry over
    compute = mod.compute_coefficients

    def compute_then_delete(*args):
        coefs = compute(*args)
        mod.delete_samples(True)  # the delete lands between the compute and the write
        return coefs
    monkeypatch.setattr(mod, "compute_coefficients", compute_then_delete)
    assert mod.calibrated_coefficients()["week_all"]["value"] is not None
    assert not os.path.exists(coef_path(mod))
    monkeypatch.setattr(mod, "compute_coefficients", compute)
    assert mod.calibrated_coefficients()["week_all"] == {
        "value": None, "source": "uncalibrated", "intervals": 0}


def test_busy_data_lock_skips_the_cache_write(mod, monkeypatch):
    mod.save_samples(calibrated_samples(time.time()))
    monkeypatch.setattr(mod, "COEF_CACHE_LOCK_S", 0.3)
    fd = os.open(os.path.join(mod.DATA_DIR, ".lock"), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)  # another process holds the data lock
    try:
        assert mod.calibrated_coefficients()["week_all"]["value"] is not None
        assert not os.path.exists(coef_path(mod))
    finally:
        os.close(fd)


def test_data_lock_is_reentrant(mod):
    mod.save_samples(calibrated_samples(time.time()))
    with mod.data_lock(timeout=0.5):
        with mod.data_lock(timeout=0.5):
            pass
        mod.calibrated_coefficients()
    assert os.path.exists(coef_path(mod))


# ---------------------------------------------------------------------------------------
# --sampling status

def test_status_lists_switch_samples_and_calibrated_bars(env):
    samples = calibrated_samples(time.time())
    env.write_samples(samples)
    first, last = (datetime.fromtimestamp(s["t"]).strftime("%Y-%m-%d %H:%M") for s in samples)
    res = env.run("--sampling", "status")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == [
        "Sampling: on",
        f"Samples: 2, from {first} to {last} (local time)",
        "Calibrated: 5h yes, Week·All yes, Week·Fable no"]


def test_status_when_off_without_samples(env):
    set_sampling(env, "off")
    res = env.run("--sampling", "status")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == [
        "Sampling: off", "Samples: 0", "Calibrated: 5h no, Week·All no, Week·Fable no"]


# ---------------------------------------------------------------------------------------
# Standalone actions

@pytest.mark.parametrize("args", [
    ["--sampling", "on", "--title", "x"],
    ["--sampling", "on", "--session", SID],
    ["--sampling", "status", "--record-sample", "usage.txt"],
    ["--sampling", "on", "--sample-if-due"],
    ["--sampling", "status", "--estimate", "--context", "200K"],
    ["--sampling", "on", "--mark-price-checked"],
    ["--sampling", "on", "--delete-samples"],
    ["--delete-samples", "--title", "x"],
    ["--delete-samples", "--session", SID],
    ["--delete-samples", "--record-sample", "usage.txt"],
    ["--delete-samples", "--sample-if-due"],
    ["--delete-samples", "--yes", "--estimate", "--context", "200K"],
    ["--delete-samples", "--yes", "--mark-price-checked"],
    ["--yes"],
    ["--yes", "--session", SID],
    ["--sampling", "maybe"],
])
def test_standalone_actions_reject_other_actions(env, args):
    make_session(env)
    fill_data(env)
    before = files(env)
    res = env.run(*args)
    assert res.returncode == 2
    assert "usage: ccsb" in res.stderr
    assert res.stdout == ""
    assert files(env) == before
