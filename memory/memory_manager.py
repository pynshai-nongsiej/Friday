import json
from threading import Lock
from pathlib import Path
import sys


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR    = get_base_dir()
MEMORY_PATH = BASE_DIR / "memory" / "long_term.json"
CONVERSATION_HISTORY_PATH = BASE_DIR / "memory" / "conversation_history.json"
_lock       = Lock()

MAX_VALUE_LENGTH = 300  

def _empty_memory() -> dict:
    return {
        "identity":      {},
        "preferences":   {},
        "relationships": {},
        "notes":         {},
        "relationship_profile": {},
    }

def load_memory() -> dict:
    if not MEMORY_PATH.exists():
        return _empty_memory()

    with _lock:
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
            return _empty_memory()
        except Exception as e:
            print(f"[Memory] ⚠️ Load error: {e}")
            return _empty_memory()


def save_memory(memory: dict) -> None:
    if not isinstance(memory, dict):
        return

    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)

    with _lock:
        MEMORY_PATH.write_text(
            json.dumps(memory, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )


def load_conversation_history(limit: int | None = None) -> list[dict]:
    if not CONVERSATION_HISTORY_PATH.exists():
        return []

    with _lock:
        try:
            data = json.loads(CONVERSATION_HISTORY_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
        except Exception as e:
            print(f"[Memory] ⚠️ Conversation history load error: {e}")
            return []

    if limit is not None and limit > 0:
        return data[-limit:]
    return data


def append_conversation_turn(user_text: str, assistant_text: str) -> None:
    user_text = (user_text or "").strip()
    assistant_text = (assistant_text or "").strip()
    if not user_text and not assistant_text:
        return

    history = load_conversation_history()
    history.append(
        {
            "timestamp": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
            "user": _truncate_value(user_text),
            "assistant": _truncate_value(assistant_text),
        }
    )
    history = history[-40:]

    CONVERSATION_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        CONVERSATION_HISTORY_PATH.write_text(
            json.dumps(history, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def _recursive_update(target: dict, updates: dict) -> bool:
    changed = False

    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue

        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value):
                changed = True
        else:
            if isinstance(value, dict) and "value" in value:
                entry = {"value": _truncate_value(str(value["value"]))}
            else:
                entry = {"value": _truncate_value(str(value))}

            if key not in target or target[key] != entry:
                target[key] = entry
                changed = True

    return changed


def update_memory(memory_update: dict) -> dict:

    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()

    memory = load_memory()

    if _recursive_update(memory, memory_update):
        save_memory(memory)
        print(f"[Memory] 💾 Saved: {list(memory_update.keys())}")

    return memory


def update_relationship_profile(profile_update: dict) -> dict:
    if not isinstance(profile_update, dict) or not profile_update:
        return load_memory()
    return update_memory({"relationship_profile": profile_update})



def format_memory_for_prompt(memory: dict | None) -> str:
    if not memory:
        return ""

    lines = []

    # Identity
    identity = memory.get("identity", {})
    name = identity.get("name", {}).get("value")
    age  = identity.get("age",  {}).get("value")
    bday = identity.get("birthday", {}).get("value")
    city = identity.get("city", {}).get("value")
    if name: lines.append(f"Name: {name}")
    if age:  lines.append(f"Age: {age}")
    if bday: lines.append(f"Birthday: {bday}")
    if city: lines.append(f"City: {city}")

    prefs = memory.get("preferences", {})
    for i, (key, entry) in enumerate(prefs.items()):
        if i >= 5:
            break
        val = entry.get("value") if isinstance(entry, dict) else entry
        if val:
            lines.append(f"{key.replace('_', ' ').title()}: {val}")

    rels = memory.get("relationships", {})
    for i, (key, entry) in enumerate(rels.items()):
        if i >= 5:
            break
        val = entry.get("value") if isinstance(entry, dict) else entry
        if val:
            lines.append(f"{key.title()}: {val}")

    notes = memory.get("notes", {})
    for i, (key, entry) in enumerate(notes.items()):
        if i >= 5:
            break
        val = entry.get("value") if isinstance(entry, dict) else entry
        if val:
            lines.append(f"{key}: {val}")

    relationship_profile = memory.get("relationship_profile", {})
    for i, (key, entry) in enumerate(relationship_profile.items()):
        if i >= 6:
            break
        val = entry.get("value") if isinstance(entry, dict) else entry
        if val:
            lines.append(f"{key.replace('_', ' ').title()}: {val}")

    if not lines:
        return ""

    result = "[USER MEMORY]\n" + "\n".join(f"- {l}" for l in lines)
    if len(result) > 800:
        result = result[:797] + "…"

    return result + "\n"


def format_recent_conversations_for_prompt(limit: int = 6) -> str:
    history = load_conversation_history(limit=limit)
    if not history:
        return ""

    lines = []
    for item in history[-limit:]:
        user = (item.get("user") or "").strip()
        assistant = (item.get("assistant") or "").strip()
        if user:
            lines.append(f"User: {user}")
        if assistant:
            lines.append(f"Assistant: {assistant}")

    if not lines:
        return ""

    result = "[RECENT CONVERSATION CONTEXT]\n" + "\n".join(f"- {line}" for line in lines[-10:])
    if len(result) > 1200:
        result = result[:1197] + "…"
    return result + "\n"
