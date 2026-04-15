# actions/web_search.py
# MARK XXV — Web Search
# Primary: Gemini google_search (yeni google.genai SDK)
# Fallback: DuckDuckGo (ddgs)

import json
import sys
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _gemini_search(query: str) -> str:
    from google import genai

    client = genai.Client(api_key=_get_api_key())
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=query,
        config={"tools": [{"google_search": {}}]}
    )
    text = ""
    for part in response.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            text += part.text
    if not text.strip():
        raise ValueError("Empty response")
    return text.strip()


def _is_quota_error(error: Exception) -> bool:
    text = str(error or "")
    lowered = text.lower()
    return "429" in text or "resource_exhausted" in lowered or "quota" in lowered


def _retry_delay_seconds(error: Exception) -> int | None:
    text = str(error or "")
    marker = "retry in "
    lowered = text.lower()
    idx = lowered.find(marker)
    if idx == -1:
        return None
    tail = lowered[idx + len(marker) :]
    digits = []
    for ch in tail:
        if ch.isdigit():
            digits.append(ch)
        elif digits:
            break
    if not digits:
        return None
    try:
        return int("".join(digits))
    except Exception:
        return None


def _ddg_search(query: str, max_results: int = 6) -> list:
    from ddgs import DDGS
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title":   r.get("title", ""),
                "snippet": r.get("body", ""),
                "url":     r.get("href", ""),
            })
    return results

def _format_ddg(query: str, results: list) -> str:
    if not results:
        return f"No results found for: {query}"
    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        if r.get("title"):   lines.append(f"{i}. {r['title']}")
        if r.get("snippet"): lines.append(f"   {r['snippet']}")
        if r.get("url"):     lines.append(f"   {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()


def _compare(items: list, aspect: str) -> str:
    query = f"Compare {', '.join(items)} in terms of {aspect}. Give specific facts and data."
    try:
        return _gemini_search(query)
    except Exception as e:
        if _is_quota_error(e):
            retry_in = _retry_delay_seconds(e)
            if retry_in:
                print(f"[WebSearch] ⚠️ Gemini compare quota hit, retry suggested in ~{retry_in}s. Falling back to DDG.")
            else:
                print("[WebSearch] ⚠️ Gemini compare quota hit. Falling back to DDG.")
        else:
            print(f"[WebSearch] ⚠️ Gemini compare failed: {e}")
        all_results = {}
        for item in items:
            try:
                all_results[item] = _ddg_search(f"{item} {aspect}", max_results=3)
            except Exception:
                all_results[item] = []
        lines = [f"Comparison — {aspect.upper()}\n{'─'*40}"]
        for item in items:
            lines.append(f"\n▸ {item}")
            for r in all_results.get(item, [])[:2]:
                if r.get("snippet"):
                    lines.append(f"  • {r['snippet']}")
        return "\n".join(lines)


def web_search(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    query  = params.get("query", "").strip()
    mode   = params.get("mode", "search").lower()
    items  = params.get("items", [])
    aspect = params.get("aspect", "general")

    if not query and not items:
        return "Please provide a search query, sir."

    if items and mode != "compare":
        mode = "compare"

    if player:
        player.write_log(f"[Search] {query or ', '.join(items)}")

    print(f"[WebSearch] 🔍 Query: {query!r}  Mode: {mode}")

    try:
        if mode == "compare" and items:
            print(f"[WebSearch] 📊 Comparing: {items}")
            result = _compare(items, aspect)
            print("[WebSearch] ✅ Compare done.")
            return result

        print("[WebSearch] 🌐 Gemini search...")
        try:
            result = _gemini_search(query)
            print("[WebSearch] ✅ Gemini OK.")
            return result
        except Exception as e:
            if _is_quota_error(e):
                retry_in = _retry_delay_seconds(e)
                if retry_in:
                    print(f"[WebSearch] ⚠️ Gemini quota hit, retry suggested in ~{retry_in}s. Switching to DDG...")
                else:
                    print("[WebSearch] ⚠️ Gemini quota hit. Switching to DDG...")
            else:
                print(f"[WebSearch] ⚠️ Gemini failed ({e}), trying DDG...")
            results = _ddg_search(query)
            result  = _format_ddg(query, results)
            print(f"[WebSearch] ✅ DDG: {len(results)} results.")
            return result

    except Exception as e:
        print(f"[WebSearch] ❌ Failed: {e}")
        return f"Search failed, sir: {e}"
