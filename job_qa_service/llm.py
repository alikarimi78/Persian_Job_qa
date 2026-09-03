import logging
import threading
import time

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from .config import LLM_API_KEY, LLM_BASE_DELAY, LLM_BASE_URL, LLM_MAX_RETRIES, LLM_MODEL
from .text import clean_markdown

log = logging.getLogger("job_qa_service")


class LLMClient:
    def __init__(self):
        self._client = None
        self._lock = threading.Lock()
        self._json_mode_ok = True

    def __call__(self, messages, temperature=0.3, max_tokens=700, clean=True,
                 json_object=False):
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
                if attempt == LLM_MAX_RETRIES:
                    return ""
                time.sleep(LLM_BASE_DELAY * (2 ** (attempt - 1)))
            except Exception:
                if extra:
                    log.warning("response_format=json_object refused by %s; "
                                "continuing without it for this process", LLM_MODEL)
                    self._json_mode_ok, extra = False, {}
                    continue
                return ""
        return ""
