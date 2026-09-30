"""/usage sampling with a fake `claude`: throttle, force, recursion guard, failures."""
import json
import os
import shutil
import signal
import sys
import time

import pytest

from conftest import USAGE_TEXT, make_exec, run_sampler, wait_for


def attempt(env):
    return json.loads((env.data / "last_attempt.json").read_text())


def test_sample_if_due_records_a_sample(env):
    res = env.run("--sample-if-due")
    assert res.returncode == 0, res.stderr
    samples = env.samples()
    assert len(samples) == 1
    assert samples[0]["bars"]["week_all"]["pct"] == 40.0
    assert attempt(env)["ok"] is True
    # The inner run gets the /usage prompt and the recursion guard.
    assert env.fake_log.read_text().strip() == "-p /usage --no-session-persistence CCSB_SAMPLING=1"


def test_throttle_skips_second_sample_and_force_overrides(env):
    env.run("--sample-if-due")
    env.run("--sample-if-due")
    assert len(env.samples()) == 1
    assert len(env.fake_log.read_text().splitlines()) == 1
    env.run("--sample-if-due", "--force")
    assert len(env.samples()) == 2


def test_failing_claude_records_failure_and_no_sample(env):
    env.set_fake("Error: not logged in\n", code=3)
    res = env.run("--sample-if-due")
    assert res.returncode == 0, res.stderr
    assert env.samples() == []
    result = attempt(env)
    assert result["ok"] is False
    assert "exited 3" in result["error"]


def test_missing_claude_records_failure(env):
    env.fake_claude.unlink()
    env.run("--sample-if-due")
    assert env.samples() == []
    assert attempt(env)["error"] == "claude CLI not found"


def test_record_sample_from_file(env, tmp_path):
    saved = tmp_path / "usage.txt"
    saved.write_text(USAGE_TEXT)
    res = env.run("--record-sample", str(saved))
    assert res.returncode == 0, res.stderr
    assert len(env.samples()) == 1


def test_sampler_hook_exits_at_once_inside_a_sample(env):
    res = run_sampler(env, CCSB_SAMPLING="1")
    assert res.returncode == 0
    assert not wait_for(env.data, 1.5)
    assert not env.fake_log.exists()


class Worker:
    """A fake `claude` for tests that start a detached sample job.

    The fake writes "<its pid> <its parent pid> <its session id>" to `info`, then prints
    the canned /usage text. With block=True it first waits until `release` exists. Its
    parent is the detached `--sample-if-due` process, so `info` also names the worker.
    """

    def __init__(self, env, tmp_path, block=False):
        self.info, self.release = tmp_path / "worker-info", tmp_path / "release"
        wait = (f"while not os.path.exists({str(self.release)!r}):\n    time.sleep(0.1)\n"
                if block else "")
        make_exec(env.fake_claude,
                  f"#!{sys.executable}\n"
                  "import os, sys, time\n"
                  f"with open({str(self.info) + '.tmp'!r}, 'w') as f:\n"
                  "    f.write(f'{os.getpid()} {os.getppid()} {os.getsid(0)}')\n"
                  f"os.rename({str(self.info) + '.tmp'!r}, {str(self.info)!r})\n"
                  + wait +
                  f"sys.stdout.write({USAGE_TEXT!r})\n")

    def ids(self):
        """(fake pid, worker pid, fake session id) once the fake has started."""
        return tuple(int(x) for x in self.info.read_text().split())

    def stop(self):
        """Release the fake, wait for the fake and the worker to end, and kill a survivor."""
        self.release.touch()
        if not wait_for(self.info, 5):
            return
        pids = self.ids()[:2]
        deadline = time.time() + 30
        while time.time() < deadline and any(alive(p) for p in pids):
            time.sleep(0.1)
        for p in pids:
            if alive(p):
                os.kill(p, signal.SIGKILL)


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:  # gone, or the pid now belongs to another user
        return False
    return True


def test_sampler_hook_starts_a_detached_sample(env, tmp_path):
    # The fake `claude` signals that it started, then blocks until the test releases it.
    worker = Worker(env, tmp_path, block=True)
    try:
        res = run_sampler(env)
        # The hook has returned while the sample job is still blocked inside `claude`.
        assert res.returncode == 0
        assert wait_for(worker.info, 30)
        assert not (env.data / "samples.jsonl").exists()
        assert not (env.data / "last_attempt.json").exists()
        worker.release.touch()
        assert wait_for(env.data / "last_attempt.json", 30)
        assert attempt(env)["ok"] is True
        assert len(env.samples()) == 1
    finally:
        worker.stop()


@pytest.mark.skipif(not (shutil.which("setsid") or shutil.which("perl")),
                    reason="neither setsid nor perl is on PATH, so the hook cannot detach")
def test_sampler_hook_job_leaves_the_hook_session(env, tmp_path):
    # The normal PATH: Linux takes the setsid(1) branch, macOS the perl branch.
    worker = Worker(env, tmp_path, block=True)
    try:
        res = run_sampler(env)
        assert res.returncode == 0, res.stderr
        assert wait_for(worker.info, 30)
        # run_sampler starts bash in this test's session.
        assert worker.ids()[2] != os.getsid(0)
    finally:
        worker.stop()


def restricted_path(tmp_path, tools):
    """A PATH folder that holds only links to the named tools from the real PATH."""
    bindir = tmp_path / "restricted-bin"
    bindir.mkdir()
    for tool in tools:
        os.symlink(shutil.which(tool), str(bindir / tool))
    return bindir


def test_sampler_hook_uses_setsid_program_when_present(env, tmp_path):
    bindir = restricted_path(tmp_path, ["dirname", "nohup", "cat"])
    used = tmp_path / "setsid-used"
    make_exec(bindir / "setsid", f"#!/bin/sh\n: > '{used}'\nexec \"$@\"\n")
    worker = Worker(env, tmp_path)
    try:
        res = run_sampler(env, PATH=str(bindir))
        assert res.returncode == 0, res.stderr
        assert wait_for(env.data / "last_attempt.json", 30)
        assert attempt(env)["ok"] is True
        assert len(env.samples()) == 1
        assert used.exists()
    finally:
        worker.stop()


def test_sampler_hook_works_without_setsid_and_perl(env, tmp_path):
    bindir = restricted_path(tmp_path, ["dirname", "nohup", "cat"])
    worker = Worker(env, tmp_path)
    try:
        res = run_sampler(env, PATH=str(bindir))
        assert res.returncode == 0, res.stderr
        assert wait_for(env.data / "last_attempt.json", 30)
        assert attempt(env)["ok"] is True
        assert len(env.samples()) == 1
    finally:
        worker.stop()
