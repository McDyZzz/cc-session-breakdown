# AGENTS.md - rules for working on this repo

This file is for agents and people who change the code in this repo.
People who only use the plugin read `README.md`. Their agents read the `SKILL.md` files
under `skills/`.

## What this repo is

`cc-session-breakdown` is a Claude Code plugin named `ccsb`. It reports the token use of one
Claude Code session, split by model, by agent, and by kind of work. It also estimates the
cost of the next turns now and after `/compact`.

The repo is both the plugin and its marketplace.

| Path | Content |
| --- | --- |
| `.claude-plugin/` | `plugin.json` (name, version) and `marketplace.json` |
| `skills/report/SKILL.md` | How AI runs the report. Usage only |
| `skills/sampling/SKILL.md` | How AI turns sampling on or off, shows its status, and deletes samples. Usage only |
| `scripts/session_breakdown_report.py` | The whole program. Python standard library only |
| `scripts/plan_quota_sampler.sh` | Stop hook. Starts a quota sample in the background, unless sampling is off |
| `scripts/pricing.json` | Bundled API prices |
| `hooks/hooks.json` | Registers the Stop hook |
| `bin/ccsb` | Launcher. Claude Code puts `bin/` on the PATH of the Bash tool |
| `tests/` | pytest tests. All test data is made up |

## Actor words

- `SKILL.md`, this file, and code comments name the actors HUMAN and AI.
  HUMAN = the user. AI = the agent.
- `README.md` and `README.zh-CN.md` say "the user" and "Claude". People read them.
  This is a deliberate exception. Keep it.

## Develop from a local clone

An installed plugin is a copy. Claude Code stores it under `~/.claude/plugins/cache/` and
refreshes it only when the version in `plugin.json` changes. So edits in the clone stay
out of the installed copy. AI checks an edit in three ways:

1. AI runs the tests.

   ```bash
   python3 -m pytest
   ```

2. AI runs the script from the clone. `CCSB_DATA_DIR` points it at a temp folder.

   ```bash
   CCSB_DATA_DIR="$(mktemp -d)" bin/ccsb --help
   ```

3. HUMAN loads the clone as a plugin for one CLI session. The plugin loads in place.

   ```bash
   claude --plugin-dir /path/to/cc-session-breakdown
   ```

## Tests

```bash
python3 -m pytest
```

- GitHub Actions runs the tests on macOS and Linux, on Python 3.9 and the newest Python.
- AI runs the tests before every commit.
- A test sets `CCSB_DATA_DIR` to a temp folder and `CCSB_CLAUDE_BIN` to a fake program.
  Tests stay away from `~/.claude/` and from the real `claude` program.

## Rules for changes

1. **Standard library only** at run time. The script runs on Python 3.9.
2. **Made-up data only.** Fixtures, examples, and docs hold invented numbers and titles.
   Real transcripts, session titles, and quota samples stay out of the repo.
3. **`SKILL.md` holds usage only.** Development notes go in this file.
4. **Two READMEs change together.** A commit that changes `README.md` also changes
   `README.zh-CN.md`.
5. **Every release raises the version** in `.claude-plugin/plugin.json` and adds a
   `CHANGELOG.md` entry. Claude Code keeps an installed copy until the version changes.
6. **The report keeps working without quota data.** Quota figures come from
   `claude -p "/usage"`, which has no documented contract. When a sample fails, the report
   prints the token and cost parts and marks the quota part.
7. **The `/usage` labels are matched as exact text.** The tests pin the text. When Claude
   Code changes the wording, AI updates the patterns and the tests together.
8. **Quota bars:** `5h`, `Week·All`, and `Week·Fable`. Other weekly model bars are ignored.

## Data folder

The script stores samples, state, and the sampling switch (`config.json`) in
`~/.claude/cc-session-breakdown/`. `CCSB_DATA_DIR` moves it. The folder stays when the
plugin is removed. The Python script and `plan_quota_sampler.sh` both resolve this folder
and read the switch, so a change to either rule changes both files.

## Bundled prices

`scripts/pricing.json` is the starting price table. On a user's machine, AI writes newer
prices to `pricing.override.json` in the data folder, after HUMAN approves them.

To refresh the bundled table, AI:

1. Reads https://platform.claude.com/docs/en/about-claude/pricing .
2. Compares every value in `scripts/pricing.json` with the page, and adds missing models.
3. Shows HUMAN the changed values: model, field, old, new.
4. After HUMAN approves, edits the file and sets `read` to the date of the check.
