"""Shared fixtures. All data is made up.

Tests never touch the real ~/.claude folder or the real `claude` program:
- In-process tests patch the module constants DATA_DIR and PROJECTS to temp folders.
- Subprocess tests set HOME to a temp folder, CCSB_DATA_DIR to a temp data folder, and
  CCSB_CLAUDE_BIN to a fake `claude` that prints canned /usage text.
"""
import importlib.util
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "session_breakdown_report.py")
SAMPLER = os.path.join(ROOT, "scripts", "plan_quota_sampler.sh")
LAUNCHER = os.path.join(ROOT, "bin", "ccsb")

USAGE_TEXT = (
    "Current session: 12% used · resets 3pm (UTC)\n"
    "Current week (all models): 40% used · resets Oct 2 at 9am (UTC)\n"
    "Current week (Fable): 7% used · resets Oct 2 at 9am (UTC)\n"
)

_spec = importlib.util.spec_from_file_location("ccsb_report", SCRIPT)
report_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report_module)


@pytest.fixture
def mod(tmp_path, monkeypatch):
    """The report module with its data folder and transcript folder in tmp_path, and a
    fresh price cache."""
    data = tmp_path / "data"
    projects = tmp_path / "home" / ".claude" / "projects"
    projects.mkdir(parents=True)
    data.mkdir()
    monkeypatch.setattr(report_module, "DATA_DIR", str(data))
    monkeypatch.setattr(report_module, "PROJECTS", str(projects))
    monkeypatch.setattr(report_module, "_PRICE_TABLE", None)
    monkeypatch.setattr(report_module, "_RESOLVED", {})
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    return report_module


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def user_line(sid, ts, text):
    return {"type": "user", "sessionId": sid, "timestamp": iso(ts), "cwd": "/tmp/demo-project",
            "message": {"role": "user", "content": text}}


def assistant_line(sid, ts, msg_id, model, inp=1000, out=100, read=0, write=0, speed=None,
                   searches=0, tools=()):
    usage = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": read,
             "cache_creation_input_tokens": write}
    if speed:
        usage["speed"] = speed
    if searches:
        usage["server_tool_use"] = {"web_search_requests": searches}
    content = [{"type": "text", "text": "Done."}]
    content += [{"type": "tool_use", "id": f"tu_{msg_id}_{i}", "name": n, "input": {}}
                for i, n in enumerate(tools)]
    return {"type": "assistant", "sessionId": sid, "timestamp": iso(ts),
            "message": {"id": msg_id, "model": model, "role": "assistant",
                        "content": content, "usage": usage}}


def title_line(sid, title):
    return {"type": "custom-title", "sessionId": sid, "customTitle": title}


def write_jsonl(path, records):
    """Compact JSON lines, like real transcripts (the script matches '"type":"user"' as bytes)."""
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")


def make_exec(path, text):
    """Write text to path and make the file executable."""
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class Env:
    """A temp HOME, data folder, and fake `claude` for subprocess runs."""

    def __init__(self, tmp_path):
        self.home = tmp_path / "home"
        self.data = tmp_path / "data"
        self.projects = self.home / ".claude" / "projects"
        self.projects.mkdir(parents=True)
        self.fake_log = tmp_path / "fake-claude.log"
        self.fake_claude = tmp_path / "fake-claude"
        self.set_fake(USAGE_TEXT)
        # A `python3` that runs the interpreter running pytest. env() puts this folder
        # first on the child PATH, so bin/ccsb and the hook use that interpreter too.
        self.pybin = tmp_path / "pybin"
        self.pybin.mkdir()
        make_exec(self.pybin / "python3", f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n")

    def set_fake(self, text, code=0):
        """The fake `claude` logs its arguments and CCSB_SAMPLING, prints text, exits code."""
        body = (
            "#!/bin/sh\n"
            f"echo \"$* CCSB_SAMPLING=$CCSB_SAMPLING\" >> '{self.fake_log}'\n"
            "cat <<'CCSB_EOF'\n"
            f"{text}"
            "CCSB_EOF\n"
            f"exit {code}\n"
        )
        make_exec(self.fake_claude, body)

    def env(self, **extra):
        e = {k: v for k, v in os.environ.items()
             if k not in ("CLAUDE_CODE_SESSION_ID", "CCSB_SAMPLING")}
        e.update(HOME=str(self.home), CCSB_DATA_DIR=str(self.data),
                 CCSB_CLAUDE_BIN=str(self.fake_claude))
        e.update(extra)
        e["PATH"] = os.pathsep.join([str(self.pybin), e.get("PATH", "")])
        return e

    def run(self, *args, **extra):
        return subprocess.run([sys.executable, SCRIPT] + list(args), env=self.env(**extra),
                              capture_output=True, text=True, timeout=60)

    def session(self, sid, records, title=None):
        recs = list(records) + ([title_line(sid, title)] if title else [])
        write_jsonl(self.projects / "-tmp-demo-project" / f"{sid}.jsonl", recs)

    def samples(self):
        path = self.data / "samples.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def write_samples(self, samples):
        self.data.mkdir(parents=True, exist_ok=True)
        with open(str(self.data / "samples.jsonl"), "w") as f:
            for s in samples:
                f.write(json.dumps(s) + "\n")


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def sample(t, sonnet_input, bars, fable_input=0, state_id="state-a"):
    """A stored /usage sample. Units: Sonnet 5 input tokens = weighted units (ratio 1);
    Fable 5 input tokens weigh 5 units each. bars maps bar -> (pct, reset_ts)."""
    cum = {"claude-sonnet-5": {"std": [sonnet_input, 0, 0, 0, 0], "fast": [0] * 5, "ws": 0}}
    if fable_input:
        cum["claude-fable-5"] = {"std": [fable_input, 0, 0, 0, 0], "fast": [0] * 5, "ws": 0}
    return {"v": 2, "t": t, "state_id": state_id, "cum": cum,
            "bars": {b: {"pct": p, "reset": "", "reset_ts": r} for b, (p, r) in bars.items()}}


def calibrated_samples(now, fable=False):
    """Two samples, one 10-point interval per bar. 5h and Week·All: 1000 units per 1%,
    or 6000 with fable=True. Week·Fable (fable=True only): 5000 units per 1%."""
    reset = now + 86400
    b0 = {"5h": (10.0, reset), "week_all": (10.0, reset)}
    b1 = {"5h": (20.0, reset), "week_all": (20.0, reset)}
    if fable:
        b0["week_fable"] = (1.0, reset)
        b1["week_fable"] = (11.0, reset)
    return [sample(now - 600, 0, b0), sample(now - 300, 10_000, b1,
                                               fable_input=10_000 if fable else 0)]
