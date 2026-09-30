# Changelog

This file follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-29

### Added

- `/ccsb:report` skill: token use of one Claude Code session, split by model, by agent,
  and by kind of work, with the cost at API prices.
- Plan quota share for the 5-hour, weekly, and weekly Fable bars, calibrated on each
  machine from `/usage` samples.
- `ccsb --estimate`: cost of the next turns now and after `/compact`.
- Session lookup by title with `ccsb --title`.
- Stop hook that samples the plan quota in the background.
- Price override file in the data folder.
- Report labels in English and Chinese.
