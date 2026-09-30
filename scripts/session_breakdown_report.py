#!/usr/bin/env python3
# Scope: reports how much plan quota and API-priced cost one Claude Code session used, and
# records /usage samples.
# Called by the `ccsb` command (bin/ccsb: report, --estimate) and by plan_quota_sampler.sh
# (--sample-if-due). Reads ~/.claude/projects transcripts, pricing.json next to this script,
# and <data dir>/pricing.override.json; writes only the data dir.
"""Plan-quota and API-cost report for one Claude Code session, plus /usage sampling for calibration.

Usage:
  ccsb                          report on the current session ($CLAUDE_CODE_SESSION_ID)
  ccsb --session ID             report on one session (full id or unique prefix)
  ccsb --title TEXT             find a session by title, then report on it
  ccsb --record-sample FILE     parse saved `claude -p /usage` output and store one sample
  ccsb --sample-if-due [--force]  take one /usage sample (Stop hook, detached)
  ccsb --estimate --context N [--turns 10] [--session ID]
                                quota cost of the next turns at context N (200000, 200K, 1.2M):
                                one line and exit 0, or ERROR / MULTIPLE and exit 1. While
                                Week·All has no calibrated coefficient, the line gives weighted
                                units and USD at API prices and ends with a not-calibrated mark.
  ccsb --mark-price-checked     record today as the last price check
  ccsb ... --json               print the computed numbers as JSON (debug)

A report may carry a third line `PRICE_CHECK: due (<reason>)`: a model is missing from
the merged price table, or the last price check is more than 30 days old.
First output line: REPORT, MULTIPLE, CANDIDATES (--title without a strong match: sessions
sharing a word or CJK bigram with the query, then the most recent ones), or ERROR.
Python 3.9+ standard library only (macOS, Linux). Data dir: $CCSB_DATA_DIR, default
~/.claude/cc-session-breakdown/. Errors go to <data dir>/error.log.

Weighted units of an API call = sum of tokens x price multiplier x the model's base input
price ratio (Sonnet 5 = 1). "Tokens" in the report are input-equivalent tokens: the same
sum without the model ratio. Quota coefficients (weighted units per 1% of a bar) come from
samples of `/usage`; see compute_coefficients(). A bar without a calibrated coefficient
shows a not-calibrated label. API cost in USD = weighted units x the Sonnet 5 base input
price / 1e6.
"""
import argparse
import base64
import bisect
import contextlib
import fcntl
import hashlib
import shutil
import json
import math
import os
import re
import statistics
import struct
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
DATA_DIR = os.environ.get("CCSB_DATA_DIR") or os.path.join(HOME, ".claude", "cc-session-breakdown")
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
STATE_FILE = "state.json"
SAMPLES_FILE = "samples.jsonl"
COEF_FILE = "coefficients.json"
ATTEMPT_FILE = "last_attempt.json"
PRICE_CHECK_FILE = "price-check.json"  # {"checked": "YYYY-MM-DD"}; kept out of pricing.json
PRICE_OVERRIDE_FILE = "pricing.override.json"  # same schema as pricing.json; see price_table()
PRICE_CHECK_MAX_DAYS = 30

# ---------------------------------------------------------------------------------------
# Prices live in pricing.json next to this script: per exact model id, the base input
# $/MTok and the 5m write, 1h write, cache read, and output multipliers of that base price.
# <data dir>/pricing.override.json, when present, overlays it (see price_table()).
PRICING_FILE = os.path.join(SCRIPT_DIR, "pricing.json")
REFERENCE_INPUT_PRICE = 2.0  # Sonnet 5 base input $/MTok: 1 unit = 1 Sonnet 5 input token
FALLBACK_PRICED_MODEL = "claude-sonnet-5"  # unknown model of an unknown family

# Context window per model id prefix. "[1m]" in a model id also means 1M.
CONTEXT_WINDOW = [
    ("claude-haiku-4-5", 200_000),
    ("claude-fable-", 1_000_000), ("claude-mythos-", 1_000_000),
    ("claude-opus-5", 1_000_000), ("claude-opus-4-8", 1_000_000),
    ("claude-opus-4-7", 1_000_000), ("claude-opus-4-6", 1_000_000),
    ("claude-opus-4-5", 200_000),
    ("claude-sonnet-5", 1_000_000), ("claude-sonnet-4-6", 1_000_000),
    ("claude-sonnet-4-5", 200_000),
]

FAMILY_ORDER = ["Fable", "Opus", "Sonnet", "Haiku"]  # display order; other families follow
KNOWN_FAMILIES = FAMILY_ORDER + ["Mythos"]

# Quota calibration: weighted units per 1% of each bar, from /usage samples only.
BARS = ("5h", "week_all", "week_fable")
MIN_RISE_PCT = {"5h": 5.0, "week_all": 3.0, "week_fable": 3.0}
MIN_INTERVALS_FOR_P75 = 2
SAMPLE_VERSION = 2            # samples hold raw token counts per model id
STATE_VERSION = 3             # scanner state: per-id usage snapshots keyed by (model, speed)
HASH_BYTES = 4096             # head and tail hashes of scanned files
SAMPLE_GAP_S = 300            # at most one /usage sample per 5 minutes
USAGE_TIMEOUT_S = 45
SAME_WINDOW_S = 3600          # reset times this close belong to one window
SEEN_DAYS = 8                 # state keeps message ids this long
SAMPLE_KEEP_DAYS = 14         # samples of the last 2 weekly windows

COLD_GAP_S = 3600
COMPACT_CONTEXT = 20_000      # context assumed after /compact, for --estimate
DEFAULT_CALLS_PER_TURN = 3
DICE_STRONG = 0.4             # CJK title search: character-bigram Dice, strong match
CANDIDATE_RECENT = 30         # recent sessions added to CANDIDATES
CANDIDATE_MAX = 60            # CANDIDATES line cap
SCREENSHOT_MIN_SHARE = 10.0
IMAGE_FALLBACK_TOKENS = 1568
TOP_SHARE_THRESHOLD = 10.0

# ---------------------------------------------------------------------------------------
# Work types. First matching rule per API call wins, in this order: spawn, edit, ui, web,
# run, read, other; a call with no tool use is think.
SPAWN_TOOLS = {"Agent", "Task", "Workflow"}
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
UI_PREFIXES = ("mcp__Claude_Code_iOS_Simulator__", "mcp__computer-use__")
WEB_TOOLS = {"WebSearch", "WebFetch"}
WEB_PREFIXES = ("mcp__Claude_Browser__", "mcp__claude-in-chrome__", "mcp__Control_Chrome__")
READ_TOOLS = {"Read", "Grep", "Glob", "LS"}

# Research Bash (counts as Web search): curl/jina fetches, mcporter exa search,
# agent-reach, python fetch scripts, helper scripts called with URLs, and grep/python over
# saved .html pages.
RESEARCH_BASH_KEYWORDS = re.compile(
    r"(?<![\w-])(agent-reach|opencli|mcporter|yt-dlp|xreach|r\.jina\.ai|urllib\.request"
    r"|requests\.get|httpx|playwright)(?![\w-])")
URL_HOST = re.compile(r"https?://([^/\s\"'`)>]+)")
LOCAL_HOST = re.compile(
    r"^(localhost|127(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}|0\.0\.0\.0|\[::1\]"
    r"|([a-z0-9-]+\.)+localhost|([a-z0-9-]+\.)+test)\.?(:\d+)?$", re.I)
PACKAGE_TOOL = re.compile(r"(?:^|[;&|(]\s*)(git|npm|npx|pnpm|yarn|pip3?|brew|gh|go|cargo)\s")
SAVED_PAGE = re.compile(r"\.html?\b")

SCREENSHOT_SOURCES = [
    ("mcp__computer-use__", "computer_use"),
    ("mcp__Claude_Code_iOS_Simulator__", "ios_sim"),
    ("mcp__Claude_Browser__", "browser"),
    ("mcp__claude-in-chrome__", "browser"),
    ("mcp__Control_Chrome__", "browser"),
    ("Read", "read_file"),
]

