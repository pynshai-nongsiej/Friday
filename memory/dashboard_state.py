import imaplib
import json
import re
import subprocess
import sys
from datetime import datetime
from email import message_from_bytes
from email.header import decode_header
from pathlib import Path
from typing import Any

from memory.memory_manager import load_conversation_history, load_memory


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
MEMORY_DIR = BASE_DIR / "memory"

INTEGRATIONS_PATH = CONFIG_DIR / "integrations.json"
PERSONALITY_PATH = MEMORY_DIR / "personality.json"
CALENDAR_CACHE_PATH = MEMORY_DIR / "calendar_events.json"
EMAIL_CACHE_PATH = MEMORY_DIR / "email_digest.json"
REMINDERS_PATH = MEMORY_DIR / "reminders.json"


DEFAULT_INTEGRATIONS = {
    "calendar": {
        "enabled": False,
        "ics_paths": [],
    },
    "documents": {
        "enabled": True,
        "watch_folder": str((BASE_DIR / "briefing_drop").resolve()),
    },
    "email": {
        "enabled": False,
        "imap_host": "",
        "email": "",
        "password": "",
        "mailbox": "INBOX",
        "max_items": 5,
    },
}

DEFAULT_PERSONALITY = {
    "mode": "witty",
    "description": "Warm, sharp, lightly humorous, and professional.",
}


def _ensure_json_file(path: Path, default_data: Any) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(default_data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_json(path: Path, default: Any) -> Any:
    _ensure_json_file(path, default)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, type(default)) else default
    except Exception:
        return default


def load_personality_mode() -> dict:
    data = _load_json(PERSONALITY_PATH, DEFAULT_PERSONALITY)
    mode = str(data.get("mode", "witty")).strip().lower()
    if mode not in {"formal", "witty", "playful"}:
        mode = "witty"
    return {
        "mode": mode,
        "description": str(data.get("description") or DEFAULT_PERSONALITY["description"]).strip(),
    }


def set_personality_mode(mode: str) -> dict:
    mode = (mode or "").strip().lower()
    if mode not in {"formal", "witty", "playful"}:
        raise ValueError("mode must be one of: formal, witty, playful")
    descriptions = {
        "formal": "Polished, restrained, highly professional, and direct.",
        "witty": "Warm, sharp, lightly humorous, and professional.",
        "playful": "More energetic, friendly, and playful while still competent.",
    }
    data = {"mode": mode, "description": descriptions[mode]}
    PERSONALITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    PERSONALITY_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def load_integrations_config() -> dict:
    return _load_json(INTEGRATIONS_PATH, DEFAULT_INTEGRATIONS)


def _frontmost_app_macos() -> str:
    script = 'tell application "System Events" to get name of first application process whose frontmost is true'
    try:
        return subprocess.check_output(["osascript", "-e", script], text=True).strip()
    except Exception:
        return ""


def get_desktop_awareness() -> dict:
    app_name = ""
    if sys.platform == "darwin":
        app_name = _frontmost_app_macos()

    normalized = app_name.lower()
    suggestion = "No active desktop context detected yet, boss."
    inferred_scene = "personal"
    if any(key in normalized for key in ("code", "cursor", "xcode", "terminal", "iterm", "pycharm")):
        inferred_scene = "coding"
        suggestion = "You appear to be coding. I can help with debugging, refactors, or project briefings."
    elif any(key in normalized for key in ("chrome", "safari", "firefox", "edge")):
        inferred_scene = "work"
        suggestion = "You appear to be in a browser workflow. I can brief tabs, tasks, or research context."
    elif any(key in normalized for key in ("mail", "outlook")):
        inferred_scene = "briefing"
        suggestion = "You appear to be reviewing mail. I can summarize inbox priorities and next actions."
    elif any(key in normalized for key in ("maps", "flight", "travel")):
        inferred_scene = "travel"
        suggestion = "You appear to be in travel planning. I can surface flights, reminders, and itinerary context."

    return {
        "active_app": app_name or "Unknown",
        "scene_hint": inferred_scene,
        "suggestion": suggestion,
    }


