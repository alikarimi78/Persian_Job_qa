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
import openai
from openai import OpenAI


HERE = Path(__file__).resolve().parent

try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass

logging.getLogger("openai").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)

KEY_ENV_NAMES = ("XKIRO_API_KEY", "OPENAI_API_KEY")
ENV_KEYS = [(name, os.environ[name]) for name in KEY_ENV_NAMES if os.environ.get(name)]

BASE_URL = os.environ.get("TRANSLATE_BASE_URL", "https://api.xkiro.com/v1")

MODEL = os.environ.get("TRANSLATE_MODEL", "openai/gpt-5.6-luna")

MAX_OUTPUT_TOKENS = 65536

# only sent when set — many models reject the parameter
REASONING_EFFORT = os.environ.get("TRANSLATE_REASONING_EFFORT", "")

TRANSLATE_TOOLS = True

COLUMNS = ["job_code", "job_title", "aliases", "tools", "skills", "knowledge",
           "abilities", "work_context", "career_path_next", "description",
           "responsibilities"]

# every item must survive the translation, one for one
STRICT_LIST_COLUMNS = ["aliases", "skills", "knowledge", "abilities",
                       "career_path_next"]

# only the most important items are kept. (what the prompt asks for,
# how few the validator still accepts, how many it still accepts)
CAPPED_LIST_COLUMNS = {
    "tools": ("6-7", 4, 9),
    "work_context": ("6-7", 4, 9),
    "responsibilities": ("8-10", 6, 12),
}

LIST_COLUMNS = STRICT_LIST_COLUMNS + list(CAPPED_LIST_COLUMNS)

ITEM_TOLERANCE = 1

PERSIAN_RE = re.compile(r"[\u0600-\u06FF]")