LABELS = {
    "en": {
        "title": "**📊 Session breakdown (estimate)**",
        "week_all": "Week·All", "week_fable": "Week·Fable", "share": "Share",
        "colon": ": ", "sep": ", ", "paren": (" (", ")"),
        "quota_failed": "Quota read failed",
        "api_cost": "API cost",
        "work_head": "| Work type | Tokens | Week·All | Share | Main |",
        "top_title": "**Top tasks** (first-level agent, incl. its subagents)",
        "top_head": "| # | Model | Week·All | Share | Task |",
        "cold_title": "**Cold-start waste** (back after >1h idle, {n} {times})",
        "cold_head": "| Model | Week·All | Share |",
        "shot_title": "**⚠️ High screenshot cost** ({n} images, share {share})",
        "shot_head": "| Source | Model | Images | Week·All | Share |",
        "context": "Context: {used} / {window} ({pct})",
        "context_nowin": "Context: {used}",
        "footer": ("Share = this row's weekly quota ÷ this session's weekly quota "
                   "(weighted by model price, not token count). Calibrated from {n} {samples}."),
        "footer_uncalibrated": " A not-calibrated bar needs more /usage samples.",
        "footer_cost": " API cost = this session's tokens at API list prices.",
        "work": {"think": "Think/reply", "web": "Web search", "read": "Read code",
                 "edit": "Edit code", "run": "Run command", "spawn": "Spawn agent",
                 "ui": "UI control", "other": "Other tools"},
        "source": {"computer_use": "computer-use", "ios_sim": "iOS Simulator",
                   "browser": "Browser", "read_file": "Read file", "other": "Other"},
        "workflow": "Workflow {name} ({n} agents)",
        "unknown_model": "⚠️ Unknown model {id}: priced as {used} for now. Please add it to pricing.override.json.",
        "malformed": "⚠️ {n} records could not be parsed; the numbers may be too low.",
        "estimate": "Next {turns} turns (about {calls} calls, {ctx} context, {model}): {now}; "
                    "after /compact to about {small}: {after}",
        "estimate_units": "≈ {units} weighted units, {usd}",
        "estimate_uncalibrated": " (not calibrated)",
        "uncalibrated": "not calibrated",
        "override_invalid": "⚠️ pricing.override.json is invalid and was ignored: {why}",
    },
    "zh": {
        "title": "**📊 会话明细（估算）**",
        "week_all": "周·全部", "week_fable": "周·Fable", "share": "占比",
        "colon": "：", "sep": "，", "paren": ("（", "）"),
        "quota_failed": "额度读取失败",
        "api_cost": "API 计价",
        "work_head": "| 工作类型 | token | 周·全部 | 占比 | 主session |",
        "top_title": "**最费周额度的任务**（第一层子 agent，含它的子 agent）",
        "top_head": "| # | 模型 | 周·全部 | 占比 | 任务 |",
        "cold_title": "**冷启动浪费**（离开 >1h 后回来，共 {n} 次）",
        "cold_head": "| 模型 | 周·全部 | 占比 |",
        "shot_title": "**⚠️ 截图开销较大**（共 {n} 张，占比 {share}）",
        "shot_head": "| 来源 | 模型 | 张数 | 周·全部 | 占比 |",
        "context": "上下文: {used} / {window}（{pct}）",
        "context_nowin": "上下文: {used}",
        "footer": ("占比 = 该行占用的周额度 ÷ 本 session 占用的周额度"
                   "（按模型价格加权，不按 token 数）。系数来自 {n} 个采样样本。"),
        "footer_uncalibrated": "标为未校准的额度条需要更多 /usage 样本。",
        "footer_cost": "API 计价 = 本会话 token 按 API 标价计算的费用。",
        "work": {"think": "思考/回复", "web": "搜索网页", "read": "读代码",
                 "edit": "改代码", "run": "跑命令", "spawn": "派 agent",
                 "ui": "界面操作", "other": "其他工具"},
        "source": {"computer_use": "computer-use", "ios_sim": "iOS 模拟器",
                   "browser": "浏览器", "read_file": "读图片文件", "other": "其他"},
        "workflow": "Workflow {name}（{n} 个 agent）",
        "unknown_model": "⚠️ 未知模型 {id}，暂按 {used} 价格估算，请把它加进 pricing.override.json",
        "malformed": "⚠️ {n} 条记录无法解析，数字可能偏低",
        "estimate": "接下来 {turns} 轮（约 {calls} 次调用，{ctx} 上下文，{model}）：{now}；"
                    "compact 到约 {small} 后：{after}",
        "estimate_units": "≈ {units} 加权单位，{usd}",
        "estimate_uncalibrated": "（未校准）",
        "uncalibrated": "未校准",
        "override_invalid": "⚠️ pricing.override.json 无效，已忽略：{why}",
    },
}


# ---------------------------------------------------------------------------------------
# Small helpers

def log_error(msg):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "error.log"), "a") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} session_breakdown_report.py: {msg}\n")
    except OSError:
        pass


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def data_path(name):
    return os.path.join(DATA_DIR, name)


def parse_ts(s):
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


_PRICE_TABLE = None
_RESOLVED = {}


PRICE_FIELDS = ("input", "write_5m", "write_1h", "cache_read", "output")
# Accepted ranges in pricing.override.json, limits included; values outside them give absurd weights.
INPUT_USD_RANGE = (0.001, 10000.0)  # input, USD per million tokens
MULTIPLIER_RANGE = (0.001, 1000.0)  # write_5m, write_1h, cache_read, output, fast_multiplier
WEB_SEARCH_USD_RANGE = (0.0, 1000.0)  # web_search_usd_per_request, USD


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def finite_number(v):
    """v as a finite float, or None when v is not a number (booleans included), is NaN or
    infinite, or is too large for a float."""
    if not is_number(v):
        return None
    try:
        f = float(v)
    except OverflowError:
        return None
    return f if math.isfinite(f) else None


def override_problem(data):
    """Why a pricing.override.json object breaks the schema, or None when it is valid.
    Checked fields: "models" (id -> the five price fields, optional fast_multiplier) and
    web_search_usd_per_request. Every price is a finite number inside its range:
    INPUT_USD_RANGE for input, MULTIPLIER_RANGE for the other fields and fast_multiplier,
    WEB_SEARCH_USD_RANGE for web_search_usd_per_request. Other keys are allowed and
    ignored."""
    def outside(x, limits):
        return x is None or not limits[0] <= x <= limits[1]

    if not isinstance(data, dict):
        return "top level is not an object"
    models = data.get("models", {})
    if not isinstance(models, dict):
        return '"models" is not an object'
    for k, v in models.items():
        if not isinstance(v, dict):
            return f"model {k} is not an object"
        for f in PRICE_FIELDS:
            limits = INPUT_USD_RANGE if f == "input" else MULTIPLIER_RANGE
            if outside(finite_number(v.get(f)), limits):
                return f"model {k}: {f} is missing or not a number from {limits[0]:g} to {limits[1]:g}"
        if "fast_multiplier" in v and outside(finite_number(v["fast_multiplier"]), MULTIPLIER_RANGE):
            return (f"model {k}: fast_multiplier is not a number from "
                    f"{MULTIPLIER_RANGE[0]:g} to {MULTIPLIER_RANGE[1]:g}")
    if "web_search_usd_per_request" in data and \
            outside(finite_number(data["web_search_usd_per_request"]), WEB_SEARCH_USD_RANGE):
        return (f"web_search_usd_per_request is not a number from "
                f"{WEB_SEARCH_USD_RANGE[0]:g} to {WEB_SEARCH_USD_RANGE[1]:g}")
    return None


def load_price_override():
    """(override object or None, problem text or None). A missing file gives (None, None)."""
    path = data_path(PRICE_OVERRIDE_FILE)
    if not os.path.exists(path):
        return None, None
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        return None, f"{type(e).__name__}: {one_line(e)[:120]}"
    why = override_problem(data)
    return (None, why) if why else (data, None)


def price_table():
    """Prices loaded once: pricing.json next to this script, overlaid by
    <data dir>/pricing.override.json when that file is valid. An override model entry
    replaces the bundled entry for that id as a whole; an override
    web_search_usd_per_request replaces the bundled value. An invalid override is ignored
    as a whole, and "override_error" holds the reason.
    "models" maps model id -> (base input, 5m write x, 1h write x, cache read x, output x);
    "fast" maps model id -> fast-mode multiplier; "search_usd" is the price of one web
    search request; "hash" identifies the merged prices (coefficient cache key)."""
    global _PRICE_TABLE
    if _PRICE_TABLE is None:
        with open(PRICING_FILE) as f:
            data = json.load(f)
        raw = dict(data["models"])
        search_usd = float(data.get("web_search_usd_per_request", 0.0))
        override, override_error = load_price_override()
        if override_error:
            log_error(f"{PRICE_OVERRIDE_FILE} ignored: {override_error}")
        if override:
            raw.update(override.get("models", {}))
            if "web_search_usd_per_request" in override:
                search_usd = float(override["web_search_usd_per_request"])
        models = {k: (float(v["input"]), float(v["write_5m"]), float(v["write_1h"]),
                      float(v["cache_read"]), float(v["output"]))
                  for k, v in raw.items()}
        fast = {k: float(v.get("fast_multiplier", 1.0)) for k, v in raw.items()}
        merged = json.dumps({"models": models, "fast": fast, "search_usd": search_usd}, sort_keys=True)
        source = f"{data['source']} (read {data['read']})"
        if override:
            source += f" + {PRICE_OVERRIDE_FILE}"
        _PRICE_TABLE = {"models": models, "fast": fast, "read": data["read"],
                        "search_usd": search_usd, "source": source,
                        "hash": hashlib.sha1(merged.encode()).hexdigest()[:16],
                        "override_error": override_error}
    return _PRICE_TABLE


def resolve_model(model):
    """(priced model id, known). A date suffix (-YYYYMMDD) and [1m] are ignored. An unknown
    id is priced as the newest known model of its family, else as Sonnet 5."""
    if model not in _RESOLVED:
        models = price_table()["models"]
        key = re.sub(r"-\d{8}$", "", model.lower().split("[")[0])
        if key in models:
            _RESOLVED[model] = (key, True)
        else:
            fam = family(model)
            same = [k for k in models if family(k) == fam] if fam != "Other" else []
            best = max(same, key=lambda k: version_key(model_version(k))) if same else FALLBACK_PRICED_MODEL
            _RESOLVED[model] = (best, False)
    return _RESOLVED[model]


def pricing(model):
    return price_table()["models"][resolve_model(model)[0]]


def price_check_due(models, today=None):
    """Reason the price table needs a check, or None. Due when a model is missing from
    the merged price table (pricing.json plus any override), or the last check
    (price-check.json, else pricing.json "read") is more than PRICE_CHECK_MAX_DAYS days old."""
    reasons = []
    unknown = sorted(m for m in models if m and not resolve_model(m)[1])
    if unknown:
        reasons.append("unknown model " + ", ".join(unknown))
    checked = (read_json(data_path(PRICE_CHECK_FILE), {}) or {}).get("checked") or price_table()["read"]
    try:
        days = ((today or datetime.now().date()) - datetime.strptime(checked, "%Y-%m-%d").date()).days
    except (TypeError, ValueError):
        days = None
    if days is None:
        reasons.append("no valid check date")
    elif days > PRICE_CHECK_MAX_DAYS:
        reasons.append(f"last checked {days} days ago")
    return "; ".join(reasons) or None


