# -*- coding: utf-8 -*-
"""The OpenAI-compatible chat call, with the degradation the engine depends on.

Every failure path returns `""` rather than raising — no API key, a refused request,
an exhausted retry budget. Each caller in `engine.py` treats an empty reply as "fall
back to the plain-text template", which is what keeps `/search` answering when the
API is down.
"""

import threading
import time

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from .config import LLM_API_KEY, LLM_BASE_DELAY, LLM_BASE_URL, LLM_MAX_RETRIES, LLM_MODEL
from .text import clean_markdown


class LLMClient:
    """Callable wrapper around one lazily-created OpenAI client.

    The client is built on first use, not at construction: an engine can be built and
    serve template answers with no API key present at all — and under a lock, because
    `engine._answer_and_select` deliberately calls this from two threads at once and
    two of them arriving on an unbuilt client would each construct one. The underlying
    object is thread-safe for requests, so only the construction is guarded.
    """

    def __init__(self):
        self._client = None
        self._lock = threading.Lock()

    def __call__(self, messages, temperature=0.3, max_tokens=700, clean=True):
        """API call with exponential backoff; returns '' on failure (caller falls
        back to the template answer). `clean=False` keeps the reply verbatim, which
        JSON replies need since markdown stripping would corrupt them."""
        if not LLM_API_KEY:
            return ""
        with self._lock:
            if self._client is None:
                self._client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

        for attempt in range(1, LLM_MAX_RETRIES + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=LLM_MODEL, messages=messages,
                    temperature=temperature, max_tokens=max_tokens)
                text = (resp.choices[0].message.content or "").strip()
                return clean_markdown(text) if clean else text
            except (RateLimitError, APIConnectionError, APITimeoutError):
                if attempt == LLM_MAX_RETRIES:
                    return ""
                time.sleep(LLM_BASE_DELAY * (2 ** (attempt - 1)))
            except Exception:
                return ""
        return ""
