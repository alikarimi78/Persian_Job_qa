#!/usr/bin/env python3
"""
translate_script.py — send O*NET xlsx batches to the LLM, get Persian back, write xlsx.

    pip install requests pandas openpyxl
    export OPENAI_API_KEY="sk-..."            # the same key the engine uses
    python3 translate_script.py               # reads batch_10/, writes batch_10_fa/

The endpoint is the **OpenAI-compatible** one this project already talks to
(`OPENAI_BASE_URL`, default https://api.gapgpt.app/v1) — the same base URL and key as
`job_qa_service/llm.py`, not Google's native Gemini API, which is not reachable from
here. Any model the proxy lists works; `--model` / `TRANSLATE_MODEL` picks it.

Behaviour:
  * reads every .xlsx in --in (each = header + 10 rows), defaults to ./batch_10
  * sends it as CSV text, receives Persian CSV text
  * validates: row count, job_code match, column set, per-cell item counts
  * retries a failed batch up to --retries times, feeding the error back to the model
  * network/rate-limit failures are retried separately, without polluting the prompt
  * writes <name>_fa.xlsx into --out (default ./batch_10_fa), plus a report.json
  * already-translated batches are skipped, so you can stop and resume
  * prints the tokens and the dollars each batch spent, retries included, and totals
    them at the end — the proxy returns a real `usage.cost`, so this is not an estimate

Cost, measured on this proxy (2026-08-23, per million tokens: in / out):
    gemini-3.1-flash-lite  0.25 / 1.50   thinks 0 tokens
    gemini-2.5-flash       0.30 / 2.50   thinks 0 with reasoning_effort=none
    gemini-3.6-flash       0.75 / 3.75   thinks anyway, ~300 tokens a call
    gemini-3.5-flash       1.50 / 9.00   thinks anyway, ignores reasoning_effort
    gemini-3.1-pro-preview 2.00 / 12.00  thinks anyway, ignores reasoning_effort
Reasoning tokens are billed at the *output* rate, which is what makes a thinking model
expensive here: the work is translation, and none of the thinking reaches the file.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import io
import json
import os
import random
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent

# The project's own endpoint and key (see .env.example / job_qa_service/config.py).
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.gapgpt.app/v1").rstrip("/")
API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Not LLM_MODEL: that one is the runtime's answering model (gpt-4o-mini) and has nothing
# to do with this pass. Plain `gemini-3.1-flash` is not offered by the proxy (404
# model_not_found) — `-lite` is the whole of the 3.1 flash family there.
#
# flash-lite is the right model here and not a compromise: measured on batch_0002 it
# answered in one attempt, translated every list column, and kept brand names English —
# for $0.0246, where gemini-3.1-pro-preview cost $0.50 for the same ten rows. The gap is
# not skill, it is thinking: pro spends hundreds of reasoning tokens per call, billed at
# the output rate, on a task that is transcription. If a batch starts losing items,
# `gemini-3.6-flash` is the next step up (3× the price); `gemini-3.5-flash` and the pro
# models both ignore reasoning_effort and are 6–20× the price for no gain on this job.
MODEL = os.environ.get("TRANSLATE_MODEL", "gemini-3.1-flash-lite")

MAX_OUTPUT_TOKENS = 65536   # measured: a 10-row Persian batch is ~14.6k output tokens
# ...which is also the ceiling on --in batch size: at ~1.5k output tokens a row, 44 rows
# fills it and the answer is cut off mid-record. Bigger batches save nothing anyway —
# only the 695-token system prompt is re-sent, worth 2 cents over the whole dataset.

# Sent only to gemini models, and only when non-empty. Translation needs no deliberation
# — thinking tokens bill at the output rate and never reach the file. Verified honoured
# by gemini-2.5-flash and (partly) gemini-3.6-flash; gemini-3.5-flash and the pro models
# ignore it and think regardless, which is the whole of why they cost what they do.
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


class TransientError(RuntimeError):
    """Rate limit, timeout or 5xx — retry the same request, do not blame the model."""


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
            if exp != got:
                errs.append(f"{code} / {c}: expected {exp} items, got {got}")
        if not str(out.iloc[i]["description"]).strip():
            errs.append(f"{code} / description: empty")
    return errs


# --------------------------------------------------------------------------
# API — OpenAI-compatible /chat/completions
# --------------------------------------------------------------------------

def _build_body(model: str, user_text: str) -> dict:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        "stream": False,
    }
    # The gpt-5 / o-series reject both `max_tokens` and a non-default temperature;
    # everything else on this proxy takes the classic fields.
    if re.match(r"^(gpt-5|o[1-4])", model):
        body["max_completion_tokens"] = MAX_OUTPUT_TOKENS
    else:
        body["max_tokens"] = MAX_OUTPUT_TOKENS
        body["temperature"] = 0
        body["top_p"] = 0.95
    # Only gemini takes this field here; gpt-4o and friends answer 400 for an unknown one.
    if REASONING_EFFORT and model.startswith("gemini"):
        body["reasoning_effort"] = REASONING_EFFORT
    return body


def read_usage(data: dict) -> dict:
    """The four numbers worth keeping out of an OpenAI-compatible `usage` object."""
    u = data.get("usage") or {}
    return {
        "in": int(u.get("prompt_tokens") or 0),
        "out": int(u.get("completion_tokens") or 0),
        "think": int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
        "usd": float(u.get("cost") or 0.0),
    }


def add_usage(total: dict, one: dict) -> None:
    for k in ("in", "out", "think", "usd"):
        total[k] += one[k]


def call_llm(base_url: str, api_key: str, model: str, user_text: str,
             timeout: int = 900) -> tuple[str, dict]:
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    try:
        resp = requests.post(url, headers=headers, json=_build_body(model, user_text),
                             timeout=timeout)
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise TransientError(f"{type(exc).__name__}: {exc}") from exc

    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"non-JSON answer: {resp.text[:300]}") from exc

    # the proxy sometimes reports upstream failures as 200 + an error object
    if isinstance(data.get("error"), dict):
        raise TransientError(f"api error: {str(data['error'])[:300]}")

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"no choices in answer: {str(data)[:300]}")

    usage = read_usage(data)

    choice = choices[0]
    reason = choice.get("finish_reason")
    if reason not in (None, "stop"):
        # `length` here means the CSV was cut off mid-record — never accept it.
        # It is also the expensive failure: the tokens were spent and are unusable,
        # which is why the batch size is capped by the output limit, not by taste.
        raise UsageError(f"finish_reason={reason} (output truncated or blocked)", usage)

    text = (choice.get("message") or {}).get("content") or ""
    if not text.strip():
        raise UsageError("empty response body", usage)
    return text, usage


# --------------------------------------------------------------------------
# one batch
# --------------------------------------------------------------------------

def process_batch(path: Path, out_dir: Path, api_key: str, base_url: str, model: str,
                  retries: int, overwrite: bool) -> dict:
    name = path.stem
    spent = {"in": 0, "out": 0, "think": 0, "usd": 0.0}   # every attempt, failures too
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
    for attempt in range(1, retries + 1):
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
            raw, usage = call_llm(base_url, api_key, model, user_text)
            add_usage(spent, usage)
            out = parse_csv_text(raw)
            out.columns = [str(c).strip() for c in out.columns]
            errs = validate(send, out, cols)
        except TransientError as exc:
            # nothing for the model to fix — wait and send the same request again
            print(f"  [{name}] attempt {attempt}/{retries}: {exc}", file=sys.stderr)
            if attempt < retries:
                time.sleep(min(60, 5 * 2 ** (attempt - 1)) + random.uniform(0, 3))
            continue
        except UsageError as exc:                     # answered, billed, unusable
            add_usage(spent, exc.usage)
            errs = [f"{type(exc).__name__}: {exc}"]
            out = None
        except Exception as exc:                      # parse / API / bad answer
            errs = [f"{type(exc).__name__}: {exc}"]
            out = None

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
              f"(${spent['usd']:.4f} spent so far): {errs[0]}", file=sys.stderr)
        if attempt < retries:
            time.sleep(min(60, 2 ** attempt) + random.uniform(0, 2))

    if not last_errs:
        last_errs = [f"no answer after {retries} attempts (network / rate limit)"]

    # keep the last raw answer so the batch can be inspected by hand
    fail_dir = out_dir / "_failed"
    fail_dir.mkdir(parents=True, exist_ok=True)
    try:
        (fail_dir / f"{name}_raw.txt").write_text(raw, encoding="utf-8")
    except Exception:
        pass
    return {"batch": name, "status": "failed", "errors": last_errs[:20],
            "spent": spent}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    # anchored on this file's directory, so the script runs from anywhere
    ap.add_argument("--in", dest="in_dir", default=str(HERE / "batch_10"))
    ap.add_argument("--out", dest="out_dir", default="",
                    help="default: <in>_fa, beside the input directory")
    ap.add_argument("--api-key", default=API_KEY)
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--workers", type=int, default=3,
                    help="parallel batches; lower this if you hit 429s")
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0,
                    help="only the first N batches — for a trial run")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    if not args.api_key:
        print("error: pass --api-key or set OPENAI_API_KEY", file=sys.stderr)
        return 2

    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir) if args.out_dir else in_dir.parent / f"{in_dir.name}_fa"
    files = sorted(p for p in in_dir.glob("*.xlsx")
                   if not p.name.startswith("~$") and not p.stem.endswith("_fa"))
    if not files:
        print(f"error: no .xlsx files in {in_dir}", file=sys.stderr)
        return 2
    if args.limit:
        files = files[:args.limit]

    print(f"model={args.model}  base_url={args.base_url}\n"
          f"in={in_dir}  out={out_dir}\n"
          f"batches={len(files)}  workers={args.workers}  "
          f"translate_tools={TRANSLATE_TOOLS}  "
          f"reasoning_effort={REASONING_EFFORT or '(unset)'}")
    if args.limit:
        print(f"trial run: {args.limit} batch(es) — multiply the total below by "
              f"{len(list(in_dir.glob('*.xlsx'))) / max(args.limit, 1):.0f} for the whole set")

    results = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_batch, f, out_dir, args.api_key, args.base_url,
                        args.model, args.retries, args.overwrite): f
            for f in files
        }
        for fut in cf.as_completed(futures):
            r = fut.result()
            results.append(r)
            mark = {"ok": "OK", "skipped": "--", "failed": "!!"}[r["status"]]
            sp = r.get("spent") or {}
            cost = (f"  {sp.get('in', 0)}+{sp.get('out', 0)} tok"
                    + (f" ({sp['think']} thinking)" if sp.get("think") else "")
                    + f"  ${sp.get('usd', 0):.4f}") if sp.get("usd") else ""
            print(f"[{mark}] {r['batch']}"
                  + (f"  ({r['rows']} rows, {r['attempts']} attempt(s))"
                     if r["status"] == "ok" else "")
                  + cost)

    results.sort(key=lambda r: r["batch"])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(r["status"] == "ok" for r in results)
    skipped = sum(r["status"] == "skipped" for r in results)
    failed = [r for r in results if r["status"] == "failed"]

    total = {"in": 0, "out": 0, "think": 0, "usd": 0.0}
    for r in results:
        if r.get("spent"):
            add_usage(total, r["spent"])
    rows = sum(r.get("rows", 0) for r in results if r["status"] == "ok")
    print(f"\ntokens: {total['in']:,} in + {total['out']:,} out"
          + (f" (of which {total['think']:,} thinking)" if total["think"] else "")
          + f"\ncost:   ${total['usd']:.4f}"
          + (f"  =  ${total['usd'] / rows:.4f}/row" if rows else "")
          + (f", so ~${total['usd'] / rows * 1017:.2f} for all 1017 rows" if rows else ""))
    print(f"\ndone: {ok} translated, {skipped} skipped, {len(failed)} failed")
    for r in failed:
        print(f"  {r['batch']}: {r['errors'][0]}")
    if failed:
        print("raw answers for failed batches are in", out_dir / "_failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
