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
    from memory.memory_db import get_long_term
    db_mem = get_long_term()
    
    # Merge with empty memory to ensure all keys exist
    mem = _empty_memory()
    for cat, entries in db_mem.items():
        if cat in mem:
            mem[cat].update(entries)
        else:
            mem[cat] = entries
    return mem


def save_memory(memory: dict) -> None:
    from memory.memory_db import update_long_term
    for category, entries in memory.items():
        if isinstance(entries, dict):
            for key, entry in entries.items():
                val = entry.get("value", "") if isinstance(entry, dict) else str(entry)
                update_long_term(category, key, val)


def load_conversation_history(limit: int | None = None) -> list[dict]:
    from memory.memory_db import get_conversation_history
    return get_conversation_history(limit=limit)


def append_conversation_turn(user_text: str, assistant_text: str) -> None:
    user_text = (user_text or "").strip()
    assistant_text = (assistant_text or "").strip()
    if not user_text and not assistant_text:
        return

    from memory.memory_db import append_conversation
    append_conversation(_truncate_value(user_text), _truncate_value(assistant_text))

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
