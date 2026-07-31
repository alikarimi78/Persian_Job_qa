# -*- coding: utf-8 -*-
"""Text utilities shared across the engine: normalization, Markdown stripping,
tolerant JSON extraction, and the corpus digest the embedding cache is keyed on."""

import hashlib
import json
import re

import pandas as pd

from .config import EMBED_MODEL_NAME

try:
    from hazm import Normalizer
    _normalizer = Normalizer()
except Exception:
    _normalizer = None

_MD_PATTERNS = [(re.compile(r"\*\*(.*?)\*\*"), r"\1"), (re.compile(r"\*(.*?)\*"), r"\1"),
                (re.compile(r"`(.*?)`"), r"\1"), (re.compile(r"#{1,6}\s?"), "")]


def normalize_text(text):
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    text = str(text).replace("ي", "ی").replace("ك", "ک")
    text = " ".join(text.split())
    if _normalizer:
        text = _normalizer.normalize(text)
    return text.strip()


def clean_markdown(text):
    for pattern, repl in _MD_PATTERNS:
        text = pattern.sub(repl, text)
    return text.strip()


def parse_json_object(text):
    """Extracts the first JSON object from an LLM reply; None if there is none.
    Tolerates the code fences and stray prose some models add around JSON."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def corpus_fingerprint(*text_groups):
    """Short digest of the embedding model plus every text that gets encoded. The
    embedding cache is keyed on it, so editing a record or changing how combined_text
    is assembled misses the cache instead of silently reusing the old vectors."""
    digest = hashlib.sha256(EMBED_MODEL_NAME.encode("utf-8"))
    for texts in text_groups:
        for text in texts:
            digest.update(text.encode("utf-8"))
            digest.update(b"\x1f")
        digest.update(b"\x1e")
    return digest.hexdigest()[:12]
