# Changelog

This file follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-09-30

### Added

- Sampling switch in `config.json` in the data folder. Sampling stays on by default.
- `/ccsb:sampling` skill: turn sampling on or off, show its status, or delete the samples
  after a dry run and a confirmation.
- `ccsb --sampling on|off|status`: set the switch, or print it with the sample count, the
  sample time range, and the calibrated bars.
- `ccsb --delete-samples [--yes]`: list the sample and calibration files, or delete them
  with `--yes`. The switch, the scan state, and the price files stay.

### Changed

- While sampling is off, the report shows no quota percentage: no bar bullets, no
  Week·All column, and a footer line that says sampling is off. `--estimate` gives
  weighted units and USD and ends with `(sampling off)`. The Stop hook starts no sample.

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
