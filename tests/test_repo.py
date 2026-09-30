"""Repo checks: manifests, hook wiring, file modes, and no personal paths."""
import json
import os
import re

import pytest

from conftest import LAUNCHER, ROOT, SAMPLER

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv"}


def repo_files():
    for dirpath, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            yield os.path.join(dirpath, n)


def load(rel):
    with open(os.path.join(ROOT, rel)) as f:
        return json.load(f)


@pytest.mark.parametrize("path", [p for p in repo_files() if p.endswith(".json")],
                         ids=lambda p: os.path.relpath(p, ROOT))
def test_json_parses(path):
    with open(path) as f:
        json.load(f)


def test_plugin_manifest():
    plugin = load(".claude-plugin/plugin.json")
    assert plugin["name"] == "ccsb"
    assert isinstance(plugin.get("version"), str) and plugin["version"]
    market = load(".claude-plugin/marketplace.json")
    assert [p["name"] for p in market["plugins"]] == ["ccsb"]


def test_stop_hook_runs_sampler_from_plugin_root():
    hooks = load("hooks/hooks.json")["hooks"]["Stop"]
    commands = [h["command"] for group in hooks for h in group["hooks"]]
    assert commands == ['bash "${CLAUDE_PLUGIN_ROOT}/scripts/plan_quota_sampler.sh"']


@pytest.mark.parametrize("path", [LAUNCHER, SAMPLER])
def test_scripts_are_executable(path):
    assert os.access(path, os.X_OK)


# An absolute home path such as /Users/<name>/ or /home/<name>/. The example path
# /path/to/ is allowed.
HOME_PATH = re.compile(rb"/(?:Users|home)/(?!path/to/)[A-Za-z0-9._-]+/")


def test_no_personal_paths():
    hits = []
    for p in repo_files():
        with open(p, "rb") as f:
            m = HOME_PATH.search(f.read())
        if m:
            hits.append(os.path.relpath(p, ROOT))
    assert hits == []
