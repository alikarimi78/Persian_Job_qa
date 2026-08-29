#!/usr/bin/env python3
"""
translate_script.py — send O*NET xlsx batches to the LLM, get Persian back, write xlsx.

    pip install google-genai pandas openpyxl
    export GEMINI_API_KEY="AIza..."           # your own Google AI Studio key
    python3 translate_script.py               # reads batch_10/, writes batch_10_fa/

Talks to Google's **native** Gemini API via the `google-genai` SDK
(`client.models.generate_content`) rather than the gapgpt.app OpenAI-compatible proxy the
rest of this project uses. Nothing here reports a price any more: the proxy returned a
real `usage.cost` per call and the native API does not, so what is printed is tokens.

Behaviour:
  * reads every .xlsx in --in (each = header + 10 rows), defaults to ./batch_10
  * sends it as CSV text, receives Persian CSV text
  * validates: row count, job_code match, column set, per-cell item counts
    (one lost item per cell is tolerated; two is a re-translate)
  * retries a failed batch up to --retries times, feeding the error back to the model
  * rate-limit / network failures have a budget of their own (--stall-retries) and wait
    exactly as long as the server asks, so they never eat the model's retries
  * rotates over every key it was given: the free tier's budget is per key, not per
    project, so a key that answers 429 is rested and the other one is tried
  * writes <name>_fa.xlsx into --out (default ./batch_10_fa), plus a report.json
  * already-translated batches are skipped, so you can stop and resume
  * prints the tokens each batch spent, retries included, and totals them at the end

**The free tier is the binding constraint, not the script.** A batch is a ~12k-token
request answered with ~15k tokens, and on the free tier those arrive faster than the
quota allows. What comes back is

    429 ... Quota exceeded for metric: generativelanguage.googleapis.com/
    generate_content_free_tier_requests, limit: 20, model: gemini-3.5-flash
    Please retry in 43.7s

and the window does reopen: measured on 2026-08-29, roughly one batch a minute gets
through while the rest wait, and a one-sentence request to the same key and model is
answered immediately in the middle of it. So the answer to a 429 here is patience, not a
smaller batch or a different key — which is why the wait is the server's number and the
stalls have a budget of their own. Expect a full pass over ~30 batches to take half an
hour of mostly waiting, and note that translated batches are kept: a run stopped
half-way is resumed by running it again.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import io
import json
import logging
import os
import random
import re
import sys
import threading
import time
from pathlib import Path

import pandas as pd
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
import httpx

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent

# Progress is printed to stdout, and stdout is a **pipe** whenever this runs under
# `docker run` / `docker exec` rather than in a terminal — which makes it block-buffered.
# The header, every per-batch line and the totals then sit in an 8 KB buffer until the
# process exits, so a run that spends half an hour on the API looks like a hang, and one
# that is interrupted or killed prints nothing at all. Line buffering is what makes the
# container show what a terminal shows. (`-u` / PYTHONUNBUFFERED would do it too; this
# way the script does not depend on being invoked correctly.)
try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):      # a replaced stdout, e.g. under a test harness
    pass

# The SDK logs a paragraph about automatic function calling on *every* generate_content
# call. Nothing here uses tools, so it is pure noise between the lines that matter.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

# Google AI Studio keys — https://aistudio.google.com/apikey
#
# Two names, and both are read: the request budget below is per key, so the deployment's
# second key is a second day's worth of batches rather than a spare nobody uses. Reading
# only GEMINI_API_KEY2 (as this did) also meant that a machine which had set the
# documented name alone exited 2 before the first batch, with the error naming the
# variable it had *not* looked at.
KEY_ENV_NAMES = ("GEMINI_API_KEY5", )
ENV_KEYS = [(name, os.environ[name]) for name in KEY_ENV_NAMES if os.environ.get(name)]

# gemini-3.5-flash: picked because it's currently free-tier. No accuracy comparison
# against flash-lite / 2.5-flash / 3.6-flash has been run on this dataset — if batches
# start failing validate() repeatedly, that's the first thing worth checking.
MODEL = os.environ.get("TRANSLATE_MODEL", "gemini-3.5-flash")

MAX_OUTPUT_TOKENS = 65536   # measured: a 10-row Persian batch is ~14.6k output tokens
# ...which is also the ceiling on --in batch size: at ~1.5k output tokens a row, 44 rows
# fills it and the answer is cut off mid-record. Bigger batches save nothing anyway —
# only the 695-token system prompt is re-sent, worth 2 cents over the whole dataset.

# Translation needs no deliberation, so thinking is disabled by default (thinking_budget
# 0 — confirmed a valid field on GenerateContentConfig.thinking_config in google-genai
# 2.20.0). Set the env var to "" to leave thinking on if quality issues show up.
REASONING_EFFORT = os.environ.get("TRANSLATE_REASONING_EFFORT", "none")

# Set to False to keep `tools` in English: the column is then stripped before the
# request and re-attached from the source file afterwards. Saves ~29% of tokens.
TRANSLATE_TOOLS = True

COLUMNS = ["job_code", "job_title", "aliases", "tools", "skills", "knowledge",
           "abilities", "work_context", "career_path_next", "description",
           "responsibilities"]

# columns whose cells are " | "-separated lists — item counts must survive
LIST_COLUMNS = ["aliases", "tools", "skills", "knowledge", "abilities",
                "work_context", "career_path_next", "responsibilities"]

# How far a cell's item count may drift before the batch is rejected. One is tolerated:
# a single alias dropped out of twelve is not worth re-spending a whole 10-row batch on,
# and the model is still told to keep every item — this only decides what is refused.
# Two is a pattern rather than a slip. A cell that comes back *empty* is never tolerated
# whatever its size: that is a lost cell, not a lost item.
ITEM_TOLERANCE = 1

SYSTEM_PROMPT = """\
I will send a CSV of occupation records. You ONLY translate them into Persian (Farsi).