def mark_price_checked():
    os.makedirs(DATA_DIR, exist_ok=True)
    write_json(data_path(PRICE_CHECK_FILE), {"checked": datetime.now().date().isoformat()})


def speed_factor(model, u):
    """Fast-mode multiplier of one call (1 unless usage.speed is "fast")."""
    if u.get("speed") != "fast":
        return 1.0
    return price_table()["fast"].get(resolve_model(model)[0], 1.0)


def search_units(u):
    """Weighted units of the call's web search requests (web fetch adds nothing)."""
    stu = u.get("server_tool_use")
    n = (stu.get("web_search_requests") or 0) if isinstance(stu, dict) else 0
    return n * price_table()["search_usd"] * 1e6 / REFERENCE_INPUT_PRICE


def family(model):
    m = model.lower()
    for fam in KNOWN_FAMILIES:
        if fam.lower() in m:
            return fam
    return "Other"


def context_window(model):
    if "[1m]" in model.lower():
        return 1_000_000
    for prefix, size in CONTEXT_WINDOW:
        if model.startswith(prefix):
            return size
    return None


def model_version(model):
    """'claude-opus-5-5' -> '5.5', 'claude-haiku-4-5-20251001' -> '4.5'."""
    m = re.match(r"claude-[a-z]+-(.*)", model.lower())
    parts = []
    for part in re.split(r"[-\[]", m.group(1)) if m else []:
        if not (part.isdigit() and len(part) <= 2):
            break
        parts.append(part)
    return ".".join(parts)


def version_key(v):
    return tuple(int(x) for x in v.split(".")) if v else ()


def family_title(fam, models):
    """'Opus' for one version, 'Opus 5.5 + 5' for several, newest first."""
    versions = sorted({model_version(m) for m in models} - {""}, key=version_key, reverse=True)
    return f"{fam} {' + '.join(versions)}" if len(versions) >= 2 else fam


def family_order(fams, units):
    known = [f for f in FAMILY_ORDER if f in fams]
    return known + sorted((f for f in fams if f not in FAMILY_ORDER), key=lambda f: -units.get(f, 0))


def usage_parts(u):
    """Return input, 5m write, 1h write, cache read, output tokens of one usage block."""
    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    w5m = cc.get("ephemeral_5m_input_tokens")
    w1h = cc.get("ephemeral_1h_input_tokens")
    if w5m is None and w1h is None:
        w5m, w1h = u.get("cache_creation_input_tokens") or 0, 0
    return (u.get("input_tokens") or 0, w5m or 0, w1h or 0,
            u.get("cache_read_input_tokens") or 0, u.get("output_tokens") or 0)


def weigh(model, u):
    """Return (weighted units, input-equivalent tokens) of one API call. Units include the
    fast-mode multiplier and web search requests; tokens do not."""
    base, m5, m1, mr, mo = pricing(model)
    inp, w5m, w1h, read, out = usage_parts(u)
    ie = inp + w5m * m5 + w1h * m1 + read * mr + out * mo
    units = ie * base / REFERENCE_INPUT_PRICE
    fast = speed_factor(model, u)
    if fast != 1.0:
        units *= fast
    search = search_units(u)
    if search:
        units += search
    return units, ie


def is_cjk(ch):
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0x3040 <= o <= 0x30FF
            or 0xAC00 <= o <= 0xD7AF or 0xF900 <= o <= 0xFAFF)


def fmt_pct(x):
    return f"{x:.2f}%"


def fmt_usd(x):
    return f"${x:,.2f}"


def fmt_tokens(n):
    if n >= 999_500:
        return f"{n / 1e6:.1f}M"
    if n >= 1000:
        return f"{n / 1e3:.0f}K"
    return str(int(round(n)))


def fmt_window(n):
    return f"{n / 1e6:g}M" if n >= 1_000_000 else f"{n / 1e3:g}K"


def cell(s):
    return str(s).replace("|", "\\|")


def one_line(s):
    return " ".join(str(s).split())


# ---------------------------------------------------------------------------------------
# Transcript parsing

def content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


def has_tool_result(content):
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def human_text(o):
    """Text of a message HUMAN typed, else None."""
    if o.get("isMeta") or o.get("isCompactSummary") or o.get("isSidechain"):
        return None
    content = (o.get("message") or {}).get("content")
    if has_tool_result(content):
        return None
    text = content_text(content)
    origin = o.get("origin")
    if isinstance(origin, dict):
        return text if origin.get("kind") == "human" else None
    t = text.strip()
    if not t or t.startswith("<") or t.startswith("[Request interrupted"):
        return None
    return text


WRAPPER_BLOCK = re.compile(
    r"<(system-reminder|pasted_content|command-[\w-]+|local-command-[\w-]+)\b[^>]*>.*?</\1[^>]*>"
    r"|```.*?(```|$)", re.S)
TAG = re.compile(r"<[^>]+>")
CODE_LINE = re.compile(r"[{}();=\[\]<>]|^\s*(//|#|\+|-|\*|\$)")


