# -*- coding: utf-8 -*-
"""The OpenAI-compatible chat call, with the degradation the engine depends on.

Every failure path returns `""` rather than raising — no API key, a refused request,
an exhausted retry budget. Each caller in `engine.py` treats an empty reply as "fall
back to the plain-text template", which is what keeps `/search` answering when the
API is down.
"""

import logging
import threading
import time

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from .config import LLM_API_KEY, LLM_BASE_DELAY, LLM_BASE_URL, LLM_MAX_RETRIES, LLM_MODEL
from .text import clean_markdown

log = logging.getLogger("job_qa_service")


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
        # Whether the provider takes response_format={"type": "json_object"}. Assumed
        # until a request carrying it fails for a reason that is not transient, then
        # remembered for the life of the process — the parameter is an upgrade, not a
        # dependency, and a provider that refuses it must cost one wasted request ever
        # rather than one per call (or worse, every generation quietly answering
        # DISCOVERY_UNAVAILABLE because a 400 looked like an outage).
        self._json_mode_ok = True

    def __call__(self, messages, temperature=0.3, max_tokens=700, clean=True,
                 json_object=False):
        """API call with exponential backoff; returns '' on failure (caller falls
        back to the template answer). `clean=False` keeps the reply verbatim, which
        JSON replies need since markdown stripping would corrupt them.

        `json_object=True` also asks the API to *constrain decoding* to one JSON
        object (measured against the deployed endpoint: accepted, all ten keys
        intact). That turns "the reply parses" from a property of the prompt into a
        property of the request — the second line of defence behind
        `parse_json_object`, not a replacement for it, since what the object *says*
        is still checked by every caller. OpenAI requires the word "JSON" in the
        messages for it; both prompts that set this carry it."""
        if not LLM_API_KEY:
            return ""
        with self._lock:
            if self._client is None:
                self._client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

        extra = ({"response_format": {"type": "json_object"}}
                 if json_object and self._json_mode_ok else {})
        for attempt in range(1, LLM_MAX_RETRIES + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=LLM_MODEL, messages=messages,
                    temperature=temperature, max_tokens=max_tokens, **extra)
                text = (resp.choices[0].message.content or "").strip()
                return clean_markdown(text) if clean else text
            except (RateLimitError, APIConnectionError, APITimeoutError):
                # Transient either way: response_format is not what a rate limit or a
                # dropped connection is about, so it stays for the retry.
                if attempt == LLM_MAX_RETRIES:
                    return ""
                time.sleep(LLM_BASE_DELAY * (2 ** (attempt - 1)))
            except Exception:
                if extra:
                    # A non-transient refusal of a request that carried the one
                    # optional thing in it: assume the parameter (a provider or a
                    # swapped LLM_MODEL that predates json_object answers 400) and
                    # retry now, without it and without sleeping — this is not a rate
                    # problem. Remembered so later calls skip the doomed parameter.
                    log.warning("response_format=json_object refused by %s; "
                                "continuing without it for this process", LLM_MODEL)
                    self._json_mode_ok, extra = False, {}
                    continue
                return ""
        return ""