Core rule — do not change the structure:
- Output row count MUST equal input row count. Never drop or add a row.
- The number of items in every cell MUST equal the input. Never drop, merge or add an item.
- Do not summarise, rewrite, or simplify. Translate fully and faithfully.
- Translate only from the text in that cell. Add nothing from your own knowledge.
- If a phrase is unclear, translate it literally — do not guess or invent a substitute.

Columns (same names, same order, no extra columns):
job_code,job_title,aliases,tools,skills,knowledge,abilities,work_context,career_path_next,description,responsibilities

- job_code: copy verbatim, never translate. It is the join key.
- job_title: Persian translation + English original in parentheses -> مدیران ارشد اجرایی (Chief Executives)
- description: translate in full, drop no sentence.
- All others: translate each item separately.

Rule for the tools column — evaluate each item separately:
1. Proper name -> leave in English, untouched. Product, brand, company, programming
   language, framework, operating system, cloud service.
   Examples: Microsoft Excel · Python · Oracle Database · Git · Ubuntu · Tableau · SAP · React
2. Generic description with no brand -> translate.
   Examples: Database software -> نرم‌افزار پایگاه داده · Email software -> نرم‌افزار ایمیل ·
   Web browser software -> نرم‌افزار مرورگر وب · Payroll software -> نرم‌افزار حقوق و دستمزد ·
   Firewall software -> نرم‌افزار فایروال
   Keep technical acronyms inside them in English:
   Geographic information system GIS software -> نرم‌افزار سیستم اطلاعات جغرافیایی GIS
3. Brand + generic word -> brand stays English, generic word becomes Persian.
   Examples: Microsoft Office software -> نرم‌افزار Microsoft Office · SAP software -> نرم‌افزار SAP
4. Physical tool with a concrete Persian equivalent -> translate.
   Examples: wrench -> آچار · screwdriver -> پیچ‌گوشتی · caliper -> کولیس · multimeter -> مولتی‌متر
When unsure whether an item is a brand or a generic description, leave it in English.

Separator: items inside a cell are separated by " | " (space pipe space).
Preserve it EXACTLY — never convert it to commas or dashes.

Do not translate: numbers, and the acronyms
CEO, CFO, CIO, COO, CTO, EVP, HRIS, SQL, XML, ERP, CRM, KPI, ROI, GIS, GPS, LEED, CFP