SYSTEM_PROMPT = """\
You are a Senior Localization Expert and Terminology Specialist for Iranian Human
Resources, occupational classification, and public-sector job descriptions. You are
localizing an O*NET occupation dataset into Persian for an Iranian public institution
that will use it for job classification and job-description work. The register is formal
administrative Persian, the language of an official job description, not of an article
or a conversation.

Your job is NOT translation. It is localization: eliminate literal word-for-word
renderings, English sentence structures and calques, and produce the wording that is
actually used in Iranian administrative, technical, clinical and labor-market contexts.

I will send you a CSV of occupation records. Localize them and return the CSV.

# 0. Output format
- RFC 4180 CSV. Every field wrapped in double quotes, internal quotes doubled ("").
  One record per line, no newlines inside cells. Include the header row.
- Do NOT wrap the output in a markdown code block. Write no text before or after the
  CSV: no preamble, no summary, no commentary.
- Same column names, same order, no extra columns:
  job_code,job_title,aliases,tools,skills,knowledge,abilities,work_context,career_path_next,description,responsibilities
- Output row count MUST equal input row count, in the same order. Never drop, merge or
  add a row. Never modify job_code or the column headers.
- Items inside a cell are separated by " | " (space pipe space). Preserve that separator
  EXACTLY, never turn it into a comma, a dash or a newline.
- Be consistent: once you choose a Persian equivalent for an English term, use that same
  equivalent everywhere. Never vary the wording for style.

# 1. Localization, the core rule

## 1.1 Job titles
Replace literal renderings of medical, administrative, technical and industrial titles
with the authentic Iranian equivalent, the title an Iranian organization would actually
put on the position.
  Licensed Practical Nurse - پرستار عملی دارای مجوز WRONG - بهیار / کمک‌پرستار RIGHT
  Dispensing Optician - عینک‌ساز توزیع‌کننده WRONG - عینک‌ساز (ساخت و فروش عینک) RIGHT
  Health Technologists, All Other - تکنولوژیست‌های سلامت، همه مشاغل دیگر WRONG -
    سایر متخصصان و تکنسین‌های حوزه سلامت RIGHT
Keep the original English title in parentheses after the Persian:
  بهیاران و کمک‌پرستاران دارای مجوز (Licensed Practical and Licensed Vocational Nurses)

## 1.2 US-specific references
This is a US dataset. Where a cell names a US-specific institution, law or credential
that has no Iranian counterpart, generalize it to the equivalent concept rather than
importing the American name:
  state licensing board -> مراجع صدور مجوز / مرجع صدور پروانه (not هیئت ایالتی)
  OSHA regulations -> مقررات ایمنی و بهداشت حرفه‌ای
  federal and state law -> قوانین و مقررات جاری
  associate's degree -> مدرک کاردانی · bachelor's degree -> مدرک کارشناسی
  high school diploma -> دیپلم متوسطه
Never invent an Iranian law, organization or certificate name that the source did not
imply.

## 1.3 Fixed terminology glossary
These O*NET concepts are mapped strictly. Use exactly these equivalents:
  Written Comprehension -> درک کتبی        Oral Comprehension -> درک شفاهی
  Written Expression -> بیان کتبی          Oral Expression -> بیان شفاهی
  Writing -> نگارش                         Reading Comprehension -> درک مطلب
  Monitoring -> نظارت                      Active Listening -> شنیدن فعال
  Speaking -> سخن گفتن                     Critical Thinking -> تفکر انتقادی
  Active Learning -> یادگیری فعال          Coordination -> هماهنگی
  Instructing -> آموزش دادن                Service Orientation -> خدمت‌محوری
  Time Management -> مدیریت زمان           Negotiation -> مذاکره
  Persuasion -> ترغیب و متقاعدسازی         Social Perceptiveness -> ادراک اجتماعی
  Complex Problem Solving -> حل مسائل پیچیده
  Judgment and Decision Making -> قضاوت و تصمیم‌گیری
  Operations Monitoring -> پایش عملیات     Quality Control Analysis -> کنترل کیفیت
  Deductive Reasoning -> استدلال قیاسی     Inductive Reasoning -> استدلال استقرایی
  Problem Sensitivity -> حساسیت به مسئله   Information Ordering -> مرتب‌سازی اطلاعات
  Near Vision -> دید نزدیک                 Far Vision -> دید دور
  Manual Dexterity -> چابکی دستی           Finger Dexterity -> چابکی انگشتان
  Speech Clarity -> وضوح گفتار             Speech Recognition -> تشخیص گفتار
  Customer and Personal Service -> خدمات مشتریان و خدمات فردی
  Administration and Management -> مدیریت و امور اداری
  Public Safety and Security -> ایمنی و امنیت عمومی

## 1.4 General wording
  troubleshoot equipment -> عیب‌یابی تجهیزات (not رفع مشکل تجهیزات)
  stakeholders -> ذی‌نفعان · quality assurance -> تضمین کیفیت
  on-the-job training -> آموزش حین کار · shift work -> نوبت‌کاری
  entry-level -> سطح مقدماتی · compliance -> انطباق با مقررات
  workflow -> گردش کار · hands-on experience -> تجربهٔ عملی
  oversee -> نظارت بر · liaise with -> هماهنگی با · ensure -> حصول اطمینان از
If a phrase has no good Persian equivalent, rephrase it into something an Iranian
professional would actually say. Never produce a mechanical calque, and never add
information that is not in the cell.

# 2. Register and sentence structure
- In description and responsibilities, do NOT use narrative third-person plural verbs
  (انجام می‌دهند، آماده می‌کنند، مراقبت می‌کنند).
- Use the noun-phrase / masdar structures standard in Iranian official job descriptions:
  انجام آزمایش‌ها…، تهیه و آماده‌سازی واکسن‌ها…، نظارت بر عملکرد…، ثبت و نگهداری سوابق…
- Short, clear sentences. Persian word order. Avoid long اضافه chains and avoid padding
  every verb with «انجام دادن».
- Neutral and official in tone: no slang, no marketing language, no literary Persian.

# 3. Column by column

## 3.1 Localized in full, nothing dropped, added or reordered
  aliases · skills · knowledge · abilities · career_path_next
Every item is localized one for one, in the same order, with the same item count as the
input. The list length of these columns must match the input exactly.

  description: localized in FULL, no sentence dropped. You may split or reorder
  sentences so the Persian reads well, but the content stays complete.

  job_code: copied verbatim. It is the join key.

## 3.2 Selected, keep only what matters for that occupation
  tools · work_context: keep the 6-7 most important, most representative items and
  localize only those. Drop the rest.
  responsibilities: keep the 8-10 tasks that actually define the job and localize only
  those. Drop the rest.

Rules for all three:
- Order from most important to least important.
- Judge importance by what a person in that job actually does, uses and needs, not by
  the order of the input list, and by how that job is practised in Iran. An item that
  only makes sense in a US workplace, or that is true of almost any office job, is a
  weak candidate.
- Merge or drop near-duplicates: if two items say nearly the same thing, keep the
  clearer one.
- These are ceilings, not quotas. If the input has fewer items than the ceiling, keep
  them all. If a genuinely essential item would be lost by stopping at the ceiling, you
  may keep one or two more, but never pad a list to reach a number, and never invent an
  item that is not in the input.

# 4. The tools column, decide for each item separately
1. Proper name -> leave in English, untouched. Product, brand, company, programming
   language, framework, operating system, cloud service.
   Microsoft Excel · Python · Oracle Database · Git · Ubuntu · Tableau · SAP · React
2. Generic description with no brand -> translate.
   Database software -> نرم‌افزار پایگاه داده · Email software -> نرم‌افزار ایمیل
   Web browser software -> نرم‌افزار مرورگر وب · Firewall software -> نرم‌افزار فایروال
   Keep technical acronyms inside them in English:
   Geographic information system GIS software -> نرم‌افزار سیستم اطلاعات جغرافیایی GIS
3. Brand + generic word -> the brand stays English, the generic word becomes Persian.
   Microsoft Office software -> نرم‌افزار Microsoft Office · SAP software -> نرم‌افزار SAP
4. Physical tool with a concrete Persian equivalent -> translate.
   wrench -> آچار · screwdriver -> پیچ‌گوشتی · caliper -> کولیس · multimeter -> مولتی‌متر
When unsure whether an item is a brand or a generic description, leave it in English.

# 5. The work_context column
Keep only the conditions that really characterise this job, written as natural Persian
phrases rather than as translated O*NET labels:
  Face-to-Face Discussions -> گفت‌وگوی حضوری با همکاران
  Telephone -> ارتباط تلفنی · Electronic Mail -> مکاتبهٔ ایمیلی
  Indoors, Environmentally Controlled -> محیط سرپوشیده با تهویهٔ مطبوع
  Work With Work Group or Team -> کار گروهی و تیمی
  Importance of Being Exact or Accurate -> اهمیت بالای دقت در کار
  Spend Time Sitting -> نشستن طولانی‌مدت پشت میز
  Exposed to Contaminants -> مواجهه با آلاینده‌ها
  Wear Common Protective or Safety Equipment -> استفاده از تجهیزات حفاظت فردی

# 6. Never translate
Numbers, and the acronyms:
CEO, CFO, CIO, COO, CTO, EVP, HRIS, SQL, XML, ERP, CRM, KPI, ROI, GIS, GPS, LEED, CFP

# 7. Worked example

Literal, wrong:
job_title: پرستاران عملی دارای مجوز و پرستاران حرفه‌ای دارای مجوز (Licensed Practical and Licensed Vocational Nurses)
description: از بیماران بیمار، آسیب‌دیده یا در حال بهبودی و افراد دارای معلولیت در بیمارستان‌ها مراقبت می‌کنند.
responsibilities: داروهای بیهوشی را زیر نظر دامپزشک به حیوانات می‌دهند | نمونه‌های خون را جمع‌آوری می‌کنند.

Localized, right:
job_title: بهیاران و کمک‌پرستاران دارای مجوز (Licensed Practical and Licensed Vocational Nurses)
description: مراقبت از بیماران، مجروحان، افراد در حال نقاهت و افراد دارای معلولیت در بیمارستان‌ها، درمانگاه‌ها و مراکز مراقبتی.
responsibilities: تجویز داروهای بیهوشی به حیوانات تحت نظارت دامپزشک | جمع‌آوری و آماده‌سازی نمونه‌های خون
"""


