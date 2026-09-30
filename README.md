# cc-session-breakdown (`ccsb`)

English | [简体中文](README.zh-CN.md)

A Claude Code plugin that shows where one session spent its tokens: by model, by agent,
and by kind of work. It also estimates what the next turns cost now and after `/compact`.

## What you get

- **One session, fully split.** Each model has its own table. Each row is a kind of work:
  think/reply, web search, read code, edit code, run command, spawn agent, UI control.
- **Main session and subagents apart.** The report marks which rows belong to the main
  session. It lists the most expensive subagent tasks.
- **Three numbers per session:** share of the 5-hour bar, share of the weekly bars, and
  the cost in US dollars at API list prices.
- **`/compact` estimate.** One line: the cost of the next turns at the current context
  size, and the cost after `/compact`.
- **Any local session.** Ask for the current session, or name another one by its title.
- **Hidden costs.** Cold starts after an idle hour, and screenshots at 10% or more.
- **English and Chinese reports.** The report follows the language of the session.

## Example

All numbers below are made up.

```markdown
**📊 Session breakdown (estimate)**

- 5h: ≈ 18.00%
- Week·All: ≈ 3.00%
- Week·Fable: ≈ 1.50%
- API cost: $24.00

**Opus** (Week·All 1.80%, Share 60.00%)

| Work type | Tokens | Week·All | Share | Main |
|---|--:|--:|--:|:-:|
| Web search | 2.0M | 1.05% | 35.00% | - |
| Run command | 600K | 0.36% | 12.00% | - |
| Think/reply | 400K | 0.24% | 8.00% | - |
| Edit code | 150K | 0.15% | 5.00% | - |

**Top tasks** (first-level agent, incl. its subagents)

| # | Model | Week·All | Share | Task |
|--:|---|--:|--:|---|
| 1 | Opus+Sonnet | 1.20% | 40.00% | Compare three hosting plans for a small web app |
| 2 | Opus | 0.60% | 20.00% | Fix the flaky login test |
```

## Requirements

| Item | Requirement |
|---|---|
| Tool | Claude Code: the CLI, the desktop app's Code tab, or an IDE extension |
| Plan | A Claude subscription plan. The quota figures come from `/usage` |
| System | macOS or Linux |
| Python | 3.9 or newer. No extra packages |

Windows, claude.ai chat, and Cowork are outside the scope of this plugin.

## Install

```bash
claude plugin marketplace add McDyZzz/cc-session-breakdown
```

```bash
claude plugin install ccsb@cc-session-breakdown
```

Then start a new session, or run `/reload-plugins` in an open one.

## Use

Ask Claude in plain words, or call the skill by name.

| You want | You say |
|---|---|
| The current session | "How much did this session use?" or `/ccsb:report` |
| Another session | "Check the usage of the release notes session" |
| The `/compact` estimate | "Run `ccsb --estimate --context 200000 --turns 10`" |

Claude Code adds the `ccsb` command to the PATH of Claude's Bash tool. Your own terminal
does not have it, so ask Claude to run it.

## What the plugin runs on your machine

- **A Stop hook.** After each Claude turn, the hook starts a background job and returns
  at once.
- **A quota sample, at most once per 5 minutes from the hook.** The job runs `claude -p "/usage"` and
  stores the bar percentages. This command makes no model call and uses 0 tokens.
  A report of the current session also takes one sample.
- **A switch for the sampling.** Sampling is on by default. To turn it off, say
  "turn off ccsb sampling" or run `/ccsb:sampling off`. `/ccsb:sampling on` turns it back
  on, and `/ccsb:sampling status` shows the switch, the number of samples, and the
  calibrated bars. While sampling is off, the hook starts no job, and a report takes no
  sample. The report then shows the token counts and the API cost, with no quota %.
  The switch lives in `config.json` in the data folder.
- **Local reads.** The report reads the session transcripts under `~/.claude/projects/`.

All data stays on your machine. The plugin's own code makes no network request. The
`claude` program contacts Anthropic to read your plan usage, as it does when you type
`/usage`.

## Quota figures need a few days

The plugin learns how many tokens equal 1% of each bar on your plan. It learns this from
the samples on your machine.

- Until it has enough samples, a bar shows `not calibrated`. Token counts and the API cost
  work from the first run.
- Supported bars: the 5-hour bar, the weekly bar for all models, and the weekly Fable bar.
- The weekly Fable figure shows when the session used Fable and your `/usage` has a Fable
  bar. Other weekly model bars are ignored.

## Prices

The plugin ships a price table, `scripts/pricing.json`. Claude checks it against the
official pricing page when a model is missing, or every 30 days. Claude shows you each
changed value and asks before it writes. Approved values go to `pricing.override.json` in
the data folder, so a plugin update keeps them.

## Data

| Item | Value |
|---|---|
| Folder | `~/.claude/cc-session-breakdown/` |
| Content | Quota samples, calibration, the sampling switch (`config.json`), price override, an error log, and reading state with the paths of your transcript files. No conversation text |
| Move it | Set the environment variable `CCSB_DATA_DIR` |
| After uninstall | The folder stays, so a reinstall keeps the calibration |

Delete the samples and the calibration only: run `/ccsb:sampling delete`. Claude lists the
files first, and deletes them after you confirm. The delete keeps the switch, the reading
state, and the price files. While sampling is on, new samples start the calibration again.

The `/ccsb:sampling` skill runs these commands. You can also ask Claude to run them:

| Command | Effect |
|---|---|
| `ccsb --sampling on`, `off`, or `status` | Turns sampling on or off, or shows its status |
| `ccsb --delete-samples` | Lists the sample and calibration files. Deletes nothing |
| `ccsb --delete-samples --yes` | Deletes those files |

Delete all data:

```bash
rm -rf ~/.claude/cc-session-breakdown
```

## Update and uninstall

```bash
claude plugin update ccsb@cc-session-breakdown
```

```bash
claude plugin uninstall ccsb@cc-session-breakdown
```

## Limits

- **The numbers are estimates.** Anthropic does not publish how plan quota is computed.
  The plugin weights tokens by API price and calibrates against your own `/usage`.
- **`/usage` has no documented contract.** A Claude Code update can change its output.
  The report then prints `Quota read failed` for the quota part. The token and cost parts
  keep working.
- **Tested by hand on one subscription plan, on macOS.** Automated tests cover Linux.
  Reports from other plans are welcome in the issues.
- **Lookup by title needs a session title.** Claude Code writes the title into the
  transcript. This was checked with sessions from the desktop app. A session with no
  title is still reachable by its id.

## Development

See [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
