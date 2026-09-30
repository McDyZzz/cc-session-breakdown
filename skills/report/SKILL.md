---
name: report
description: Use when HUMAN asks how many tokens, how much plan quota, or how much money at API prices a Claude Code session used, how usage splits across models, agents, and kinds of work, or asks to check the usage of another local session by its title.
---

# report

HUMAN is the user. AI is the agent that runs this skill.

This skill reports the quota use of one Claude Code session. The report shows the 5h and
weekly share, and the cost in US dollars at API list prices. It also shows the split by
model and kind of work, the most expensive tasks, cold-start waste, and screenshot cost.
The `ccsb` script computes every number. AI names the top tasks and prints the report.

Scope:

- Claude Code only.
- The quota figures need a Claude subscription plan, because they come from `/usage`.
  The API cost works without a plan.
- macOS and Linux.

The plugin's Stop hook samples the plan bars in the background. The quota estimates
improve as samples add up. HUMAN turns the sampling on or off with `/ccsb:sampling`.
While sampling is off, the report shows no quota percentage.

## Steps

1. AI picks the target session.
   - HUMAN gives no session hint: AI takes the current session and runs `ccsb` with no
     arguments.
   - HUMAN names a session by title ("check the release notes one"): AI runs
     `ccsb --title "<words HUMAN used>"`.
2. AI runs:
   ```bash
   ccsb [--title "<text>" | --session <id>]
   ```
3. AI acts on the first line of the script output:

   | First line | AI does |
   |---|---|
   | `REPORT` | Step 4 |
   | `MULTIPLE` | Shows the candidate list (title, project, last active) to HUMAN, asks which one, then reruns with `--session <id>` |
   | `ERROR: …` | Tells HUMAN the error in one line (current session id not found, or unknown session id). Stops. |
   | `CANDIDATES` | No title matched by text. The script lists up to 60 sessions: those sharing a word or a 2-character CJK piece with the query, then the 30 most recent. AI picks by meaning: one clear fit → reruns with `--session <id>`; several fits → shows them to HUMAN and asks which one; no fit → tells HUMAN in one line that no session matches. |

4. AI fills the task names. The report holds placeholders `{{TASK_1}}` … `{{TASK_10}}`.
   Below the report, a `TASK CONTEXT` block gives each task's description and the first
   300 characters of its prompt. For each placeholder, AI writes a short name:
   - Language: the value of `lang:` in the report header. The script sets it from the
     language HUMAN used in that session.
   - Length: up to 20 CJK characters, or up to 40 Latin characters. No `|` character.
   - Content: what the agent did, in words HUMAN can read cold. Example:
     "Simulator check: sign-up flow after router change",
     not "Simulator walk after router cha…".
5. AI prints the report as Markdown, directly after its answer, outside any code block.
   - AI removes the `REPORT` line, the `lang:` line, any `PRICE_CHECK:` line, and the
     `TASK CONTEXT` block.
   - AI copies every other line exactly. AI adds no summary, advice, or extra lines.
6. AI checks prices when the output has a `PRICE_CHECK: due (<reason>)` line. The script
   prints it when a model is missing from the price table, or when the last check is more
   than 30 days old. After printing the report, AI:
   1. Spawns a subagent to read https://platform.claude.com/docs/en/about-claude/pricing.
      The subagent compares every value in the price table with that page. It also reports
      the prices of any missing model. The price table is the plugin's
      `scripts/pricing.json` plus any `pricing.override.json` in the data folder.
   2. No difference: AI runs `ccsb --mark-price-checked`. AI tells HUMAN nothing.
   3. Any difference: AI shows HUMAN the changed values (model, field, old, new). After
      HUMAN approves, AI:
      1. Writes the changed values to `~/.claude/cc-session-breakdown/pricing.override.json`.
         When `CCSB_DATA_DIR` is set, the file goes in that folder instead.
      2. Runs `ccsb --mark-price-checked`.

## Price override file

`pricing.override.json` uses the same schema as `scripts/pricing.json`:

```json
{
  "web_search_usd_per_request": 0.01,
  "models": {
    "claude-example-1": {"input": 3.0, "write_5m": 1.25, "write_1h": 2.0, "cache_read": 0.1, "output": 5.0}
  }
}
```

- Each model entry in the override replaces the bundled entry for that model id as a whole.
  So AI writes all five price fields, plus `fast_multiplier` when the model has one.