MAX_WAIT = 120.0

QUOTA_GIVE_UP = 300.0


class TransientError(RuntimeError):
    def __init__(self, message: str, retry_after: float = 0.0, quota: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.quota = quota


class UsageError(RuntimeError):
    def __init__(self, message: str, usage: dict):
        super().__init__(message)
        self.usage = usage


def n_items(value) -> int:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0
    return len([p for p in str(value).split("|") if p.strip()])


def df_to_csv_text(df: pd.DataFrame) -> str:
    buf = io.StringIO()
    df.to_csv(buf, index=False, quoting=csv.QUOTE_ALL, lineterminator="\n")
    return buf.getvalue()


def strip_fences(text: str) -> str:
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


def capped_bounds(column: str, expected: int) -> tuple[int, int]:
    """How many items a capped column may keep for a source cell of `expected` items."""
    if expected <= 0:
        return 0, 0
    _, lo, hi = CAPPED_LIST_COLUMNS[column]
    return min(expected, lo), min(expected, hi)


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
        return errs

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
            exp, got = n_items(src.iloc[i][c]), n_items(out.iloc[i][c])
            if c in STRICT_LIST_COLUMNS:
                if abs(exp - got) > ITEM_TOLERANCE or (exp and not got):
                    errs.append(f"{code} / {c}: expected {exp} items, got {got}")
            elif c in CAPPED_LIST_COLUMNS:
                target = CAPPED_LIST_COLUMNS[c][0]
                lo, hi = capped_bounds(c, exp)
                if got > hi:
                    errs.append(f"{code} / {c}: keep only the {target} most important "
                                f"items, got {got}")
                elif got < lo:
                    errs.append(f"{code} / {c}: too few items, got {got}, "
                                f"expected at least {lo}")

        desc = str(out.iloc[i]["description"]).strip()
        if not desc:
            errs.append(f"{code} / description: empty")
        elif not PERSIAN_RE.search(desc):
            errs.append(f"{code} / description: still in English, not translated")
    return errs


_DROPPED_PARAMS: set[str] = set()
_PARAM_LOCK = threading.Lock()

_PARAM_CANDIDATES = ("temperature", "max_tokens", "max_completion_tokens",
                     "reasoning_effort")


def _build_kwargs(model: str, user_text: str) -> dict:
    with _PARAM_LOCK:
        dropped = set(_DROPPED_PARAMS)

    kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
    }
    if "temperature" not in dropped:
        kwargs["temperature"] = 0
    if "max_tokens" not in dropped:
        kwargs["max_tokens"] = MAX_OUTPUT_TOKENS
    elif "max_completion_tokens" not in dropped:
        kwargs["max_completion_tokens"] = MAX_OUTPUT_TOKENS
    if REASONING_EFFORT and "reasoning_effort" not in dropped:
        kwargs["reasoning_effort"] = REASONING_EFFORT
    return kwargs