Output format: RFC 4180 CSV. Every field wrapped in double quotes, internal quotes
doubled (""). One record per line, no newlines inside cells. Include the header row.
Do not wrap the output in a markdown code block. Write no text before or after the CSV.
"""


# How long a single wait may be. A quota 429 names its own delay and this is the cap on
# believing it: a per-minute window asks for seconds, and anything longer than this is a
# window that will not open again inside a run.
MAX_WAIT = 120.0

# How long every key may go on refusing, with nothing getting through anywhere, before
# the run stops trying. It is a clock and not only a count of refusals because the two
# things a 429 can mean are told apart by *time*: a window that reopens lets some batch
# through every minute or two, a spent day never does. Five minutes of complete silence
# is the latter.
QUOTA_GIVE_UP = 300.0


class TransientError(RuntimeError):
    """Rate limit, timeout or 5xx — retry the same request, do not blame the model.

    `retry_after` is what the server itself asked for and is preferred to any backoff of
    ours; `quota` marks the 429s that are a spent budget rather than a busy moment, which
    is what tells the key ring to rest this key and reach for the other one.
    """

    def __init__(self, message: str, retry_after: float = 0.0, quota: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.quota = quota


class UsageError(RuntimeError):
    """A call that answered, and was billed, but whose answer is unusable.

    The tokens are attached because a rejected answer costs exactly what an accepted one
    does — a truncated batch is the most expensive thing this script can do, and it would
    otherwise be invisible in the total.
    """

    def __init__(self, message: str, usage: dict):
        super().__init__(message)
        self.usage = usage


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def n_items(value) -> int:
    """Number of ' | '-separated items in a cell."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0
    return len([p for p in str(value).split("|") if p.strip()])


def df_to_csv_text(df: pd.DataFrame) -> str:
    buf = io.StringIO()
    df.to_csv(buf, index=False, quoting=csv.QUOTE_ALL, lineterminator="\n")
    return buf.getvalue()


def strip_fences(text: str) -> str:
    """Remove markdown fences and any chatter before the header row."""
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text).strip()
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if "job_code" in line[:200]:
            return "\n".join(lines[i:]).strip()
    return text


def parse_csv_text(text: str) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(strip_fences(text)), dtype=str,
                       keep_default_na=False)


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def validate(src: pd.DataFrame, out: pd.DataFrame, cols: list[str]) -> list[str]:
    errs: list[str] = []

    if len(out) != len(src):
        errs.append(f"row count: got {len(out)}, expected {len(src)}")

    missing = [c for c in cols if c not in out.columns]
    if missing:
        errs.append(f"missing columns: {missing}")
    extra = [c for c in out.columns if c not in cols]
    if extra:
        errs.append(f"unexpected extra columns: {extra}")
    if missing:
        return errs  # can't check further

    src_codes = list(src["job_code"].astype(str))
    out_codes = list(out["job_code"].astype(str))
    if src_codes != out_codes:
        lost = [c for c in src_codes if c not in out_codes]
        bad = [c for c in out_codes if c not in src_codes]
        if lost:
            errs.append(f"job_code missing from output: {lost}")
        if bad:
            errs.append(f"job_code not in input: {bad}")
        if not lost and not bad:
            errs.append("job_code order changed")
        return errs

    for i, code in enumerate(src_codes):
        for c in cols:
            if c not in LIST_COLUMNS:
                continue
            exp, got = n_items(src.iloc[i][c]), n_items(out.iloc[i][c])
            if abs(exp - got) > ITEM_TOLERANCE or (exp and not got):
                errs.append(f"{code} / {c}: expected {exp} items, got {got}")
        if not str(out.iloc[i]["description"]).strip():
            errs.append(f"{code} / description: empty")
    return errs


# --------------------------------------------------------------------------
# API — Google native Gemini API (google-genai SDK)
# --------------------------------------------------------------------------

def _build_config() -> genai_types.GenerateContentConfig:
    kwargs = dict(
        system_instruction=SYSTEM_PROMPT,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        temperature=0,
        top_p=0.95,
    )
    if REASONING_EFFORT == "none":
        kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_budget=0)
    return genai_types.GenerateContentConfig(**kwargs)


def read_usage(resp: genai_types.GenerateContentResponse) -> dict:
    """The three numbers worth keeping out of a GenerateContentResponse.usage_metadata.

    There is deliberately no `usd` any more. The native API returns no cost the way the
    old proxy's `usage.cost` did, so the port hardcoded 0.0 — and every line that printed
    the tokens was written as `... if spent["usd"] else ""`, which meant the *token*
    counts silently stopped printing the moment the dollar figure became a constant zero.
    Reporting what is actually measured is the fix; a dollar figure would need a price
    table maintained by hand.
    """
    u = resp.usage_metadata
    return {
        "in": int(getattr(u, "prompt_token_count", 0) or 0),
        "out": int(getattr(u, "candidates_token_count", 0) or 0),
        "think": int(getattr(u, "thoughts_token_count", 0) or 0),
    }


