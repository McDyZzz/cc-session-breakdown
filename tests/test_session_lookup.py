"""Session lookup by id and by title."""
import time

from conftest import assistant_line, user_line

SID_A = "1111aaaa-0000-4000-8000-00000000000a"
SID_B = "2222bbbb-0000-4000-8000-00000000000b"


def make_sessions(env):
    now = time.time()
    for sid, title in ((SID_A, "Release notes for the garden app"), (SID_B, "Fix the flaky login test")):
        env.session(sid, [user_line(sid, now - 60, "Start here"),
                          assistant_line(sid, now - 50, "msg_" + sid[:8], "claude-sonnet-5")], title=title)


def test_by_session_id(env):
    make_sessions(env)
    res = env.run("--session", SID_B)
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines()[0] == "REPORT"


def test_by_unique_id_prefix(env):
    make_sessions(env)
    res = env.run("--session", SID_A[:8])
    assert res.stdout.splitlines()[0] == "REPORT"


def test_by_title(env):
    make_sessions(env)
    res = env.run("--title", "flaky login")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines()[0] == "REPORT"


def test_title_matching_two_sessions_lists_them(env):
    make_sessions(env)
    res = env.run("--title", "the")
    lines = res.stdout.splitlines()
    assert lines[0] == "MULTIPLE"
    assert {l.split(" | ")[0] for l in lines[1:]} == {SID_A, SID_B}


def test_title_without_match_lists_candidates(env):
    # SKILL.md: no strong title match prints CANDIDATES (sessions sharing a word, then
    # the most recent ones) for AI to pick from; the report path exits 0.
    make_sessions(env)
    res = env.run("--title", "garden watering schedule")
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert lines[0] == "CANDIDATES"
    assert lines[1].startswith(SID_A + " | Release notes for the garden app | /tmp/demo-project | ")


def test_unknown_session_id_gives_error_line(env):
    make_sessions(env)
    res = env.run("--session", "99999999-no-such-session")
    assert res.stdout.splitlines() == ["ERROR: session 99999999-no-such-session not found"]
    # A report run exits 0 by design: AI reads the first line.
    assert res.returncode == 0


def test_no_current_session_gives_error_line(env):
    res = env.run()
    assert res.stdout.splitlines() == ["ERROR: current session id not found"]
