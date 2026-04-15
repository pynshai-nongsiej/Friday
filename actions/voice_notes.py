import json
import sys
from datetime import datetime
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent.parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
VOICE_NOTES_PATH = BASE_DIR / "memory" / "voice_notes.json"


def _load_notes() -> list[dict]:
    if not VOICE_NOTES_PATH.exists():
        return []
    try:
        data = json.loads(VOICE_NOTES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_notes(notes: list[dict]) -> None:
    VOICE_NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    VOICE_NOTES_PATH.write_text(
        json.dumps(notes[-100:], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _simple_summary(text: str, limit: int = 140) -> str:
    text = " ".join((text or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def voice_notes(parameters: dict, player=None) -> str:
    action = str(parameters.get("action", "save_note")).strip().lower()
    content = str(parameters.get("content", "")).strip()

    notes = _load_notes()

    if action == "save_note":
        if not content:
            return "I need the note content to save a voice note, boss."
        note = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content": content,
            "summary": _simple_summary(content),
        }
        notes.append(note)
        _save_notes(notes)
        if player:
            player.write_log(f"[VoiceNote] saved: {note['summary']}")
        return f"Voice note saved, boss. Summary: {note['summary']}"

    if action == "list_notes":
        if not notes:
            return "You do not have any saved voice notes yet, boss."
        top = notes[-5:]
        joined = " | ".join(
            f"{item.get('timestamp', '')}: {item.get('summary', '')}" for item in top
        )
        return f"Your latest voice notes are: {joined}"

    if action == "summary":
        if not notes:
            return "You do not have any saved voice notes to summarize yet, boss."
        combined = " ".join(item.get("content", "") for item in notes[-8:]).strip()
        summary = _simple_summary(combined, limit=220)
        return f"Here is the current voice note summary, boss: {summary}"

    return "Unknown voice note action, boss."