def _drop_bad_param(message: str) -> str:
    """A 400 naming a parameter the endpoint does not take — remember it and move on."""
    text = str(message).lower()
    for name in _PARAM_CANDIDATES:
        if name in text:
            with _PARAM_LOCK:
                if name in _DROPPED_PARAMS:
                    continue
                _DROPPED_PARAMS.add(name)
            return name
    return ""


def read_usage(resp) -> dict:
    u = getattr(resp, "usage", None)
    if u is None:
        return new_usage()
    details = getattr(u, "completion_tokens_details", None)
    return {
        "in": int(getattr(u, "prompt_tokens", 0) or 0),
        "out": int(getattr(u, "completion_tokens", 0) or 0),
        "think": int(getattr(details, "reasoning_tokens", 0) or 0),
    }


def new_usage() -> dict:
    return {"in": 0, "out": 0, "think": 0}


def add_usage(total: dict, one: dict) -> None:
    for k in ("in", "out", "think"):
        total[k] += one.get(k, 0)


def fmt_usage(u: dict) -> str:
    if not (u.get("in") or u.get("out")):
        return ""
    return (f"{u.get('in', 0):,} + {u.get('out', 0):,} tok"
            + (f" ({u['think']:,} thinking)" if u.get("think") else ""))


_CLIENTS: dict[tuple[str, str], OpenAI] = {}
_CLIENTS_LOCK = threading.Lock()