def new_usage() -> dict:
    return {"in": 0, "out": 0, "think": 0}


def add_usage(total: dict, one: dict) -> None:
    for k in ("in", "out", "think"):
        total[k] += one.get(k, 0)


def fmt_usage(u: dict) -> str:
    """`12,176 + 16,209 tok`, or "" when nothing was spent."""
    if not (u.get("in") or u.get("out")):
        return ""
    return (f"{u.get('in', 0):,} + {u.get('out', 0):,} tok"
            + (f" ({u['think']:,} thinking)" if u.get("think") else ""))


_CLIENTS: dict[str, genai.Client] = {}
_CLIENTS_LOCK = threading.Lock()


def client_for(api_key: str, timeout: int) -> genai.Client:
    """One client per key for the whole process, and the only place the timeout is set.

    It was one `genai.Client` per call — a fresh connection pool and TLS handshake for
    every batch and every retry. The timeout is the other half: the `timeout` parameter
    survived the port from `requests.post` as a dead argument the SDK never saw, so a
    stalled connection would have hung a worker thread forever and three of them is the
    entire pool. `HttpOptions.timeout` is in **milliseconds**.
    """
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(api_key)
        if client is None:
            client = genai.Client(
                api_key=api_key,
                http_options=genai_types.HttpOptions(timeout=int(timeout * 1000)))
            _CLIENTS[api_key] = client
        return client


_RETRY_AFTER_RE = re.compile(r"retry in ([0-9.]+)\s*s", re.IGNORECASE)


def retry_delay(exc: genai_errors.APIError) -> float:
    """How many seconds the server asked us to wait; 0 when it did not say.

    A quota 429 carries a RetryInfo in `details` and repeats it in the message text. The
    fixed 5 / 10 / 20 backoff ignored both and gave the batch up after ~35 s while the
    server was asking for 58 — which is how a run that only needed patience came back as
    "no answer after 4 attempts (network / rate limit)" with nothing translated.
    """
    stack = [getattr(exc, "details", None)]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            raw = node.get("retryDelay")
            if raw is not None:
                m = re.match(r"([0-9.]+)s?$", str(raw).strip())
                if m:
                    return float(m.group(1))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    m = _RETRY_AFTER_RE.search(str(getattr(exc, "message", "") or ""))
    return float(m.group(1)) if m else 0.0


def short(message: str, limit: int = 160) -> str:
    """One line out of an API message — a quota 429 body is a five-line paragraph."""
    line = " ".join(str(message).split())
    return line[:limit] + ("…" if len(line) > limit else "")


def quota_note(message: str) -> str:
    """The one informative clause of a quota 429: which budget, and how big it is.

    The rest of that body is the same three sentences of documentation links every time,
    and this line is printed once a minute per batch while a run waits out a window.
    """
    m = re.search(r"metric:\s*\S*?/([^\s,]+),?\s*limit:\s*(\d+)",
                  " ".join(message.split()))
    return f"{m.group(1)} limit {m.group(2)}" if m else short(message, 80)


class KeyRing:
    """The keys this run may spend, and when each of them is next worth trying.

    The free tier's request budget is **per key**, not per project, so a 429 naming the
    quota is not a reason to stop — it is a reason to rest that key and reach for the
    next one. Resting rather than retiring is what keeps a per-minute window (the server
    asks for 6 s) from being read as a spent day.
    """

    def __init__(self, keys: list[tuple[str, str]]):
        self._keys = keys                       # [(env name, key), …], in preference order
        self._ready_at = {name: 0.0 for name, _ in keys}
        self._strikes = 0        # consecutive quota refusals, across every worker
        self._last_ok = time.time()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self._keys]

    def acquire(self) -> tuple[str, str, float]:
        """`(env name, key, 0)` for the first key that is ready, else `("", "", wait)`."""
        now = time.time()
        with self._lock:
            waits = []
            for name, key in self._keys:
                ready = self._ready_at[name]
                if ready <= now:
                    return name, key, 0.0
                waits.append(ready - now)
            return "", "", min(waits) if waits else 0.0

    def rest(self, name: str, seconds: float) -> None:
        with self._lock:
            self._ready_at[name] = max(self._ready_at.get(name, 0.0),
                                       time.time() + max(seconds, 1.0))
            self._strikes += 1

    def note_ok(self) -> None:
        with self._lock:
            self._strikes = 0
            self._last_ok = time.time()

    @property
    def spent(self) -> bool:
        """Every key has refused, repeatedly, with nothing getting through in between.

        The daily budget and the per-minute one arrive as the same 429, and waiting is
        the right answer to only one of them. This is the line between: a window that
        reopens lets *some* batch through every minute or two, which resets both numbers,
        while a spent day never does — and once the day is clearly gone, the remaining
        batches say so at once instead of each spending its own stall budget on the same
        wall. Both conditions are needed: on a free tier tight enough that three workers
        keep tripping over each other, the strike count alone reaches its threshold
        during a window that is still very much serving requests.
        """
        with self._lock:
            return (self._strikes >= 2 * len(self._keys) + 2
                    and time.time() - self._last_ok >= QUOTA_GIVE_UP)