- `web_search_usd_per_request` in the override replaces the bundled value.
- The script ignores an override as a whole when it has invalid JSON, a wrong type, or a
  price outside the accepted range. Accepted: `input` from 0.001 to 10000, each multiplier
  from 0.001 to 1000, `web_search_usd_per_request` from 0 to 1000. The report then prints
  one warning line. AI fixes the file and reruns the report.

## Example output

The script prints labels in the session language. This example uses `lang: en`.

````markdown
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

**Cold-start waste** (back after >1h idle, 1 time)

| Model | Week·All | Share |
|---|--:|--:|
| Fable | 0.15% | 5.00% |

Context: 250K / 1M (25.00%)

Share = this row's weekly quota ÷ this session's weekly quota (weighted by model price, not token count). Calibrated from 12 samples. API cost = this session's tokens at API list prices.
````

When screenshots reach 10% or more of the session, the script adds this block after
the cold-start block:

````markdown
**⚠️ High screenshot cost** (30 images, share 12.00%)

| Source | Model | Images | Week·All | Share |
|---|---|--:|--:|--:|
| computer-use | Fable | 14 | 0.18% | 6.00% |
| iOS Simulator | Sonnet | 10 | 0.12% | 4.00% |
| computer-use | Sonnet | 6 | 0.06% | 2.00% |
````

## Section rules (enforced by the script)

- Header: one bullet per bar. Each bar shows the whole session's use, for the current
  session and for any other session. The script divides the session's total weighted units
  by that bar's coefficient. Week·Fable counts Fable units only. A long session can span
  several 5h windows, so its 5h value can pass 100%. After the bars, one `API cost` bullet
  shows the whole session's cost in US dollars (2 decimals, thousands separator), over the
  same calls as the bars.
- Not calibrated: a bar with no calibrated coefficient shows `not calibrated` (zh: `未校准`)
  in place of its percentage. This applies to the header bullets and the model group
  titles. Week·All table cells show `-` while Week·All is not calibrated. The footer then
  adds one sentence that asks for more `/usage` samples.
- Week·Fable: the script shows it only when the session has Fable calls and the stored
  samples hold a Week·Fable bar. With no samples yet, it shows `not calibrated`. When
  samples exist and none holds a Week·Fable bar, the plan has no such bar, so the script
  prints no Week·Fable line. Other weekly model bars in `/usage` are not used.
- Model groups: fixed order Fable, Opus, Sonnet, Haiku, then others. `Week·Fable` shows
  only in the Fable title. When one family used 2+ versions, the title lists them newest
  first, for example `**Opus 5.5 + 5**`.
- Work types: Think/reply, Web search, Read code, Edit code, Run command, Spawn agent,
  UI control, Other tools. Rows with no usage are hidden. `Main` is `✓` for the main
  session and `-` for agents. One model and work type used by both the main session and
  agents gives two rows.
- Web search also covers Bash calls that fetch or read web content: agent-reach, opencli,
  mcporter, yt-dlp, xreach, r.jina.ai, urllib.request, requests.get, httpx, playwright;
  commands with a non-local http(s) URL and no git/npm/pip/brew/gh/go/cargo; and reads of
  saved `.html` files.
- Top tasks: a task is one first-level subagent plus all its descendants. A workflow run
  counts as one task. Top 3 when any task is above 10%; top 10 otherwise; all tasks when
  fewer exist.
- Cold start: one event each time HUMAN sends a message more than 1 hour after the main
  session's last call. Waste = cache rebuild cost minus the warm cache-read cost. Waste
  from a resumed agent idle over 1 hour adds to the rows without raising the count. One
  row per model.
- Screenshot block: shows only at 10% or more. One row per source and model pair. The
  model is the model of the calls that carry the image's cost.
- Blocks with nothing to show are hidden.
- Tables: Markdown, at most 5 columns, numbers right-aligned. Percentages: 2 decimals.
- `Quota read failed` replaces the bar bullets when `/usage` cannot be parsed. The
  report then uses the last saved coefficients. The `API cost` bullet still shows.
- Sampling off: `config.json` in the data folder holds `"sampling": false`
  (`/ccsb:sampling off` writes it). The report then shows no quota percentage, even when
  old samples and coefficients are on disk:
  - The header shows only the `API cost` bullet: no bar bullets, no `Quota read failed`.
  - A model group title keeps only `Share`, for example `**Opus** (Share 60.00%)`.
  - Every table leaves out its Week·All column.
  - No `not calibrated` text anywhere. The footer drops the calibration sentence. One
    line follows it: `Sampling is off, so quota % is hidden. Turn it on with /ccsb:sampling on.`
    (zh: `采样已关闭，不显示额度 %。用 /ccsb:sampling on 打开。`).
  - Sampling on again: the percentages come back from the samples on disk.