def client_for(api_key: str, base_url: str, timeout: int) -> OpenAI:
    with _CLIENTS_LOCK:
        client = _CLIENTS.get((api_key, base_url))
        if client is None:
            client = OpenAI(
                api_key=api_key or "no-key-needed",
                base_url=base_url,
                timeout=float(timeout),
                max_retries=0,          # retrying is this script's job
            )
            _CLIENTS[(api_key, base_url)] = client
        return client


_RETRY_AFTER_RE = re.compile(r"retry (?:in|after) ([0-9.]+)\s*s", re.IGNORECASE)


def retry_delay(exc: Exception) -> float:
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if headers is not None and hasattr(headers, "get"):
        for h in ("retry-after", "x-ratelimit-reset-requests",
                  "x-ratelimit-reset-tokens"):
            raw = headers.get(h)
            if not raw:
                continue
            m = re.match(r"([0-9.]+)\s*(ms|s|m)?$", str(raw).strip())
            if m:
                value = float(m.group(1))
                unit = m.group(2)
                return value / 1000 if unit == "ms" else value * 60 if unit == "m" else value
    m = _RETRY_AFTER_RE.search(str(getattr(exc, "message", "") or exc))
    return float(m.group(1)) if m else 0.0


def short(message: str, limit: int = 160) -> str:
    line = " ".join(str(message).split())
    return line[:limit] + ("…" if len(line) > limit else "")


def quota_note(message: str) -> str:
    flat = " ".join(str(message).split())
    m = re.search(r"limit:?\s*(\d+)", flat)
    return f"limit {m.group(1)}" if m else short(flat, 80)


class KeyRing:
    def __init__(self, keys: list[tuple[str, str]]):
        self._keys = keys
        self._ready_at = {name: 0.0 for name, _ in keys}
        self._strikes = 0
        self._last_ok = time.time()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self._keys]

    def acquire(self) -> tuple[str, str, float]:
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
        with self._lock:
            return (self._strikes >= 2 * len(self._keys) + 2
                    and time.time() - self._last_ok >= QUOTA_GIVE_UP)


def call_llm(api_key: str, base_url: str, model: str, user_text: str,
             timeout: int = 900) -> tuple[str, dict]:
    client = client_for(api_key, base_url, timeout)

    resp = None
    for _ in range(len(_PARAM_CANDIDATES) + 1):
        try:
            resp = client.chat.completions.create(**_build_kwargs(model, user_text))
            break
        except openai.BadRequestError as exc:
            dropped = _drop_bad_param(getattr(exc, "message", "") or str(exc))
            if not dropped:
                raise RuntimeError(f"400 {short(exc.message, 400)}") from exc
            print(f"  note: the endpoint rejects '{dropped}', "
                  f"sending the request without it", file=sys.stderr)
            continue
        except openai.RateLimitError as exc:
            delay = retry_delay(exc)
            raise TransientError(
                f"429 out of quota ({quota_note(getattr(exc, 'message', '') or exc)})"
                + (f", server asks for {delay:.0f}s" if delay else ""),
                delay, quota=True) from exc
        except openai.APITimeoutError as exc:
            raise TransientError(f"timeout after {timeout}s: {short(exc)}") from exc
        except openai.APIConnectionError as exc:
            raise TransientError(f"{type(exc).__name__}: {short(exc)}") from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise TransientError(
                    f"{exc.status_code} {short(getattr(exc, 'message', '') or exc)}",
                    retry_delay(exc)) from exc
            raise RuntimeError(
                f"{exc.status_code} "
                f"{short(getattr(exc, 'message', '') or exc, 400)}") from exc
    if resp is None:
        raise TransientError("the endpoint kept rejecting the request parameters")

    usage = read_usage(resp)

    if not getattr(resp, "choices", None):
        raise UsageError("no choices in answer", usage)

    choice = resp.choices[0]
    reason = getattr(choice, "finish_reason", None)
    if reason not in (None, "stop"):
        raise UsageError(f"finish_reason={reason} (output truncated or blocked)", usage)

    text = getattr(choice.message, "content", "") or ""
    if not text.strip():
        raise UsageError("empty response body", usage)
    return text, usage


