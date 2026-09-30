---
name: sampling
description: Use when HUMAN asks to turn ccsb or plan-quota sampling on or off, to check the sampling status, or to delete the stored /usage samples.
argument-hint: "on | off | status | delete"
---

# sampling

HUMAN is the user. AI is the agent that runs this skill.

The plugin's Stop hook samples the plan quota bars with `claude -p "/usage"`. The report
uses the samples to show quota percentages. This skill turns the sampling on or off, shows
its status, or deletes the samples. Sampling is on by default.

## Steps

1. AI picks the action from the argument or HUMAN's words: `on`, `off`, `status`, or
   `delete`. No clear action: `status`.
2. AI runs the command for that action:

   | Action | AI runs | AI tells HUMAN |
   |---|---|---|
   | `on` | `ccsb --sampling on` | The output line |
   | `off` | `ccsb --sampling off` | The output line, and that reports show no quota % while sampling is off. The token counts and the API cost stay |
   | `status` | `ccsb --sampling status` | The output lines |
   | `delete` | Step 3 | |

3. For `delete`:
   1. AI runs `ccsb --delete-samples`. This dry run deletes nothing and lists the files.
   2. AI shows HUMAN the file list and asks HUMAN to confirm in chat.
   3. Only after a clear yes, AI runs `ccsb --delete-samples --yes` and shows the output.
      No clear yes: AI deletes nothing.

   The delete removes the samples and the calibration. It keeps the on/off switch. While
   sampling stays on, new samples start the calibration again. The last output line gives
   the switch state.

## Notes

- The switch lives in `config.json` in the data folder (`~/.claude/cc-session-breakdown/`,
  or `CCSB_DATA_DIR` when set).
- Turning sampling on again brings back the quota % from the samples still on disk.
- `ERROR: a /usage sample is still running`: AI waits about a minute, then reruns
  `ccsb --delete-samples --yes`.