def call_llm(api_key: str, model: str, user_text: str,
             timeout: int = 900) -> tuple[str, dict]:
    client = client_for(api_key, timeout)
    try:
        resp = client.models.generate_content(
            model=model, contents=user_text, config=_build_config())
    except genai_errors.ServerError as exc:
        raise TransientError(f"{exc.code} {short(exc.message)}",
                             retry_delay(exc)) from exc
    except genai_errors.ClientError as exc:
        if exc.code == 429:
            delay = retry_delay(exc)
            raise TransientError(
                f"429 out of quota ({quota_note(str(exc.message))})"
                + (f", server asks for {delay:.0f}s" if delay else ""),
                delay, quota=True) from exc
        raise RuntimeError(f"{exc.code} {short(exc.message, 400)}") from exc
    except httpx.TimeoutException as exc:
        raise TransientError(f"{type(exc).__name__}: {exc}") from exc
    except httpx.TransportError as exc:
        raise TransientError(f"{type(exc).__name__}: {exc}") from exc

    usage = read_usage(resp)

    if not resp.candidates:
        # e.g. the prompt itself got blocked — resp.prompt_feedback has the reason
        raise UsageError(f"no candidates in answer: {resp.prompt_feedback}", usage)

    reason = resp.candidates[0].finish_reason
    if reason not in (None, genai_types.FinishReason.STOP):
        # MAX_TOKENS here means the CSV was cut off mid-record — never accept it.
        # It is also the expensive failure: the tokens were spent and are unusable,
        # which is why the batch size is capped by the output limit, not by taste.
        raise UsageError(f"finish_reason={reason} (output truncated or blocked)", usage)

    text = resp.text or ""
    if not text.strip():
        raise UsageError("empty response body", usage)
    return text, usage


# --------------------------------------------------------------------------
# one batch
# --------------------------------------------------------------------------

