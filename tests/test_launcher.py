"""The `ccsb` launcher, called directly and through a symlink."""
import os
import subprocess

from conftest import LAUNCHER


def run_help(path, env):
    return subprocess.run([path, "--help"], env=env.env(), capture_output=True, text=True, timeout=30)


def test_help(env):
    res = run_help(LAUNCHER, env)
    assert res.returncode == 0, res.stderr
    assert res.stdout.startswith("usage: ccsb")


def test_help_through_symlinks(env, tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    direct = bindir / "ccsb"
    os.symlink(LAUNCHER, str(direct))
    # A relative link to the absolute link: the launcher resolves the whole chain.
    chained = tmp_path / "ccsb-chained"
    os.symlink(os.path.join("bin", "ccsb"), str(chained))
    for path in (direct, chained):
        res = run_help(str(path), env)
        assert res.returncode == 0, res.stderr
        assert res.stdout.startswith("usage: ccsb")
