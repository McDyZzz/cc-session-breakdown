# cc-session-breakdown (`ccsb`)

English | [简体中文](README.zh-CN.md)

A Claude Code plugin. It shows, for one session:

- Tokens, split by model, by agent, and by kind of work
- Share of your plan quota
- Cost at API prices

## Example

```markdown
**📊 Session breakdown (estimate)**

- 5h: ≈ 18.00%
- Week·All: ≈ 3.00%
- Week·Fable: ≈ 1.50%
- API cost: $24.00

**Fable** (Week·All 0.75%, Week·Fable 1.50%, Share 25.00%)

| Work type | Tokens | Week·All | Share | Main |
|---|--:|--:|--:|:-:|
| Spawn agent | 300K | 0.30% | 10.00% | ✓ |
| Think/reply | 180K | 0.24% | 8.00% | ✓ |
| Web search | 90K | 0.12% | 4.00% | ✓ |
| Run command | 60K | 0.09% | 3.00% | ✓ |

**Opus** (Week·All 1.80%, Share 60.00%)

| Work type | Tokens | Week·All | Share | Main |
|---|--:|--:|--:|:-:|
| Web search | 2.0M | 1.05% | 35.00% | - |
| Run command | 600K | 0.36% | 12.00% | - |
| Think/reply | 400K | 0.24% | 8.00% | - |
| Edit code | 150K | 0.15% | 5.00% | - |

**Sonnet** (Week·All 0.45%, Share 15.00%)

| Work type | Tokens | Week·All | Share | Main |
|---|--:|--:|--:|:-:|
| Web search | 1.2M | 0.30% | 10.00% | - |
| Read code | 400K | 0.12% | 4.00% | - |
| Other tools | 30K | 0.03% | 1.00% | - |

**Top tasks** (first-level agent, incl. its subagents)

| # | Model | Week·All | Share | Task |
|--:|---|--:|--:|---|
| 1 | Opus+Sonnet | 1.20% | 40.00% | Compare three hosting plans for a small web app |
| 2 | Opus | 0.60% | 20.00% | Fix the flaky login test |
| 3 | Opus | 0.45% | 15.00% | Draft release notes for version 2 |
```

## Requirements

- Claude Code
- macOS or Linux
- Python 3.9 or newer

## Install

Tell Claude Code:

```
Install McDyZzz/cc-session-breakdown
```

## Use

Report on the current session:

```
/ccsb:report
```

Or ask in plain words:

```
How much did this session use?
```

Report on another session, by its title:

```
Check the usage of the release notes session
```

## What runs in the background

- After each Claude turn, a hook samples your quota bars with `claude -p "/usage"`, at most
  once per 5 minutes.
- For the first days, the quota percentages show `not calibrated`, until enough samples
  exist.
- After a report, when the last price check is over 30 days old, Claude checks the official
  pricing page and asks before it saves a changed price.
- All data stays on your machine, in `~/.claude/cc-session-breakdown/`.

Turn sampling off:

```
/ccsb:sampling off
```

`/ccsb:sampling on` turns it back on. `/ccsb:sampling status` shows the state.

## Update, uninstall, delete

Update:

```
Update the plugin ccsb
```

Uninstall:

```
Uninstall the plugin ccsb
```

Delete the samples:

```
/ccsb:sampling delete
```

Delete all data:

```
Delete the ccsb data folder ~/.claude/cc-session-breakdown
```

## Development

See [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