def process_batch(path: Path, out_dir: Path, ring: KeyRing, model: str,
                  retries: int, overwrite: bool, timeout: int,
                  stall_retries: int) -> dict:
    name = path.stem
    spent = new_usage()                       # every attempt, failures too
    dest = out_dir / f"{name}_fa.xlsx"
    if dest.exists() and not overwrite:
        return {"batch": name, "status": "skipped", "reason": "already exists",
                "spent": spent}

    src = pd.read_excel(path, dtype=str).fillna("")
    src.columns = [str(c).strip() for c in src.columns]

    unexpected = [c for c in src.columns if c not in COLUMNS]
    if unexpected:
        return {"batch": name, "status": "failed", "spent": spent,
                "errors": [f"input has unknown columns: {unexpected}"]}

    send = src if TRANSLATE_TOOLS else src.drop(columns=["tools"], errors="ignore")
    cols = list(send.columns)
    payload = df_to_csv_text(send)

    raw = ""
    last_errs: list[str] = []
    attempt = 0        # answers the model gave that were unusable — its own budget
    stalls = 0         # 429s, timeouts, 5xx — not the model's fault, so not its budget
    quota_only = True  # nothing but rate limits stood in the way

    # The two budgets are separate on purpose. They used to share one counter, so four
    # 429s in forty seconds spent every retry the *translation* was entitled to and the
    # batch was written off without the model ever having been asked. A rate limit is a
    # reason to wait, not a reason to give up on a record.
    while attempt < retries:
        if ring.spent:
            last_errs = last_errs or [
                f"free-tier request budget on {', '.join(ring.names)} is spent — "
                f"every key kept answering 429 with nothing getting through"]
            break

        key_name, api_key, wait = ring.acquire()
        if not api_key:                       # every key is resting
            stalls += 1
            if stalls > stall_retries:
                last_errs = last_errs or [
                    f"every key ({', '.join(ring.names)}) is rate-limited after "
                    f"{stall_retries} waits — the free-tier request budget is spent, "
                    f"try again later or on another key"]
                break
            print(f"  [{name}] all keys rate-limited, waiting {min(wait, MAX_WAIT):.0f}s "
                  f"({stalls}/{stall_retries})", file=sys.stderr)
            time.sleep(min(wait, MAX_WAIT) + random.uniform(0, 2))
            continue

        user_text = payload
        if last_errs:
            user_text = (
                "Your previous answer had these problems:\n"
                + "\n".join(f"- {e}" for e in last_errs[:15])
                + "\n\nTranslate the CSV below again and fix them. "
                  "Keep every row and every item.\n\n"
                + payload
            )
        try:
            raw, usage = call_llm(api_key, model, user_text, timeout)
            ring.note_ok()                    # the wall is not up: reset the strike count
            add_usage(spent, usage)
            out = parse_csv_text(raw)
            out.columns = [str(c).strip() for c in out.columns]
            errs = validate(send, out, cols)
        except TransientError as exc:
            # Nothing for the model to fix. Rest the key the server complained about —
            # the budget is per key, so the other one may still answer — and wait as long
            # as the server asked rather than as long as a doubling backoff felt like.
            stalls += 1
            if exc.quota:
                ring.rest(key_name, exc.retry_after or 60.0)
            print(f"  [{name}] {key_name}: {exc}  (stall {stalls}/{stall_retries})",
                  file=sys.stderr)
            if stalls > stall_retries:
                last_errs = last_errs or [f"gave up after {stalls} rate-limit/network "
                                          f"failures, last: {exc}"]
                break
            if not exc.quota:                 # a rested key needs no sleep of our own
                time.sleep(min(MAX_WAIT, exc.retry_after or 5 * 2 ** min(stalls - 1, 4))
                           + random.uniform(0, 3))
            continue
        except UsageError as exc:                     # answered, billed, unusable
            add_usage(spent, exc.usage)
            errs = [f"{type(exc).__name__}: {exc}"]
            out = None
        except Exception as exc:                      # parse / API / bad answer
            errs = [f"{type(exc).__name__}: {exc}"]
            out = None

        attempt += 1
        quota_only = False

        if not errs:
            if not TRANSLATE_TOOLS:
                out.insert(COLUMNS.index("tools"), "tools", src["tools"].values)
            out = out[[c for c in COLUMNS if c in out.columns]]
            out_dir.mkdir(parents=True, exist_ok=True)
            with pd.ExcelWriter(dest, engine="openpyxl") as w:
                out.to_excel(w, index=False, sheet_name="onet_fa")
            return {"batch": name, "status": "ok", "rows": len(out),
                    "attempts": attempt, "spent": spent}

        last_errs = errs
        print(f"  [{name}] attempt {attempt}/{retries} failed "
              f"({fmt_usage(spent) or 'no tokens'} spent so far): {errs[0]}",
              file=sys.stderr)
        if attempt < retries:
            time.sleep(min(60, 2 ** attempt) + random.uniform(0, 2))

    if not last_errs:
        last_errs = [f"no answer after {retries} attempts (network / rate limit)"]

    # Keep the last raw answer so the batch can be inspected by hand — and the error
    # beside it, because a batch that never got an answer writes an empty file otherwise
    # and an empty _raw.txt says nothing about why.
    fail_dir = out_dir / "_failed"
    fail_dir.mkdir(parents=True, exist_ok=True)
    try:
        (fail_dir / f"{name}_raw.txt").write_text(raw, encoding="utf-8")
        (fail_dir / f"{name}_error.txt").write_text(
            "\n".join(last_errs[:20]), encoding="utf-8")
    except Exception:
        pass
    return {"batch": name, "status": "failed", "errors": last_errs[:20],
            "spent": spent, "rate_limited": quota_only}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    # anchored on this file's directory, so the script runs from anywhere
    ap.add_argument("--in", dest="in_dir", default=str(HERE / "batch_10"))
    ap.add_argument("--out", dest="out_dir", default="",
                    help="default: <in>_fa, beside the input directory")
    ap.add_argument("--api-key", default="",
                    help="one key; without it every GEMINI_API_KEY* in the environment "
                         "is used, rotating when one runs out of quota")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--workers", type=int, default=3,
                    help="parallel batches; the free tier's window is small enough that "
                         "more of these mostly buys 429s and waits")
    ap.add_argument("--retries", type=int, default=4,
                    help="attempts at an answer the validator will accept")
    ap.add_argument("--stall-retries", type=int, default=12,
                    help="waits for a rate limit or a network failure — a budget of its "
                         "own, so a busy minute cannot spend the retries above")
    ap.add_argument("--timeout", type=int, default=900,
                    help="seconds before one request is abandoned as hung")
    ap.add_argument("--limit", type=int, default=0,
                    help="only the first N batches — for a trial run")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    keys = ([("--api-key", args.api_key)] if args.api_key else ENV_KEYS)
    if not keys:
        print(f"error: pass --api-key or set one of {', '.join(KEY_ENV_NAMES)}",
              file=sys.stderr)
        return 2
    ring = KeyRing(keys)

    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir) if args.out_dir else in_dir.parent / f"{in_dir.name}_fa"
    files = sorted(p for p in in_dir.glob("*.xlsx")
                   if not p.name.startswith("~$") and not p.stem.endswith("_fa"))
    if not files:
        print(f"error: no .xlsx files in {in_dir}", file=sys.stderr)
        return 2
    if args.limit:
        files = files[:args.limit]

    todo = [f for f in files
            if args.overwrite or not (out_dir / f"{f.stem}_fa.xlsx").exists()]
    print(f"model={args.model}  keys={', '.join(ring.names)}\n"
          f"in={in_dir}  out={out_dir}\n"
          f"batches={len(files)} ({len(todo)} to translate, "
          f"{len(files) - len(todo)} already done)  workers={args.workers}  "
          f"translate_tools={TRANSLATE_TOOLS}  "
          f"reasoning_effort={REASONING_EFFORT or '(unset)'}")

    results = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_batch, f, out_dir, ring, args.model,
                        args.retries, args.overwrite, args.timeout,
                        args.stall_retries): f
            for f in files
        }
        for fut in cf.as_completed(futures):
            r = fut.result()
            results.append(r)
            mark = {"ok": "OK", "skipped": "--", "failed": "!!"}[r["status"]]
            tok = fmt_usage(r.get("spent") or {})
            print(f"[{mark}] {r['batch']}"
                  + (f"  ({r['rows']} rows, {r['attempts']} attempt(s))"
                     if r["status"] == "ok" else "")
                  + (f"  {tok}" if tok else "")
                  + (f"  {r['errors'][0]}" if r["status"] == "failed" else ""))

    results.sort(key=lambda r: r["batch"])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(r["status"] == "ok" for r in results)
    skipped = sum(r["status"] == "skipped" for r in results)
    failed = [r for r in results if r["status"] == "failed"]

    total = new_usage()
    for r in results:
        if r.get("spent"):
            add_usage(total, r["spent"])
    print(f"\ntokens: {total['in']:,} in + {total['out']:,} out"
          + (f" (of which {total['think']:,} thinking)" if total["think"] else ""))
    print(f"done: {ok} translated, {skipped} skipped, {len(failed)} failed")
    for r in failed:
        print(f"  {r['batch']}: {r['errors'][0]}")
    if failed:
        print("raw answers for failed batches are in", out_dir / "_failed")

    # A run that translated nothing because the budget was gone is not the same failure
    # as a run the model got wrong, and it is the one that looks like a broken script:
    # say so, and say what to do about it.
    rate_limited = [r for r in failed if r.get("rate_limited")]
    if rate_limited:
        print(f"\n{len(rate_limited)} of the {len(failed)} failures never reached the "
              f"model — the free-tier request budget on {', '.join(ring.names)} is spent."
              f"\nTranslated batches are kept, so re-running this tomorrow (or with "
              f"another key) picks up exactly where it stopped.")
    remaining = sum(1 for f in files
                    if not (out_dir / f"{f.stem}_fa.xlsx").exists())
    if remaining:
        print(f"{remaining} of {len(files)} batches in {in_dir.name} still have no "
              f"translation.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
