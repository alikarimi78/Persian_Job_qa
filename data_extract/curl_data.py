import time
from typing import Optional, Any, List

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from tqdm import tqdm


def make_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.headers.update({
        "User-Agent": "job-analysis-research/1.0 (contact: you@example.com)",
    })
    return s


def pick_lang_value(v: Any, lang: str = "en") -> Optional[str]:
    """
    ESCO JSON-LD values often look like:
    [{"@language":"en","@value":"..."}] or dicts/strings.
    """
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, dict):
        # {"@value": "...", "@language": "en"}
        if v.get("@language") == lang and "@value" in v:
            return str(v["@value"]).strip() or None
        if "@value" in v:
            return str(v["@value"]).strip() or None
    if isinstance(v, list):
        # prefer requested language
        for item in v:
            if isinstance(item, dict) and item.get("@language") == lang and "@value" in item:
                val = str(item["@value"]).strip()
                if val:
                    return val
        # fallback any
        for item in v:
            got = pick_lang_value(item, lang=lang)
            if got:
                return got
    return None


def scrape_html_description(session: requests.Session, url: str, timeout: int = 30) -> Optional[str]:
    """
    Fallback: scrape HTML meta/sections. This is heuristic and may need tuning.
    """
    r = session.get(url, headers={"Accept": "text/html, */*;q=0.1"}, timeout=timeout, allow_redirects=True)
    if r.status_code >= 400 or not r.text:
        return None

    soup = BeautifulSoup(r.text, "html.parser")

    # 1) meta description / og:description
    for sel in [
        ('meta', {'property': 'og:description'}),
        ('meta', {'name': 'description'}),
    ]:
        tag = soup.find(*sel)
        if tag and tag.get("content"):
            txt = tag["content"].strip()
            if txt:
                return txt

    # 2) Look for a heading "Description" and take the next paragraph(s)
    headings = soup.find_all(["h1", "h2", "h3", "h4"])
    for h in headings:
        if h.get_text(strip=True).lower() == "description":
            # collect nearby paragraphs
            parts = []
            sib = h.find_next_sibling()
            steps = 0
            while sib is not None and steps < 8:
                if sib.name in ["p", "div", "section"]:
                    ptxt = sib.get_text(" ", strip=True)
                    if ptxt:
                        parts.append(ptxt)
                # stop if we hit another major heading
                if sib.name in ["h1", "h2", "h3"]:
                    break
                sib = sib.find_next_sibling()
                steps += 1
            joined = " ".join(parts).strip()
            if joined:
                return joined
    # 3) last resort: first meaningful paragraph
    p = soup.find("p")
    if p:
        txt = p.get_text(" ", strip=True)
        if txt:
            return txt

    return None


def detect_uri_column(df: pd.DataFrame) -> str:
    """
    Try to guess the best column that contains the ESCO URI / URL.
    Adjust if your file uses different names.
    """
    candidates = [
        "conceptUri", "concept_uri", "uri", "URI",
        "occupationUri", "occupation_uri",
        "description", "descriptionUri", "description_uri",
        "link", "url", "URL"
    ]
    for c in candidates:
        if c in df.columns:
            return c
    raise ValueError(f"Could not detect URI/URL column. Available columns: {list(df.columns)[:30]} ...")


def main(
    occupations_csv_path: str,
    out_csv_path: str = "occupations_with_description_en.csv",
    lang: str = "en",
    sleep_seconds: float = 0.25,
    limit: Optional[int] = None
):
    df = pd.read_csv(occupations_csv_path, dtype=str, keep_default_na=False)
    uri_col = detect_uri_column(df)

    session = make_session()

    descriptions: List[str] = []
    it = df.itertuples(index=False)
    total = len(df) if limit is None else min(limit, len(df))

    for i, row in enumerate(tqdm(list(it)[:total], total=total)):
        uri = getattr(row, uri_col)

        desc = None
        if uri:
            desc = scrape_html_description(session, uri)

        descriptions.append(desc or "")

        # be polite to the server
        time.sleep(sleep_seconds)

    df = df.iloc[:total].copy()
    df["description_en"] = descriptions
    df.to_csv(out_csv_path, index=False)
    print(f"Saved: {out_csv_path}  | rows: {len(df)}  | uri_col: {uri_col}")


if __name__ == "__main__":
    # مثال اجرا:
    # main("occupations_en.csv", out_csv_path="occupations_with_description_en.csv", sleep_seconds=0.3)
    main("data_csv_esco/occupations_en.csv")