def load_document_briefing(limit: int = 5) -> dict:
    config = load_integrations_config().get("documents", {})
    folder = Path(str(config.get("watch_folder") or (BASE_DIR / "briefing_drop"))).expanduser()
    folder.mkdir(parents=True, exist_ok=True)

    files = []
    for pattern in ("*.pdf", "*.ppt", "*.pptx", "*.key", "*.docx", "*.txt", "*.md"):
        files.extend(folder.glob(pattern))

    files = sorted(files, key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    docs = []
    for path in files[:limit]:
        try:
            stat = path.stat()
            docs.append(
                {
                    "name": path.name,
                    "kind": path.suffix.lower().lstrip(".") or "file",
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "size_kb": max(1, int(stat.st_size / 1024)),
                }
            )
        except Exception:
            continue

    summary = "No briefing documents found in the watched folder, boss."
    if docs:
        newest = docs[0]
        summary = (
            f"Newest briefing document: {newest['name']} "
            f"({newest['kind'].upper()}, {newest['size_kb']} KB, updated {newest['modified']})."
        )
    return {
        "folder": str(folder),
        "documents": docs,
        "summary": summary,
    }


def infer_scene_mode(briefing: dict, desktop: dict) -> dict:
    scene = desktop.get("scene_hint", "personal")
    reason = desktop.get("suggestion", "")

    documents = briefing.get("document_briefing", {}).get("documents", [])
    reminders = briefing.get("reminders", [])
    calendar_events = briefing.get("calendar_events", [])

    if documents:
        scene = "briefing"
        reason = f"Document briefing mode active from {documents[0]['name']}."
    elif calendar_events or reminders:
        scene = "work"
        reason = reason or "Work mode active based on reminders and schedule."

    if not reason:
        reason = "Personal chat mode active."

    label_map = {
        "coding": "CODING",
        "work": "WORK",
        "travel": "TRAVEL",
        "briefing": "BRIEFING",
        "personal": "PERSONAL",
    }
    return {
        "mode": scene,
        "label": label_map.get(scene, scene.upper()),
        "reason": reason,
    }


def _decode_email_header(raw_value: str | bytes | None) -> str:
    if not raw_value:
        return ""
    parts = decode_header(raw_value)
    decoded = []
    for value, encoding in parts:
        if isinstance(value, bytes):
            decoded.append(value.decode(encoding or "utf-8", errors="ignore"))
        else:
            decoded.append(str(value))
    return "".join(decoded).strip()


def _parse_ics_datetime(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_ics_events(content: str) -> list[dict]:
    events = []
    chunks = content.split("BEGIN:VEVENT")
    for chunk in chunks[1:]:
        summary_match = re.search(r"\nSUMMARY:(.+)", chunk)
        start_match = re.search(r"\nDTSTART(?:;[^:]+)?:([^\n\r]+)", chunk)
        if not summary_match or not start_match:
            continue
        start_dt = _parse_ics_datetime(start_match.group(1))
        if not start_dt:
            continue
        events.append(
            {
                "title": summary_match.group(1).strip(),
                "when": start_dt.strftime("%Y-%m-%d %H:%M"),
                "timestamp": start_dt.timestamp(),
            }
        )
    return events


def load_calendar_events(limit: int = 5) -> list[dict]:
    config = load_integrations_config().get("calendar", {})
    events = []

    if config.get("enabled"):
        for raw_path in config.get("ics_paths", []):
            path = Path(str(raw_path)).expanduser()
            if not path.exists():
                continue
            try:
                events.extend(_parse_ics_events(path.read_text(encoding="utf-8", errors="ignore")))
            except Exception:
                continue

    if not events:
        cached = _load_json(CALENDAR_CACHE_PATH, [])
        for item in cached:
            if isinstance(item, dict):
                events.append(
                    {
                        "title": str(item.get("title", "")).strip(),
                        "when": str(item.get("when", "")).strip(),
                        "timestamp": float(item.get("timestamp", 0) or 0),
                    }
                )

    events = [e for e in events if e.get("title")]
    events.sort(key=lambda item: item.get("timestamp", 0) or item.get("when", ""))
    return events[:limit]


def load_email_digest(limit: int = 5) -> list[dict]:
    config = load_integrations_config().get("email", {})
    messages = []

    if config.get("enabled") and config.get("imap_host") and config.get("email") and config.get("password"):
        try:
            mailbox = str(config.get("mailbox", "INBOX"))
            max_items = int(config.get("max_items", limit) or limit)
            with imaplib.IMAP4_SSL(str(config["imap_host"])) as imap:
                imap.login(str(config["email"]), str(config["password"]))
                imap.select(mailbox)
                status, data = imap.search(None, "ALL")
                if status == "OK" and data and data[0]:
                    ids = data[0].split()[-max_items:]
                    for msg_id in reversed(ids):
                        status, msg_data = imap.fetch(msg_id, "(RFC822)")
                        if status != "OK" or not msg_data:
                            continue
                        raw_bytes = msg_data[0][1]
                        msg = message_from_bytes(raw_bytes)
                        subject = _decode_email_header(msg.get("Subject"))
                        sender = _decode_email_header(msg.get("From"))
                        messages.append(
                            {
                                "subject": subject,
                                "from": sender,
                            }
                        )
        except Exception:
            messages = []

    if not messages:
        cached = _load_json(EMAIL_CACHE_PATH, [])
        for item in cached:
            if isinstance(item, dict):
                messages.append(
                    {
                        "subject": str(item.get("subject", "")).strip(),
                        "from": str(item.get("from", "")).strip(),
                    }
                )

    return [m for m in messages if m.get("subject")][:limit]


def load_reminders(limit: int = 5) -> list[dict]:
    reminders = _load_json(REMINDERS_PATH, [])
    items = []
    for item in reminders:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "message": str(item.get("message", "")).strip(),
                "when": str(item.get("when", "")).strip(),
                "status": str(item.get("status", "scheduled")).strip().upper(),
            }
        )
    return items[:limit]