def process_batch(path: Path, out_dir: Path, ring: KeyRing, base_url: str, model: str,
                  retries: int, overwrite: bool, timeout: int,
                  stall_retries: int) -> dict:
    name = path.stem
    spent = new_usage()
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
    attempt = 0
    stalls = 0
    quota_only = True

    while attempt < retries:
        if ring.spent:
            last_errs = last_errs or [
                f"request budget on {', '.join(ring.names)} is spent — "
                f"every key kept answering 429 with nothing getting through"]
            break

        key_name, api_key, wait = ring.acquire()
        if not api_key:
            stalls += 1
            if stalls > stall_retries:
                last_errs = last_errs or [
                    f"every key ({', '.join(ring.names)}) is rate-limited after "
                    f"{stall_retries} waits — the request budget is spent, "
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
                + "\n\nLocalize the CSV below again and fix them. Keep every row, keep "
                  "every item of aliases, skills, knowledge, abilities and "
                  "career_path_next, and keep the Persian idiomatic and official. "
                  "For tools and work_context keep the 6-7 most important items, for "
                  "responsibilities the 8-10 that define the job.\n\n"
                + payload
            )
        try:
            raw, usage = call_llm(api_key, base_url, model, user_text, timeout)
            ring.note_ok()
            add_usage(spent, usage)
            out = parse_csv_text(raw)
            out.columns = [str(c).strip() for c in out.columns]
            errs = validate(send, out, cols)
        except TransientError as exc:
            stalls += 1
            if exc.quota:
                ring.rest(key_name, exc.retry_after or 60.0)
            print(f"  [{name}] {key_name}: {exc}  (stall {stalls}/{stall_retries})",
                  file=sys.stderr)
            if stalls > stall_retries:
                last_errs = last_errs or [f"gave up after {stalls} rate-limit/network "
                                          f"failures, last: {exc}"]
                break
            if not exc.quota:
                time.sleep(min(MAX_WAIT, exc.retry_after or 5 * 2 ** min(stalls - 1, 4))
                           + random.uniform(0, 3))
            continue
        except UsageError as exc:
            add_usage(spent, exc.usage)
            errs = [f"{type(exc).__name__}: {exc}"]
            out = None
        except Exception as exc:
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_dir", default=str(HERE / "batch_10"))
    ap.add_argument("--out", dest="out_dir", default="",
                    help="default: <in>_fa, beside the input directory")
    ap.add_argument("--api-key", default="",
                    help="one key; without it every key named in KEY_ENV_NAMES that is "
                         "set in the environment is used, rotating when one runs out "
                         "of quota")
    ap.add_argument("--base-url", default=BASE_URL,
                    help=f"OpenAI-compatible endpoint (default: {BASE_URL})")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--workers", type=int, default=3,
                    help="parallel batches")
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
        keys = [("(no key)", "")]
        print(f"note: no key given and none of {', '.join(KEY_ENV_NAMES)} is set — "
              f"sending unauthenticated requests to {args.base_url}", file=sys.stderr)
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
    print(f"model={args.model}  base_url={args.base_url}\n"
          f"keys={', '.join(ring.names)}\n"
          f"in={in_dir}  out={out_dir}\n"
          f"batches={len(files)} ({len(todo)} to translate, "
          f"{len(files) - len(todo)} already done)  workers={args.workers}  "
          f"translate_tools={TRANSLATE_TOOLS}  "
          f"capped={', '.join(f'{c}:{v[0]}' for c, v in CAPPED_LIST_COLUMNS.items())}  "
          f"reasoning_effort={REASONING_EFFORT or '(unset)'}")

    results = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_batch, f, out_dir, ring, args.base_url, args.model,
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

    rate_limited = [r for r in failed if r.get("rate_limited")]
    if rate_limited:
        print(f"\n{len(rate_limited)} of the {len(failed)} failures never reached the "
              f"model — the request budget on {', '.join(ring.names)} is spent."
              f"\nTranslated batches are kept, so re-running this later (or with "
              f"another key) picks up exactly where it stopped.")
    remaining = sum(1 for f in files
                    if not (out_dir / f"{f.stem}_fa.xlsx").exists())
    if remaining:
        print(f"{remaining} of {len(files)} batches in {in_dir.name} still have no "
              f"translation.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())