def jpeg_size(b):
    i = 2
    while i + 9 < len(b):
        if b[i] != 0xFF:
            i += 1
            continue
        marker = b[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack(">H", b[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return w, h
        i += 2 + seg_len
    return None


def image_tokens(src):
    """Tokens of one image block: width x height / 750, from the PNG or JPEG header."""
    data = src.get("data") if isinstance(src, dict) and src.get("type") == "base64" else None
    wh = None
    try:
        if isinstance(data, str) and data.startswith("iVBORw0KGgo"):
            head = base64.b64decode(data[:44])
            wh = struct.unpack(">II", head[16:24])
        elif isinstance(data, str) and data.startswith("/9j/"):
            n = min(len(data), 262144) // 4 * 4
            wh = jpeg_size(base64.b64decode(data[:n]))
    except (ValueError, struct.error):
        wh = None
    if wh and wh[0] > 0 and wh[1] > 0:
        return wh[0] * wh[1] / 750.0
    return float(IMAGE_FALLBACK_TOKENS)


def new_stream(path, is_main):
    """One conversation stream: the main session, or one agent id. A stream can span
    several files (resume chain); seq numbers lines across them, and images and compacts
    are deduped within the stream."""
    return {"path": path, "is_main": is_main, "raw_calls": [], "calls": [], "humans": [],
            "images": [], "img_keys": set(), "compacts": [], "compact_ids": set(), "seq": 0,
            "sids": set(), "first_prompt": None, "bad": 0}


USAGE_COUNTS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
NESTED_COUNTS = {"cache_creation": ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"),
                 "server_tool_use": ("web_search_requests", "web_fetch_requests")}


def valid_count(v):
    return v is None or (isinstance(v, int) and not isinstance(v, bool) and v >= 0)


def valid_usage(u):
    """True when every token count, nested ones included, is missing or an int >= 0."""
    if not isinstance(u, dict) or not all(valid_count(u.get(k)) for k in USAGE_COUNTS):
        return False
    for key, fields in NESTED_COUNTS.items():
        sub = u.get(key)
        if sub is None:
            continue
        if not isinstance(sub, dict) or not all(valid_count(sub.get(f)) for f in fields):
            return False
    return True


def usage_size(u):
    return sum(usage_parts(u))


def parse_stream(path, is_main, registry, tool_names, s=None):
    """Parse one transcript file into calls, HUMAN messages, images, and compacts.

    registry maps message id -> call, across all files of the session. A message id seen
    again (streaming lines, or a copy in a resumed transcript) updates that call: the
    largest usage wins and tool uses are merged. The call stays in the stream that saw it
    first. tool_names maps tool_use id -> tool name. Pass s to continue a stream from an
    earlier file. Call finalize_stream() after every file of the session is parsed.
    s["bad"] counts malformed JSON lines and usage records.
    """
    s = s or new_stream(path, is_main)
    with open(path, "rb") as f:
        for raw in f:
            s["seq"] += 1
            seq = s["seq"]
            head = raw[:400]
            is_user = b'"type":"user"' in head
            # Tool results without images carry nothing the report needs; skip the parse.
            if is_user and b'"tool_use_id"' in raw and b'"type":"image"' not in raw:
                continue
            if not is_user and b'"type":"system"' in head and b'compact_boundary' not in raw:
                continue
            if not raw.strip():
                continue
            try:
                o = json.loads(raw)
            except ValueError:
                if raw.endswith(b"\n"):  # an unterminated last line is still being written
                    s["bad"] += 1
                continue
            if not isinstance(o, dict):
                s["bad"] += 1
                continue
            t = o.get("type")
            sid = o.get("sessionId")
            if isinstance(sid, str):
                s["sids"].add(sid)
            if t == "assistant":
                parse_assistant(o, s, registry, tool_names)
            elif t == "user":
                parse_user(o, s, seq)
            elif t == "system" and o.get("subtype") == "compact_boundary":
                cid = o.get("uuid") or seq
                if cid not in s["compact_ids"]:
                    s["compact_ids"].add(cid)
                    s["compacts"].append((len(s["raw_calls"]), seq))
    return s


def finalize_stream(s):
    """Drop calls without usage, then map image and compact positions onto kept calls."""
    kept_before = [0]
    for c in s["raw_calls"]:
        kept_before.append(kept_before[-1] + (c["usage"] is not None))
    s["calls"] = [c for c in s["raw_calls"] if c["usage"] is not None]
    for img in s["images"]:
        img["pos"] = kept_before[img["raw_pos"]]
        # Prefix before the image: context plus output of the last call before it.
        prev = s["calls"][img["pos"] - 1]["usage"] if img["pos"] else None
        img["offset"] = sum(usage_parts(prev)) if prev else 0
    s["compacts"] = sorted((kept_before[rp], seq) for rp, seq in s["compacts"])
    for c in s["calls"]:
        c["units"], c["ie"] = weigh(c["model"], c["usage"])
        c["fam"] = family(c["model"])
        c["is_main"] = s["is_main"] and not c["sidechain"]
    return s


def parse_assistant(o, s, registry, tool_names):
    m = o.get("message") or {}
    model = m.get("model") or ""
    if model == "<synthetic>":
        return
    key = m.get("id") or o.get("requestId")
    if not key:
        return
    c = registry.get(key)
    if c is None:
        c = {"id": key, "model": model, "ts": parse_ts(o.get("timestamp")), "usage": None,
             "tools": {}, "sidechain": bool(o.get("isSidechain"))}
        registry[key] = c
        s["raw_calls"].append(c)
    u = m.get("usage")
    if u is not None:
        if not valid_usage(u):
            s["bad"] += 1
        elif c["usage"] is None or usage_size(u) >= usage_size(c["usage"]):
            c["usage"] = u
    for b in m.get("content") or []:
        if isinstance(b, dict) and b.get("type") == "tool_use":
            name = b.get("name") or ""
            tid = b.get("id") or f"_{len(c['tools'])}"
            cmd = str((b.get("input") or {}).get("command", "")) if name == "Bash" else None
            c["tools"][tid] = (name, cmd)
            tool_names[b.get("id")] = name


def parse_user(o, s, seq):
    content = (o.get("message") or {}).get("content")
    if isinstance(content, list):
        for b in content:
            if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                continue
            inner = b.get("content")
            if not isinstance(inner, list):
                continue
            for i, ib in enumerate(inner):
                if isinstance(ib, dict) and ib.get("type") == "image":
                    if (b.get("tool_use_id"), i) in s["img_keys"]:
                        continue
                    s["img_keys"].add((b.get("tool_use_id"), i))
                    s["images"].append({"raw_pos": len(s["raw_calls"]), "seq": seq,
                                        "key": (b.get("tool_use_id"), i),
                                        "tool_use_id": b.get("tool_use_id"),
                                        "tokens": image_tokens(ib.get("source"))})
    if not s["is_main"]:
        if s["first_prompt"] is None and not o.get("isMeta") and not has_tool_result(content):
            text = content_text(content).strip()
            if text:
                s["first_prompt"] = text
        return
    text = human_text(o)
    if text is not None:
        s["humans"].append((parse_ts(o.get("timestamp")), text))


def research_bash(cmd):
    if RESEARCH_BASH_KEYWORDS.search(cmd):
        return True
    hosts = [h for h in URL_HOST.findall(cmd) if not LOCAL_HOST.match(h)]
    if hosts and not PACKAGE_TOOL.search(cmd):
        return True
    return bool(SAVED_PAGE.search(cmd))


def work_type(c):
    names = [n for n, _cmd in c["tools"].values()]
    if not names:
        return "think"
    if any(n in SPAWN_TOOLS for n in names):
        return "spawn"
    if any(n in EDIT_TOOLS for n in names):
        return "edit"
    if any(n.startswith(UI_PREFIXES) for n in names):
        return "ui"
    if any(n in WEB_TOOLS or n.startswith(WEB_PREFIXES) for n in names):
        return "web"
    if any(research_bash(cmd) for n, cmd in c["tools"].values() if n == "Bash"):
        return "web"
    if "Bash" in names:
        return "run"
    if any(n in READ_TOOLS for n in names):
        return "read"
    return "other"


def screenshot_source(tool_name):
    for prefix, key in SCREENSHOT_SOURCES:
        if tool_name and (tool_name == prefix or tool_name.startswith(prefix) and prefix.endswith("__")):
            return key
    return "other"


# ---------------------------------------------------------------------------------------
# Session lookup

def main_transcripts():
    out = []
    try:
        projects = os.listdir(PROJECTS)
    except OSError:
        return out
    for proj in projects:
        d = os.path.join(PROJECTS, proj)
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if n.endswith(".jsonl"):
                out.append(os.path.join(d, n))
    return out


def find_session_file(sid):
    """Main transcript paths for a session id or unique prefix (>= 6 characters). All
    paths share one id when the id is exact or the prefix is unique; several ids otherwise."""
    exact = [p for p in main_transcripts() if os.path.basename(p) == sid + ".jsonl"]
    if exact:
        return exact
    if len(sid) >= 6:
        return [p for p in main_transcripts() if os.path.basename(p).startswith(sid)]
    return []


def session_id(path):
    return os.path.basename(path)[:-len(".jsonl")]


def read_title(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    for tag, field in ((b'"type":"custom-title"', "customTitle"), (b'"type":"ai-title"', "aiTitle")):
        i = data.rfind(tag)
        if i < 0:
            continue
        start = data.rfind(b"\n", 0, i) + 1
        end = data.find(b"\n", i)
        try:
            o = json.loads(data[start:end if end >= 0 else len(data)])
        except ValueError:
            continue
        title = o.get(field)
        if isinstance(title, str) and title.strip():
            return title.strip()
    return None


def read_cwd(path):
    try:
        with open(path, "rb") as f:
            head = f.read(262144)
    except OSError:
        return None
    m = re.search(rb'"cwd":"((?:[^"\\]|\\.)*)"', head)
    if not m:
        return None
    try:
        return json.loads(b'"' + m.group(1) + b'"')
    except ValueError:
        return None


def project_label(path):
    cwd = read_cwd(path)
    if cwd:
        return "~" + cwd[len(HOME):] if cwd.startswith(HOME) else cwd
    return os.path.basename(os.path.dirname(path))


def search_title(query):
    """Strong matches, newest first: every word is a substring of the title, or (CJK query)
    character-bigram Dice >= DICE_STRONG. Empty when nothing matches strongly."""
    q = query.lower().strip()
    words = q.split()
    cands = []
    for p in main_transcripts():
        title = read_title(p)
        if title:
            cands.append((os.path.getmtime(p), p, title))
    cjk = any(is_cjk(ch) for ch in q)
    strong = [c for c in cands if words and all(w in c[2].lower() for w in words)
              or cjk and bigram_dice(q, c[2].lower()) >= DICE_STRONG]
    return sorted(strong, reverse=True)


def title_tokens(text):
    """Latin words (length >= 3) and CJK character bigrams of a text, lowercased."""
    t = text.lower()
    words = {w for w in re.findall(r"[a-z0-9]+", t) if len(w) >= 3}
    t = "".join(t.split())
    bigrams = {t[i:i + 2] for i in range(len(t) - 1) if is_cjk(t[i]) and is_cjk(t[i + 1])}
    return words | bigrams


def candidate_sessions(query):
    """Titled sessions sharing a word or CJK bigram with the query (most shared first,
    then newest), followed by the most recent titled sessions; deduped and capped."""
    q = title_tokens(query)
    titled = []
    for p in main_transcripts():
        title = read_title(p)
        if title:
            titled.append((os.path.getmtime(p), p, title))
    overlap = [(len(q & title_tokens(c[2])), c) for c in titled]
    ranked = [c for n, c in sorted(overlap, key=lambda x: (-x[0], -x[1][0])) if n > 0]
    recent = sorted(titled, reverse=True)[:CANDIDATE_RECENT]
    out, seen = [], set()
    for c in ranked + recent:
        if c[1] not in seen:
            seen.add(c[1])
            out.append(c)
    return out[:CANDIDATE_MAX]


def print_sessions(first_line, rows):
    print(first_line)
    for mtime, p, title in rows:
        last = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        print(f"{os.path.basename(p)[:-6]} | {one_line(title).replace('|', '/')} | {project_label(p)} | {last}")


def bigram_dice(a, b):
    """Dice coefficient of character bigrams, whitespace removed."""
    a, b = "".join(a.split()), "".join(b.split())
    ba = {a[i:i + 2] for i in range(len(a) - 1)}
    bb = {b[i:i + 2] for i in range(len(b) - 1)}
    return 2 * len(ba & bb) / (len(ba) + len(bb)) if ba and bb else 0.0


# ---------------------------------------------------------------------------------------
# Session analysis

def session_files(main_paths, main_stream):
    sids = set(main_stream["sids"]) | {os.path.basename(p)[:-6] for p in main_paths}
    files = []
    for sid in sorted(sids):
        for proj in (os.listdir(PROJECTS) if os.path.isdir(PROJECTS) else []):
            root = os.path.join(PROJECTS, proj, sid, "subagents")
            if not os.path.isdir(root):
                continue
            for dirpath, _dirs, names in os.walk(root):
                for n in sorted(names):
                    if n.startswith("agent-") and n.endswith(".jsonl"):
                        files.append(os.path.join(dirpath, n))
    return files


def write_mult_of(c):
    """Write multiplier of one call, from its own 5m/1h split (1h when it wrote nothing)."""
    _base, m5, m1, _mr, _mo = pricing(c["model"])
    _i, w5m, w1h, _r, _o = usage_parts(c["usage"])
    return (w5m * m5 + w1h * m1) / (w5m + w1h) if w5m + w1h else m1


def unit_ratio(c):
    """Units per input-equivalent token of one call: price ratio x fast-mode factor."""
    return pricing(c["model"])[0] / REFERENCE_INPUT_PRICE * speed_factor(c["model"], c["usage"])


def cold_waste(cold, typical_write):
    """Units lost by a cold call: the cache it wrote beyond a typical call of its stream
    (median cache write of non-cold calls), priced as write minus warm read."""
    _i, w5m, w1h, _r, _o = usage_parts(cold["usage"])
    rebuilt = max(0.0, w5m + w1h - typical_write)
    mr = pricing(cold["model"])[3]
    return rebuilt * (write_mult_of(cold) - mr) * unit_ratio(cold)


def by_mtime(paths):
    return sorted(paths, key=lambda p: (os.path.getmtime(p), p))


def load_session(main_paths):
    """Parse a session: its main transcript(s) (one session id can sit in several project
    folders) as one main stream, and every subagent file of its resume chain, one merged
    stream per agent id. Files are read oldest first; message ids dedupe across all."""
    registry, tool_names = {}, {}
    main = None
    for p in by_mtime(main_paths):
        main = parse_stream(p, True, registry, tool_names, main)
    files = {}
    for p in session_files(main_paths, main):
        files.setdefault(os.path.basename(p)[len("agent-"):-len(".jsonl")], []).append(p)
    agents = {}
    for aid, paths in files.items():
        paths = by_mtime(paths)
        parts = paths[0].split(os.sep)
        wf = parts[parts.index("workflows") + 1] if "workflows" in parts else None
        meta = {}
        for p in paths:
            meta = read_json(p[:-len(".jsonl")] + ".meta.json", {}) or meta
        st = None
        for p in paths:
            st = parse_stream(p, False, registry, tool_names, st)
        agents[aid] = {"meta": meta, "wf": wf, "streams": [st]}
    for st in [main] + [st for a in agents.values() for st in a["streams"]]:
        finalize_stream(st)
    return main, agents, tool_names


def screenshot_costs(streams, tool_names):
    """Screenshot cost per (source, model family): images and units.

    An image rides in the context of every later call of its stream until the first compact
    after it. The first carrying call pays the cache write multiplier. A later call pays the
    cache read multiplier when its cache read covers the image (cache_read >= prefix before
    the image + image tokens), else the write multiplier. Image cost charged to one call
    never exceeds that call's units. Images are deduped per stream."""
    shots = {}
    for s in streams:
        cs = s["calls"]
        if not s["images"]:
            continue
        comp_seq = [seq for _pos, seq in s["compacts"]]
        comp_pos = [pos for pos, _seq in s["compacts"]]
        charged = [{} for _ in cs]  # per call: source -> units
        for img in s["images"]:
            src = screenshot_source(tool_names.get(img["tool_use_id"]))
            p = img["pos"]
            j = bisect.bisect_right(comp_seq, img["seq"])
            end = min(comp_pos[j], len(cs)) if j < len(comp_pos) else len(cs)
            fam = cs[min(p, len(cs) - 1)]["fam"] if cs else "Other"
            shots.setdefault((src, fam), {"n": 0, "units": 0.0})["n"] += 1
            tokens, reach = img["tokens"], img["offset"] + img["tokens"]
            for i in range(p, end):
                c = cs[i]
                if i > p and usage_parts(c["usage"])[3] >= reach:
                    mult = pricing(c["model"])[3]
                else:
                    mult = write_mult_of(c)
                charged[i][src] = charged[i].get(src, 0.0) + tokens * mult * unit_ratio(c)
        for c, parts in zip(cs, charged):
            total = sum(parts.values())
            if total <= 0:
                continue
            scale = min(1.0, c["units"] / total)
            for src, u in parts.items():
                shots.setdefault((src, c["fam"]), {"n": 0, "units": 0.0})["units"] += u * scale
    return shots


def analyze(main_paths):
    if isinstance(main_paths, str):
        main_paths = [main_paths]
    main, agents, tool_names = load_session(main_paths)
    agent_streams = [st for a in agents.values() for st in a["streams"]]
    streams = [main] + agent_streams
    calls = [c for s in streams for c in s["calls"]]
    total = sum(c["units"] for c in calls)
    # 1 unit = 1 Sonnet 5 input token, so units x its $/MTok gives the API list price in USD.
    cost_usd = total * REFERENCE_INPUT_PRICE / 1e6

    # Model groups and work-type rows.
    fam_units, fam_models, rows = {}, {}, {}
    for c in calls:
        c["wt"] = work_type(c)
        fam_units[c["fam"]] = fam_units.get(c["fam"], 0.0) + c["units"]
        if c["units"] > 0:
            fam_models.setdefault(c["fam"], set()).add(c["model"])
        r = rows.setdefault((c["fam"], c["wt"], c["is_main"]), [0.0, 0.0])
        r[0] += c["units"]
        r[1] += c["ie"]

    # Tasks: a first-level agent plus its descendants; one workflow run is one task.
    def task_key(aid):
        seen = set()
        while True:
            a = agents[aid]
            if a["wf"]:
                return "wf:" + a["wf"]
            parent = a["meta"].get("parentAgentId")
            if not parent or parent not in agents or parent in seen:
                return aid
            seen.add(aid)
            aid = parent

    tasks = {}
    for aid, a in agents.items():
        k = task_key(aid)
        t = tasks.setdefault(k, {"units": 0.0, "fams": {}, "agents": []})
        t["agents"].append(aid)
        for st in a["streams"]:
            for c in st["calls"]:
                t["units"] += c["units"]
                t["fams"][c["fam"]] = t["fams"].get(c["fam"], 0.0) + c["units"]

    # Cold starts. An event is HUMAN writing >1h after the main session's last call.
    # Waste also includes agents resumed after >1h idle.
    cold = {"events": 0, "fam": {}}
    cold_calls = []  # (stream, call)
    mcalls = [c for c in main["calls"] if c["ts"] is not None and c["is_main"]]
    mts = [c["ts"] for c in mcalls]
    cold_ids = set()
    for ts, _text in main["humans"]:
        if ts is None:
            continue
        i = bisect.bisect_left(mts, ts)
        if i == 0 or i >= len(mcalls) or ts - mts[i - 1] <= COLD_GAP_S or mcalls[i]["id"] in cold_ids:
            continue
        cold_ids.add(mcalls[i]["id"])
        cold_calls.append((main, mcalls[i]))
        cold["events"] += 1
    for st in agent_streams:
        acalls = [c for c in st["calls"] if c["ts"] is not None]
        for prev, cur in zip(acalls, acalls[1:]):
            if cur["ts"] - prev["ts"] > COLD_GAP_S and cur["id"] not in cold_ids:
                cold_ids.add(cur["id"])
                cold_calls.append((st, cur))
    typical = {}
    for st, c in cold_calls:
        if st["path"] not in typical:
            writes = [sum(usage_parts(x["usage"])[1:3]) for x in st["calls"] if x["id"] not in cold_ids]
            typical[st["path"]] = statistics.median(writes) if writes else 0.0
        cold["fam"][c["fam"]] = cold["fam"].get(c["fam"], 0.0) + cold_waste(c, typical[st["path"]])

    shots = screenshot_costs(streams, tool_names)

    # Context of the last main-session call.
    context = None
    main_calls = [c for c in main["calls"] if c["is_main"]]
    if main_calls:
        last = main_calls[-1]
        inp, w5m, w1h, read, _out = usage_parts(last["usage"])
        used_tokens = inp + w5m + w1h + read
        win = context_window(last["model"])
        if win and used_tokens > win:
            win = None
        context = {"used": used_tokens, "window": win, "model": last["model"]}

    return {"main": main, "agents": agents, "calls": calls, "total": total, "cost_usd": cost_usd,
            "fam_units": fam_units, "fam_models": fam_models, "rows": rows, "tasks": tasks, "cold": cold,
            "shots": shots, "context": context,
            "bad": sum(st["bad"] for st in streams)}


def session_lang(main):
    """zh when >= 20% of the letters HUMAN typed are CJK, or when at least half of HUMAN's
    messages contain a CJK character (pasted logs can outweigh short Chinese questions). Pasted blocks,
    fenced code, and code-like lines without CJK are left out: they are not HUMAN's words."""
    cjk = alnum = msgs = cjk_msgs = 0
    for _ts, text in main["humans"]:
        t = TAG.sub(" ", WRAPPER_BLOCK.sub(" ", text))
        msg_cjk = 0
        for line in t.split("\n"):
            n_cjk = sum(1 for ch in line if is_cjk(ch))
            if not n_cjk and CODE_LINE.search(line):
                continue
            msg_cjk += n_cjk
            alnum += sum(1 for ch in line if ch.isalnum())
        cjk += msg_cjk
        msgs += 1
        cjk_msgs += msg_cjk >= 1
    if alnum and cjk / alnum >= 0.2 or msgs and cjk_msgs / msgs >= 0.5:
        return "zh"
    return "en"


# ---------------------------------------------------------------------------------------
# Rendering

def bar_text(units, coef, L, approx=False):
    """A bar's share as a percentage, or the not-calibrated label when coef is None."""
    if coef is None:
        return L["uncalibrated"]
    return ("≈ " if approx else "") + fmt_pct(units / coef)


def cell_pct(units, coef):
    """A table cell's Week·All percentage, or "-" when Week·All is not calibrated."""
    return fmt_pct(units / coef) if coef is not None else "-"


def render(a, lang, coefs):
    L = LABELS[lang]
    c_all, c_fable, c_5h = coefs["week_all"], coefs["week_fable"], coefs["5h"]
    total = a["total"] or 1e-9
    fable_units = a["fam_units"].get("Fable", 0.0)
    # Week·Fable shows only for a session with Fable calls, and only while the samples
    # have a Week·Fable bar (or no samples exist yet); see effective_coefficients().
    show_fable = "Fable" in a["fam_units"] and coefs["fable_bar"]
    shown = ("5h", "week_all") + (("week_fable",) if show_fable else ())
    out = [L["title"], ""]
    if coefs.get("quota_failed"):
        out.append(L["quota_failed"])
    else:
        # Every bar: the whole session's units ÷ the bar's units per 1% (Week·Fable: Fable
        # units only). A session that spans several 5h windows can pass 100% on 5h.
        out += [f"- 5h{L['colon']}{bar_text(a['total'], c_5h, L, True)}",
                f"- {L['week_all']}{L['colon']}{bar_text(a['total'], c_all, L, True)}"]
        if show_fable:
            out.append(f"- {L['week_fable']}{L['colon']}{bar_text(fable_units, c_fable, L, True)}")
    out += [f"- {L['api_cost']}{L['colon']}{fmt_usd(a['cost_usd'])}", ""]

    for fam in family_order(a["fam_units"], a["fam_units"]):
        fu = a["fam_units"][fam]
        parts = [f"{L['week_all']} {bar_text(fu, c_all, L)}"]
        if fam == "Fable" and show_fable:
            parts.append(f"{L['week_fable']} {bar_text(fu, c_fable, L)}")
        parts.append(f"{L['share']} {fmt_pct(fu / total * 100)}")
        name = family_title(fam, a["fam_models"].get(fam, ()))
        title = f"**{name}**{L['paren'][0]}{L['sep'].join(parts)}{L['paren'][1]}"
        out += [title, "", L["work_head"], "|---|--:|--:|--:|:-:|"]
        rows = [(k, v) for k, v in a["rows"].items() if k[0] == fam and v[0] > 0]
        rows.sort(key=lambda kv: -kv[1][0])
        for (f, wt, is_main), (units, ie) in rows:
            out.append(f"| {cell(L['work'][wt])} | {fmt_tokens(ie)} | {cell_pct(units, c_all)} | "
                       f"{fmt_pct(units / total * 100)} | {'✓' if is_main else '-'} |")
        out.append("")

    ranked = sorted(a["tasks"].items(), key=lambda kv: -kv[1]["units"])
    ranked = [kv for kv in ranked if kv[1]["units"] > 0]
    top = []
    if ranked:
        n = 3 if any(t["units"] / total * 100 > TOP_SHARE_THRESHOLD for _k, t in ranked) else 10
        top = ranked[:n]
        out += [L["top_title"], "", L["top_head"], "|--:|---|--:|--:|---|"]
        for i, (_k, t) in enumerate(top, 1):
            models = "+".join(f for f in family_order(t["fams"], t["fams"]) if t["fams"][f] > 0) or "-"
            out.append(f"| {i} | {cell(models)} | {cell_pct(t['units'], c_all)} | "
                       f"{fmt_pct(t['units'] / total * 100)} | {{{{TASK_{i}}}}} |")
        out.append("")

    cold = a["cold"]
    cold_rows = [(f, u) for f, u in cold["fam"].items() if u > 0]
    if cold_rows:
        times = "time" if cold["events"] == 1 else "times"
        out += [L["cold_title"].format(n=cold["events"], times=times), "", L["cold_head"],
                "|---|--:|--:|"]
        for f, u in sorted(cold_rows, key=lambda x: -x[1]):
            out.append(f"| {f} | {cell_pct(u, c_all)} | {fmt_pct(u / total * 100)} |")
        out.append("")

    shot_units = sum(s["units"] for s in a["shots"].values())
    shot_n = sum(s["n"] for s in a["shots"].values())
    if shot_units / total * 100 >= SCREENSHOT_MIN_SHARE:
        out += [L["shot_title"].format(n=shot_n, share=fmt_pct(shot_units / total * 100)), "",
                L["shot_head"], "|---|---|--:|--:|--:|"]
        for (src, fam), s in sorted(a["shots"].items(), key=lambda kv: -kv[1]["units"]):
            if s["n"] or s["units"] > 0:
                out.append(f"| {cell(L['source'][src])} | {fam} | {s['n']} | "
                           f"{cell_pct(s['units'], c_all)} | {fmt_pct(s['units'] / total * 100)} |")
        out.append("")

    ctx = a["context"]
    if ctx:
        if ctx["window"]:
            out.append(L["context"].format(used=fmt_tokens(ctx["used"]), window=fmt_window(ctx["window"]),
                                           pct=fmt_pct(ctx["used"] / ctx["window"] * 100)))
        else:
            out.append(L["context_nowin"].format(used=fmt_tokens(ctx["used"])))
        out.append("")

    footer = L["footer"].format(n=coefs["samples"], samples="sample" if coefs["samples"] == 1 else "samples")
    if any(coefs[bar] is None for bar in shown):
        footer += L["footer_uncalibrated"]
    footer += L["footer_cost"]
    out.append(footer)
    if a.get("bad"):
        out += ["", L["malformed"].format(n=a["bad"])]
    if price_table()["override_error"]:
        out += ["", L["override_invalid"].format(why=one_line(price_table()["override_error"]))]
    for m in sorted({c["model"] for c in a["calls"] if c["model"]}):
        priced, known = resolve_model(m)
        if not known:
            out += ["", L["unknown_model"].format(id=m, used=priced)]

    ctx_lines = ["TASK CONTEXT"]
    for i, (k, t) in enumerate(top, 1):
        ctx_lines.append(f"TASK_{i} | {task_description(a, k, t, L)} | {task_prompt(a, t)}")
    return out, (ctx_lines if top else [])


def task_description(a, key, t, L):
    if key.startswith("wf:"):
        return L["workflow"].format(name=key[3:], n=len(t["agents"]))
    meta = a["agents"][key]["meta"]
    return one_line(meta.get("description") or meta.get("agentType") or key).replace("|", "/")


def task_prompt(a, t):
    streams = [st for aid in t["agents"] for st in a["agents"][aid]["streams"]]
    streams.sort(key=lambda s: min([c["ts"] for c in s["calls"] if c["ts"]] or [float("inf")]))
    for s in streams:
        if s["first_prompt"]:
            return one_line(s["first_prompt"])[:300].replace("|", "/")
    return ""


# ---------------------------------------------------------------------------------------
# /usage samples and calibration

@contextlib.contextmanager
def data_lock(timeout=60):
    os.makedirs(DATA_DIR, exist_ok=True)
    fd = os.open(data_path(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.time() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    raise TimeoutError("data dir lock busy")
                time.sleep(0.2)
        yield
    finally:
        os.close(fd)


USAGE_LINES = {
    "5h": re.compile(r"^\s*Current session:\s*(\d+(?:\.\d+)?)%\s*used(?:\s*·\s*resets\s+(.+?))?\s*$", re.M),
    "week_all": re.compile(r"^\s*Current week \(all models\):\s*(\d+(?:\.\d+)?)%\s*used"
                           r"(?:\s*·\s*resets\s+(.+?))?\s*$", re.M),
    "week_fable": re.compile(r"^\s*Current week \(Fable\):\s*(\d+(?:\.\d+)?)%\s*used"
                             r"(?:\s*·\s*resets\s+(.+?))?\s*$", re.M),
}
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
RESET_RE = re.compile(
    r"(?:(?P<mon>[A-Z][a-z]{2})\w*\s+(?P<day>\d{1,2})(?:,\s*(?P<year>\d{4}))?\s+at\s+)?"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)(?:\s*\((?P<tz>[^)]+)\))?", re.I)


def parse_reset(s, now):
    """Epoch seconds of a reset string like 'Sep 27 at 1:09am (Australia/Melbourne)'."""
    if not s:
        return None
    m = RESET_RE.search(s)
    if not m:
        return None
    tz = None
    if m.group("tz") and ZoneInfo is not None:
        try:
            tz = ZoneInfo(m.group("tz"))
        except Exception:  # unknown zone name
            tz = None
    ref = datetime.fromtimestamp(now, tz) if tz else datetime.fromtimestamp(now).astimezone()
    hour = int(m.group("h")) % 12 + (12 if m.group("ap").lower() == "pm" else 0)
    minute = int(m.group("m") or 0)
    if m.group("mon"):
        mon = MONTHS.get(m.group("mon")[:3].title())
        if not mon:
            return None
        day = int(m.group("day"))
        years = [int(m.group("year"))] if m.group("year") else [ref.year - 1, ref.year, ref.year + 1]
        best = None
        for y in years:
            try:
                d = ref.replace(year=y, month=mon, day=day, hour=hour, minute=minute, second=0, microsecond=0)
            except ValueError:
                continue
            if best is None or abs(d.timestamp() - now) < abs(best.timestamp() - now):
                best = d
        return best.timestamp() if best else None
    d = ref.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if d.timestamp() < now - 600:  # up to 10 minutes in the past stays today
        d += timedelta(days=1)
    return d.timestamp()


def parse_usage(text, now):
    out = {}
    for bar, rx in USAGE_LINES.items():
        m = rx.search(text)
        if m:
            reset = (m.group(2) or "").strip()
            out[bar] = {"pct": float(m.group(1)), "reset": reset, "reset_ts": parse_reset(reset, now)}
    if "5h" not in out or "week_all" not in out:
        return None
    return out


def walk_transcripts():
    for dirpath, _dirs, names in os.walk(PROJECTS):
        for n in names:
            if n.endswith(".jsonl"):
                yield os.path.join(dirpath, n)


def raw_vector(u):
    """[input, 5m write, 1h write, cache read, output, web search requests] of one usage."""
    stu = u.get("server_tool_use")
    ws = (stu.get("web_search_requests") or 0) if isinstance(stu, dict) else 0
    return list(usage_parts(u)) + [ws]


def raw_units(model, rec):
    """Weighted units of raw cumulative counts {"std": [5], "fast": [5], "ws": n} for one
    model id, with the current merged prices."""
    base, m5, m1, mr, mo = pricing(model)
    fast = price_table()["fast"].get(resolve_model(model)[0], 1.0)

    def ie(v):
        return v[0] + v[1] * m5 + v[2] * m1 + v[3] * mr + v[4] * mo

    zero = [0, 0, 0, 0, 0]
    ratio = base / REFERENCE_INPUT_PRICE
    return (ie(rec.get("std") or zero) * ratio + ie(rec.get("fast") or zero) * ratio * fast
            + (rec.get("ws") or 0) * price_table()["search_usd"] * 1e6 / REFERENCE_INPUT_PRICE)


def span_hash(path, start, end):
    with open(path, "rb") as f:
        f.seek(start)
        return hashlib.sha1(f.read(max(0, end - start))).hexdigest()


def file_record(p, info, off):
    return {"ino": info.st_ino, "size": info.st_size, "mtime": info.st_mtime, "off": off,
            "head_len": min(HASH_BYTES, off), "head": span_hash(p, 0, min(HASH_BYTES, off)),
            "tail": span_hash(p, max(0, off - HASH_BYTES), off)}


def update_state(now):
    """Bring raw cumulative counts per model id up to date with all local transcripts.

    Per file the state keeps inode, size, mtime, read offset, and hashes of the first and
    last 4 KB read. A plain append is read from the offset; any other change (new inode,
    shrink, same size with a new mtime, changed head or tail) reads the file again from the
    start. Per message id it keeps the last usage snapshot for 8 days with its (model,
    speed) key; an update takes the old snapshot off its key and adds the new one, so
    streaming lines, copies in resumed transcripts, and full rescans count once."""
    path = data_path(STATE_FILE)
    st = read_json(path)
    if not isinstance(st, dict) or st.get("v") != STATE_VERSION:
        st = {"v": STATE_VERSION, "state_id": uuid.uuid4().hex, "created": now,
              "files": {}, "seen": {}, "cum": {}}
    files, seen, cum = st["files"], st["seen"], st["cum"]
    cutoff = now - SEEN_DAYS * 86400
    present = set()
    for p in walk_transcripts():
        present.add(p)
        try:
            info = os.stat(p)
        except OSError:
            continue
        rec = files.get(p)
        if rec is None and info.st_mtime < cutoff:
            files[p] = file_record(p, info, info.st_size)
            continue
        if rec and (rec["ino"], rec["size"], rec["mtime"]) == (info.st_ino, info.st_size, info.st_mtime):
            continue
        off = 0
        if (rec and rec["ino"] == info.st_ino and info.st_size > rec["size"]
                and span_hash(p, 0, rec["head_len"]) == rec["head"]
                and span_hash(p, max(0, rec["off"] - HASH_BYTES), rec["off"]) == rec["tail"]):
            off = rec["off"]
        with open(p, "rb") as f:
            f.seek(off)
            data = f.read(info.st_size - off)
        end = data.rfind(b"\n")
        if end >= 0:
            for raw in data[:end].split(b"\n"):
                if b'"usage"' in raw:
                    count_line(raw, seen, cum, cutoff)
            off += end + 1
        files[p] = file_record(p, info, off)
    for p in list(files):
        if p not in present:
            del files[p]
    for k in [k for k, v in seen.items() if v[0] < cutoff]:
        del seen[k]
    write_json(path, st)
    return st


def add_snapshot(cum, model, bucket, v, sign):
    rec = cum.setdefault(model, {"std": [0, 0, 0, 0, 0], "fast": [0, 0, 0, 0, 0], "ws": 0})
    part = rec[bucket]
    for i in range(5):
        part[i] += sign * v[i]
    rec["ws"] += sign * v[5]


def count_line(raw, seen, cum, cutoff):
    """Count one transcript line. seen[id] = [ts, model, bucket, 6 raw counts]."""
    try:
        o = json.loads(raw)
    except ValueError:
        return
    if not isinstance(o, dict) or o.get("type") != "assistant":
        return
    m = o.get("message") or {}
    model = m.get("model") or ""
    key = m.get("id") or o.get("requestId")
    u = m.get("usage")
    if model == "<synthetic>" or not key or not valid_usage(u):
        return
    v = raw_vector(u)
    bucket = "fast" if u.get("speed") == "fast" else "std"
    old = seen.get(key)
    if old is None:
        ts = parse_ts(o.get("timestamp"))
        if ts is None or ts < cutoff:
            return
    else:
        ts, old_model, old_bucket, old_v = old[0], old[1], old[2], old[3:]
        if (old_model, old_bucket, old_v) == (model, bucket, v) or sum(v) < sum(old_v):
            return  # same snapshot, or an older smaller copy
        add_snapshot(cum, old_model, old_bucket, old_v, -1)
    add_snapshot(cum, model, bucket, v, +1)
    seen[key] = [int(ts), model, bucket] + v


def load_samples():
    """Samples in the current format (raw counts); older ones are ignored."""
    out = []
    try:
        with open(data_path(SAMPLES_FILE)) as f:
            for line in f:
                try:
                    s = json.loads(line)
                except ValueError:
                    continue
                if isinstance(s, dict) and s.get("v") == SAMPLE_VERSION:
                    out.append(s)
    except OSError:
        pass
    return out


def save_samples(samples):
    path = data_path(SAMPLES_FILE)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(tmp, path)


def bar_units(sample, bar):
    """Weighted units of a sample's raw counts for one bar (Week·Fable: Fable models only)."""
    cum = sample.get("cum") or {}
    return sum(raw_units(m, rec) for m, rec in cum.items()
               if bar != "week_fable" or family(m) == "Fable")


def bar_intervals(samples, bar):
    """Units per 1% over disjoint sample pairs inside one reset window."""
    ratios, anchor = [], None
    for s in sorted(samples, key=lambda x: x["t"]):
        b = (s.get("bars") or {}).get(bar)
        if not b:
            continue
        if anchor is not None:
            ab = anchor["bars"][bar]
            same = (s.get("state_id") == anchor.get("state_id")
                    and b.get("reset_ts") is not None and ab.get("reset_ts") is not None
                    and abs(b["reset_ts"] - ab["reset_ts"]) <= SAME_WINDOW_S
                    and b["pct"] >= ab["pct"])
            if not same:
                anchor = s
                continue
            rise = b["pct"] - ab["pct"]
            if rise >= MIN_RISE_PCT[bar]:
                du = bar_units(s, bar) - bar_units(anchor, bar)
                if du > 0:
                    ratios.append(du / rise)
                anchor = s
            continue
        anchor = s
    return ratios


def compute_coefficients(samples, previous):
    """Coefficient per bar: 75th percentile of units per 1% with >= 2 intervals, the single
    interval with 1, else the last calibrated value, else None (not calibrated)."""
    coefs = {}
    for bar in BARS:
        ratios = bar_intervals(samples, bar)
        prev = (previous or {}).get(bar) or {}
        if len(ratios) >= MIN_INTERVALS_FOR_P75:
            value, source = statistics.quantiles(ratios, n=4, method="inclusive")[2], "p75"
        elif ratios:
            value, source = ratios[0], "single"
        elif prev.get("source") in ("p75", "single", "max", "saved"):
            value, source = prev["value"], "saved"
        else:
            value, source = None, "uncalibrated"
        coefs[bar] = {"value": value, "source": source, "intervals": len(ratios)}
    return coefs


def calibrated_coefficients():
    """Coefficients from the raw samples, weighted with the current merged prices. Cached
    in coefficients.json under a key of the merged-price hash and the samples file mtime; a
    saved value is reused only when it was computed with the same merged prices."""
    price_hash = price_table()["hash"]
    try:
        mtime = os.path.getmtime(data_path(SAMPLES_FILE))
    except OSError:
        mtime = 0
    key = f"{price_hash}:{mtime}"
    cache = read_json(data_path(COEF_FILE), {}) or {}
    if cache.get("key") == key and isinstance(cache.get("coefs"), dict):
        return cache["coefs"]
    previous = cache.get("coefs") if cache.get("pricing") == price_hash else {}
    coefs = compute_coefficients(load_samples(), previous)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        write_json(data_path(COEF_FILE), {"key": key, "pricing": price_hash, "coefs": coefs})
    except OSError as e:
        log_error(f"cannot write {COEF_FILE}: {e}")
    return coefs


def effective_coefficients():
    """Coefficient per bar in weighted units per 1%, None when the bar is not calibrated.
    fable_bar is True when a stored sample has a Week·Fable bar, or when no sample is
    stored yet; it is False when samples exist and none has that bar (the plan shows no
    Week·Fable bar). Other weekly model bars in /usage are not parsed."""
    saved = calibrated_coefficients()
    samples = load_samples()
    attempt = read_json(data_path(ATTEMPT_FILE), {}) or {}
    out = {"samples": len(samples), "quota_failed": attempt.get("ok") is False}
    sources = {}
    for bar in BARS:
        rec = saved.get(bar) or {}
        out[bar] = rec.get("value") or None
        sources[bar] = rec.get("source", "uncalibrated") if rec.get("value") else "uncalibrated"
    out["sources"] = sources
    out["fable_bar"] = not samples or any("week_fable" in (s.get("bars") or {}) for s in samples)
    return out


def record_failure(reason):
    """Record a failed /usage attempt, so the report shows the quota-read-failed label."""
    log_error("sampling failed: " + one_line(reason)[:300])
    with data_lock():
        write_json(data_path(ATTEMPT_FILE), {"t": time.time(), "ok": False, "error": one_line(reason)[:300]})


def record_sample(path):
    try:
        with open(path, errors="replace") as f:
            text = f.read()
    except OSError as e:
        text = ""
        log_error(f"cannot read /usage output {path}: {e}")
    record_sample_text(text)


def record_sample_text(text):
    with data_lock():
        now = time.time()  # after the lock: samples stay in time order
        st = update_state(now)
        bars = parse_usage(text, now)
        if bars is None:
            log_error("cannot parse /usage output: " + one_line(text)[:200])
            write_json(data_path(ATTEMPT_FILE), {"t": now, "ok": False, "error": "cannot parse /usage output"})
            return
        samples = load_samples()
        samples.append({"v": SAMPLE_VERSION, "t": now, "state_id": st["state_id"],
                        "cum": json.loads(json.dumps(st["cum"])), "bars": bars})
        samples = [s for s in samples if s.get("t", 0) >= now - SAMPLE_KEEP_DAYS * 86400]
        save_samples(samples)
        write_json(data_path(ATTEMPT_FILE), {"t": now, "ok": True})


def trigger_sample():
    """Start a forced sample in the background (a current-session report takes one)."""
    try:
        subprocess.Popen([sys.executable, os.path.realpath(__file__), "--sample-if-due", "--force"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        log_error(f"cannot start a sample: {e}")


def find_claude():
    override = os.environ.get("CCSB_CLAUDE_BIN")  # tests and smoke runs
    if override:
        return override if os.access(override, os.X_OK) else None
    found = shutil.which("claude")
    if found:
        return found
    for c in (os.path.join(HOME, ".local", "bin", "claude"), os.path.join(HOME, ".claude", "local", "claude"),
              "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if os.access(c, os.X_OK):
            return c
    return None


def sample_if_due(force):
    """One /usage sample, run detached by the Stop hook. An exclusive flock on
    sampler.lock, held for this process's whole life (the kernel frees it if the process
    dies), makes parallel hooks start at most one /usage run. Without force, a sample
    newer than 5 minutes (last-sample.stamp) means nothing to do."""
    os.makedirs(DATA_DIR, exist_ok=True)
    fd = os.open(data_path("sampler.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    stamp = data_path("last-sample.stamp")
    try:
        if not force and time.time() - os.path.getmtime(stamp) < SAMPLE_GAP_S:
            return
    except OSError:
        pass
    with open(stamp, "a"):
        os.utime(stamp, None)
    claude = find_claude()
    if not claude:
        record_failure("claude CLI not found")
        return
    env = dict(os.environ, CCSB_SAMPLING="1")
    try:
        res = subprocess.run([claude, "-p", "/usage", "--no-session-persistence"], env=env,
                             stdin=subprocess.DEVNULL, capture_output=True, text=True,
                             errors="replace", timeout=USAGE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        record_failure(f"claude -p /usage timed out after {USAGE_TIMEOUT_S} s")
        return
    except OSError as e:
        record_failure(f"cannot run claude: {e}")
        return
    if res.returncode != 0:
        record_failure(f"claude -p /usage exited {res.returncode}: {(res.stdout + res.stderr)[:200]}")
        return
    record_sample_text(res.stdout + res.stderr)


# ---------------------------------------------------------------------------------------

def report(main_paths, as_json):
    coefs = effective_coefficients()
    a = analyze(main_paths)
    lang = session_lang(a["main"])
    if as_json:
        print(json.dumps(debug_view(a, lang, coefs, main_paths), ensure_ascii=False, indent=1))
        return
    lines, ctx = render(a, lang, coefs)
    print("REPORT")
    print(f"lang: {lang}")
    due = price_check_due({c["model"] for c in a["calls"]})
    if due:
        print(f"PRICE_CHECK: due ({due})")
    print("\n".join(lines))
    if ctx:
        print()
        print("\n".join(ctx))


def debug_view(a, lang, coefs, main_paths):
    return {
        "session": main_paths, "lang": lang, "coefficients": coefs, "pricing": price_table()["source"],
        "total_units": a["total"], "api_cost_usd": a["cost_usd"], "calls": len(a["calls"]), "agents": len(a["agents"]),
        "fam_units": a["fam_units"],
        "rows": [{"fam": k[0], "work": k[1], "main": k[2], "units": v[0], "ie_tokens": v[1]}
                 for k, v in sorted(a["rows"].items(), key=lambda kv: -kv[1][0])],
        "tasks": {k: {"units": t["units"], "fams": t["fams"], "agents": len(t["agents"])}
                  for k, t in a["tasks"].items()},
        "cold": a["cold"], "context": a["context"],
        "shots": [{"source": k[0], "fam": k[1], **v} for k, v in a["shots"].items()],
        "sids": sorted(a["main"]["sids"]),
    }


def parse_count(text):
    """'200000', '200K', '1.2M' -> tokens."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kKmM]?)\s*", text or "")
    if not m:
        raise ValueError(f"bad token count: {text!r}")
    return int(float(m.group(1)) * {"": 1, "k": 1e3, "m": 1e6}[m.group(2).lower()])


def estimate(main_paths, context, turns):
    """One line: quota cost of the next turns at a given context, and at 20K after /compact.

    Per call at context C: C x cache-read multiplier + median main-session cache-write
    tokens x the session's write multiplier + median output tokens x output multiplier, all
    x the base input price ratio of the main session's latest model. Medians leave out
    cold-start calls (first call after >1h idle). Week·Fable joins the line for a Fable
    session under the same rule as the report. While Week·All is not calibrated, the line
    gives weighted units and USD at API prices in place of Week·All and ends with the
    estimate_uncalibrated label."""
    if isinstance(main_paths, str):
        main_paths = [main_paths]
    registry, s = {}, None
    for p in by_mtime(main_paths):
        s = parse_stream(p, True, registry, {}, s)
    finalize_stream(s)
    calls = [c for c in s["calls"] if c["is_main"]]
    lang = session_lang(s)
    L = LABELS[lang]
    model = calls[-1]["model"] if calls else "claude-sonnet-5"
    base, m5, m1, mr, mo = pricing(model)
    # Typical calls only: drop the first call after >1h idle (a cold cache rebuild).
    warm = [c for i, c in enumerate(calls)
            if not (i and c["ts"] and calls[i - 1]["ts"] and c["ts"] - calls[i - 1]["ts"] > COLD_GAP_S)]
    parts = [usage_parts(c["usage"]) for c in warm]
    w5, w1 = sum(p[1] for p in parts), sum(p[2] for p in parts)
    write_med = statistics.median([p[1] + p[2] for p in parts]) if parts else 0
    out_med = statistics.median([p[4] for p in parts]) if parts else 0
    write_mult = (w5 * m5 + w1 * m1) / (w5 + w1) if w5 + w1 else m1
    humans = len(s["humans"])
    per_turn = len(calls) / humans if humans and calls else DEFAULT_CALLS_PER_TURN
    n_calls = turns * per_turn
    coefs = effective_coefficients()
    c_all, c_fable = coefs["week_all"], coefs["week_fable"]
    show_fable = family(model) == "Fable" and coefs["fable_bar"]
    fast = speed_factor(model, calls[-1]["usage"]) if calls else 1.0

    def cost(ctx):
        per_call = (ctx * mr + write_med * write_mult + out_med * mo) * base / REFERENCE_INPUT_PRICE * fast
        units = per_call * n_calls
        if c_all is None:
            parts = [L["estimate_units"].format(units=fmt_tokens(units),
                                                usd=fmt_usd(units * REFERENCE_INPUT_PRICE / 1e6))]
            if show_fable and c_fable is not None:
                parts.append(f"{L['week_fable']} ≈ {fmt_pct(units / c_fable)}")
        else:
            parts = [f"{L['week_all']} ≈ {fmt_pct(units / c_all)}"]
            if show_fable:
                parts.append(f"{L['week_fable']} {bar_text(units, c_fable, L, True)}")
        return L["sep"].join(parts)

    name = f"{family(model)} {model_version(model)}".strip()
    return L["estimate"].format(turns=turns, calls=int(round(n_calls)), ctx=fmt_tokens(context),
                                model=name, now=cost(context), small=fmt_tokens(COMPACT_CONTEXT),
                                after=cost(COMPACT_CONTEXT)) + (L["estimate_uncalibrated"] if c_all is None else "")


def main():
    ap = argparse.ArgumentParser(prog="ccsb", description="Plan-quota and API-cost report for one Claude Code session.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--title")
    g.add_argument("--session")
    g.add_argument("--record-sample", metavar="FILE")
    g.add_argument("--sample-if-due", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--mark-price-checked", action="store_true")
    ap.add_argument("--estimate", action="store_true")
    ap.add_argument("--context", type=parse_count)
    ap.add_argument("--turns", type=int, default=10)
    args = ap.parse_args()
    for flag, used in (("--mark-price-checked", args.mark_price_checked),
                       ("--sample-if-due", args.sample_if_due),
                       ("--record-sample", args.record_sample)):
        if args.estimate and used:
            ap.error(f"--estimate and {flag} cannot be used together")

    if args.record_sample:
        record_sample(args.record_sample)
        return 0
    if args.sample_if_due:
        sample_if_due(args.force)
        return 0
    if args.mark_price_checked:
        mark_price_checked()
        return 0
    if args.estimate and (args.context is None or args.title):
        ap.error("--estimate needs --context N, and takes --session, not --title")

    current = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    # A report always exits 0: the skill reads its first line. --estimate exits 1 when it
    # prints no estimate, so a caller can rely on the exit code alone.
    no_result = 1 if args.estimate else 0
    if args.title:
        hits, ids = [], set()
        for h in search_title(args.title):  # newest first; one row per session id
            if session_id(h[1]) not in ids:
                ids.add(session_id(h[1]))
                hits.append(h)
        if not hits:
            print_sessions("CANDIDATES", candidate_sessions(args.title))
            return 0
        if len(hits) > 1:
            print_sessions("MULTIPLE", hits[:5])
            return 0
        paths = find_session_file(session_id(hits[0][1]))
    else:
        sid = args.session or current
        if not sid:
            print("ERROR: current session id not found")
            return no_result
        found = find_session_file(sid)
        if not found:
            print(f"ERROR: session {sid} not found")
            return no_result
        if len({session_id(p) for p in found}) > 1:
            newest = {}
            for p in sorted(found, key=os.path.getmtime):
                newest[session_id(p)] = p
            rows = sorted(((os.path.getmtime(p), p, read_title(p) or "-") for p in newest.values()), reverse=True)
            print_sessions("MULTIPLE", rows[:5])
            return no_result
        paths = found
    if args.estimate:
        print(estimate(paths, args.context, args.turns))
        return 0
    is_current = bool(current) and session_id(paths[0]) == current
    if is_current and not args.json:
        trigger_sample()
    report(paths, args.json)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # report every failure in error.log, then fail loudly
        log_error(f"{type(exc).__name__}: {exc} | {traceback.format_exc(limit=3)!r}")
        raise