- Language: `zh` when at least 20% of HUMAN's own characters are CJK, or at least half of
  HUMAN's messages contain CJK. The script skips compact summaries, pasted code, and pasted
  logs.

## Estimate option

`ccsb --estimate --context <tokens> --turns 10` estimates the quota cost of the next turns
at a given context size, now and after `/compact` (about 20K). HUMAN or AI can call it from
other rules or scripts. Its output contract:

- Success: one line, exit 0.
- Week·All not calibrated: the line gives weighted units and USD at API prices in place of
  Week·All. It ends with ` (not calibrated)` (zh: `（未校准）`). Week·Fable joins this line
  only when Week·Fable itself is calibrated.
- Week·All calibrated: Week·Fable joins the line for a Fable session under the same rule
  as the report.
- Sampling off: the line gives weighted units and USD at API prices, with no quota figure.
  It ends with ` (sampling off)` (zh: `（采样已关闭）`) in place of ` (not calibrated)`.
- No estimate (`ERROR` or `MULTIPLE`): exit 1.
- Wrong arguments, such as `--estimate` with no `--context`: usage error, exit 2.

## Method

- Token weights: per exact model id, from the price table (base input price and the cache
  write, cache read, and output multipliers, with the source URL and read date).
  1 unit = 1 Sonnet 5 input token. For a model missing from the table, the report prices it
  like the newest known model of its family and adds a warning line. A fast-mode call
  (`usage.speed == "fast"`) is multiplied by the model's `fast_multiplier`. Each web search
  request adds its per-request price. Thinking tokens are already part of `output_tokens`.
- API cost: the script multiplies the session's total weighted units by the Sonnet 5 base
  input price ($/MTok) and divides by 1e6. This equals every call's tokens at its model's
  list prices, with fast mode and web search included.
- Quota coefficients: one per bar (5h, Week·All, Week·Fable), in weighted units per 1%.
  Week·Fable counts Fable units only. Each sample stores the time, the cumulative local
  raw token counts per exact model id and token type across all sessions, the bar
  percentages, and their reset times. Raw counts let the script re-weight old samples
  after a price change. The script uses sample pairs inside one reset window with a large
  enough rise (3 points for weekly bars, 5 for 5h). It takes the 75th percentile of units
  per 1%, or the single value when only one pair exists. The 75th percentile fits because
  Claude use on other devices only adds percent. Without usable pairs, the script keeps the
  last calibrated value. With no such value, the bar is not calibrated.
- Sampling: the plugin's Stop hook runs `scripts/plan_quota_sampler.sh`. The hook checks the
  flag `CCSB_SAMPLING=1`, which the sampler sets inside its own `claude` call, so the hook
  never recurses. The hook then starts `ccsb --sample-if-due` detached and returns.
  That process holds an exclusive file lock for its whole life. So parallel hooks start at
  most one sampler, and the lock frees itself if the process dies. It samples at most
  once per 5 minutes with `claude -p "/usage" --no-session-persistence` (0 tokens,
  45 s timeout). A report run for the current session also triggers one sample.
- Sampling switch: `config.json` in the data folder. A missing file or key means on.
  `ccsb --sampling on|off` writes it, `ccsb --sampling status` prints the switch, the
  sample count and time range, and the calibrated bars. While it is off, the hook exits
  before it starts Python, `--sample-if-due` does nothing (also with `--force`), and a
  report run takes no sample.
- Data folder: `~/.claude/cc-session-breakdown/`, or `CCSB_DATA_DIR` when set. The script
  reads transcripts incrementally and keeps the samples of the last 14 days. It deletes
  older samples each time it records a new one. The coefficient file stays.
  `ccsb --delete-samples` lists the sample and calibration files (`samples.jsonl`,
  `coefficients.json`, `last_attempt.json`, `last-sample.stamp`) and deletes nothing.
  `ccsb --delete-samples --yes` waits for a running sample to end, then deletes them.
  `config.json`, the scan state, the price files, and `error.log` stay.
- Resumed sessions: the script follows the resume chain, reads every earlier session id's
  subagent folder, and dedupes calls by message id.
- Screenshot cost: tokens per image ≈ width × height ÷ 750, read from the image header.
  Cost = one write plus one read on every later call in the same stream, until a compact.