def build_skills_dashboard() -> dict:
    memory = load_memory()
    history = load_conversation_history(limit=20)
    prefs = memory.get("preferences", {})
    profile = memory.get("relationship_profile", {})

    ongoing_projects = profile.get("ongoing_projects", {}).get("value", "")
    open_loops = profile.get("open_loops", {}).get("value", "")
    support_style = profile.get("support_style", {}).get("value", "")

    top_prefs = []
    for key, entry in list(prefs.items())[:4]:
        value = entry.get("value") if isinstance(entry, dict) else entry
        if value:
            top_prefs.append(f"{key.replace('_', ' ')}: {value}")

    recent_topics = []
    for item in history[-8:]:
        user = str(item.get("user", "")).strip()
        if user:
            recent_topics.append(user[:80])
    return {
        "ongoing_projects": ongoing_projects or "No ongoing project summary yet.",
        "open_loops": open_loops or "No open loops captured yet.",
        "support_style": support_style or "Adaptive support mode.",
        "preferences": top_prefs or ["No explicit preferences saved yet."],
        "recent_topics": recent_topics[-3:] or ["No recent topics captured yet."],
    }


def build_daily_briefing() -> dict:
    reminders = load_reminders(limit=4)
    calendar_events = load_calendar_events(limit=4)
    email_digest = load_email_digest(limit=4)
    document_briefing = load_document_briefing(limit=5)
    desktop = get_desktop_awareness()
    skills = build_skills_dashboard()
    personality = load_personality_mode()

    summary_bits = []
    if reminders:
        first = reminders[0]
        summary_bits.append(f"Top reminder: {first['message']} at {first['when']}")
    if calendar_events:
        first = calendar_events[0]
        summary_bits.append(f"Next event: {first['title']} at {first['when']}")
    if email_digest:
        summary_bits.append(f"You have {len(email_digest)} recent email items to review")
    if document_briefing.get("documents"):
        summary_bits.append(f"Briefing folder has {len(document_briefing['documents'])} active documents")
    if skills.get("ongoing_projects"):
        summary_bits.append(f"Project focus: {skills['ongoing_projects']}")
    if desktop.get("active_app") and desktop.get("active_app") != "Unknown":
        summary_bits.append(f"Current app: {desktop['active_app']}")

    summary = ". ".join(summary_bits[:4]).strip()
    if summary and not summary.endswith("."):
        summary += "."

    scene = infer_scene_mode(
        {
            "reminders": reminders,
            "calendar_events": calendar_events,
            "document_briefing": document_briefing,
        },
        desktop,
    )

    return {
        "summary": summary or "No urgent briefing items yet, boss.",
        "reminders": reminders,
        "calendar_events": calendar_events,
        "email_digest": email_digest,
        "document_briefing": document_briefing,
        "desktop": desktop,
        "scene": scene,
        "skills": skills,
        "personality": personality,
    }
