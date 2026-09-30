"""Scanner state (incremental transcript reading, dedupe, pruning) and stored samples."""
import json
import os
import time

from conftest import USAGE_TEXT, assistant_line, write_jsonl

SID = "bbbb2222-0000-4000-8000-000000000002"


def transcript(mod):
    return os.path.join(mod.PROJECTS, "-tmp-demo-project", SID + ".jsonl")


def append(path, record, newline=True):
    with open(path, "a") as f:
        f.write(json.dumps(record, separators=(",", ":")) + ("\n" if newline else ""))


def read_attempt(mod):
    with open(os.path.join(mod.DATA_DIR, "last_attempt.json")) as f:
        return json.load(f)


def sonnet_input(st):
    return st["cum"]["claude-sonnet-5"]["std"][0]


def test_incremental_read_continues_from_offset(mod):
    now = time.time()
    path = transcript(mod)
    write_jsonl(path, [assistant_line(SID, now - 100, "msg_1", "claude-sonnet-5", inp=1000)])
    st = mod.update_state(now)
    assert sonnet_input(st) == 1000
    # Forget msg_1: a full re-read would count it again, an incremental read does not.
    state_path = os.path.join(mod.DATA_DIR, "state.json")
    with open(state_path) as f:
        saved = json.load(f)
    del saved["seen"]["msg_1"]
    with open(state_path, "w") as f:
        json.dump(saved, f)
    append(path, assistant_line(SID, now - 50, "msg_2", "claude-sonnet-5", inp=300))
    st = mod.update_state(now)
    assert sonnet_input(st) == 1300
    assert st["files"][path]["off"] == os.path.getsize(path)


def test_unfinished_last_line_waits(mod):
    now = time.time()
    path = transcript(mod)
    write_jsonl(path, [assistant_line(SID, now - 100, "msg_1", "claude-sonnet-5", inp=1000)])
    append(path, assistant_line(SID, now - 90, "msg_2", "claude-sonnet-5", inp=50), newline=False)
    assert sonnet_input(mod.update_state(now)) == 1000
    with open(path, "a") as f:
        f.write("\n")
    assert sonnet_input(mod.update_state(now)) == 1050


def test_duplicate_message_ids_count_once(mod):
    now = time.time()
    path = transcript(mod)
    line = assistant_line(SID, now - 100, "msg_1", "claude-sonnet-5", inp=1000)
    streamed = assistant_line(SID, now - 99, "msg_1", "claude-sonnet-5", inp=1000, out=500)
    write_jsonl(path, [line, line, streamed])
    # A copy in a second transcript (for example a resumed session).
    write_jsonl(os.path.join(mod.PROJECTS, "-tmp-demo-project", "copy.jsonl"), [line])
    st = mod.update_state(now)
    assert st["cum"]["claude-sonnet-5"]["std"][0] == 1000
    assert st["cum"]["claude-sonnet-5"]["std"][4] == 500


def test_old_files_are_not_read(mod):
    now = time.time()
    path = transcript(mod)
    write_jsonl(path, [assistant_line(SID, now - 10 * 86400, "msg_old", "claude-sonnet-5")])
    os.utime(path, (now - 10 * 86400, now - 10 * 86400))
    st = mod.update_state(now)
    assert st["cum"] == {}
    assert path in st["files"]


def test_old_seen_entries_are_pruned(mod):
    now = time.time()
    state = {"v": mod.STATE_VERSION, "state_id": "state-a", "created": now - 20 * 86400,
             "files": {}, "cum": {},
             "seen": {"msg_old": [int(now - 9 * 86400), "claude-sonnet-5", "std", 1, 0, 0, 0, 0, 0],
                      "msg_new": [int(now - 1 * 86400), "claude-sonnet-5", "std", 1, 0, 0, 0, 0, 0]}}
    with open(os.path.join(mod.DATA_DIR, "state.json"), "w") as f:
        json.dump(state, f)
    st = mod.update_state(now)
    assert set(st["seen"]) == {"msg_new"}
    assert st["state_id"] == "state-a"


def test_record_sample_stores_counts_and_prunes_old_samples(mod):
    now = time.time()
    write_jsonl(transcript(mod), [assistant_line(SID, now - 100, "msg_1", "claude-sonnet-5", inp=700)])
    old = {"v": 2, "t": now - 15 * 86400, "state_id": "x", "cum": {}, "bars": {}}
    recent = {"v": 2, "t": now - 1 * 86400, "state_id": "x", "cum": {}, "bars": {}}
    legacy = {"v": 1, "t": now - 3600}
    with open(os.path.join(mod.DATA_DIR, "samples.jsonl"), "w") as f:
        for s in (old, recent, legacy):
            f.write(json.dumps(s) + "\n")
    mod.record_sample_text(USAGE_TEXT)
    samples = mod.load_samples()
    assert len(samples) == 2
    assert samples[0]["t"] == recent["t"]
    new = samples[-1]
    assert new["cum"]["claude-sonnet-5"]["std"][0] == 700
    assert set(new["bars"]) == {"5h", "week_all", "week_fable"}
    attempt = read_attempt(mod)
    assert attempt["ok"] is True


def test_unparsable_usage_records_failure_and_no_sample(mod):
    mod.record_sample_text("Something went wrong")
    assert mod.load_samples() == []
    attempt = read_attempt(mod)
    assert attempt["ok"] is False
