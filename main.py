import asyncio
import audioop
import threading
import json
import re
import sys
import traceback
import hashlib
from pathlib import Path

import pyaudio
from google import genai
from google.genai import types
import time
from ui import JarvisUI
from memory.memory_manager import (
    load_memory,
    update_memory,
    format_memory_for_prompt,
    append_conversation_turn,
    format_recent_conversations_for_prompt,
)
from memory.dashboard_state import build_daily_briefing, load_personality_mode
from memory.dashboard_state import set_personality_mode

from agent.task_queue import get_queue

from actions.flight_finder import flight_finder
from actions.open_app import open_app
from actions.weather_report import weather_action
from actions.send_message import send_message
from actions.reminder import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor import screen_process
from actions.youtube_video import youtube_video
from actions.cmd_control import cmd_control
from actions.desktop import desktop_control
from actions.browser_control import browser_control
from actions.file_controller import file_controller
from actions.code_helper import code_helper
from actions.dev_agent import dev_agent
from actions.web_search import web_search as web_search_action
from actions.computer_control import computer_control
from actions.voice_notes import voice_notes


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH = BASE_DIR / "core" / "prompt.txt"
REMINDERS_PATH = BASE_DIR / "memory" / "reminders.json"
LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

pya = pyaudio.PyAudio()


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]

    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are FRIDAY, Tony Stark's AI assistant. "
            "The user is your creator. "
            "Always address the user as boss in every reply. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )


_memory_turn_counter = 0
_memory_turn_lock = threading.Lock()
_MEMORY_EVERY_N_TURNS = 5
_last_memory_input = ""
_relationship_turn_counter = 0
_relationship_turn_lock = threading.Lock()
_RELATIONSHIP_EVERY_N_TURNS = 6
_last_relationship_signature = ""


def _update_memory_async(user_text: str, jarvis_text: str) -> None:
    """
    Multilingual memory updater.
    Model  : gemini-2.5-flash-lite (lowest cost)
    Stage 1: Quick YES/NO check  → ~5 tokens output
    Stage 2: Full extraction     → only if Stage 1 says YES
    Result : ~80% fewer API calls vs original
    """
    global _memory_turn_counter, _last_memory_input

    with _memory_turn_lock:
        _memory_turn_counter += 1
        current_count = _memory_turn_counter

    if current_count % _MEMORY_EVERY_N_TURNS != 0:
        return

    text = user_text.strip()
    if len(text) < 10:
        return
    if text == _last_memory_input:
        return
    _last_memory_input = text

    try:
        import google.generativeai as genai

        genai.configure(api_key=_get_api_key())
        model = genai.GenerativeModel("gemini-2.5-flash-lite")

        check = model.generate_content(
            f"Does this message contain personal facts about the user "
            f"(name, age, city, job, hobby, relationship, birthday, preference)? "
            f"Reply only YES or NO.\n\nMessage: {text[:300]}"
        )
        if "YES" not in check.text.upper():
            return

        raw = model.generate_content(
            f"Extract personal facts from this message. Any language.\n"
            f"Return ONLY valid JSON or {{}} if nothing found.\n"
            f"Extract: name, age, birthday, city, job, hobbies, preferences, relationships, language.\n"
            f"Skip: weather, reminders, search results, commands.\n\n"
            f"Format:\n"
            f'{{"identity":{{"name":{{"value":"..."}}}}}}, '
            f'"preferences":{{"hobby":{{"value":"..."}}}}, '
            f'"notes":{{"job":{{"value":"..."}}}}}}\n\n'
            f"Message: {text[:500]}\n\nJSON:"
        ).text.strip()

        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        if not raw or raw == "{}":
            return

        data = json.loads(raw)
        if data:
            update_memory(data)
            print(f"[Memory] ✅ Updated: {list(data.keys())}")

    except json.JSONDecodeError:
        pass
    except Exception as e:
        if "429" not in str(e):
            print(f"[Memory] ⚠️ {e}")


def _update_relationship_memory_async() -> None:
    global _relationship_turn_counter, _last_relationship_signature

    with _relationship_turn_lock:
        _relationship_turn_counter += 1
        current_count = _relationship_turn_counter

    if current_count % _RELATIONSHIP_EVERY_N_TURNS != 0:
        return

    try:
        from memory.memory_manager import load_conversation_history

        history = load_conversation_history(limit=18)
        if len(history) < 6:
            return

        compact_history = [
            {
                "user": (item.get("user") or "").strip(),
                "assistant": (item.get("assistant") or "").strip(),
            }
            for item in history
            if (item.get("user") or "").strip() or (item.get("assistant") or "").strip()
        ]
        if len(compact_history) < 6:
            return

        signature = hashlib.sha1(
            json.dumps(compact_history, ensure_ascii=False, sort_keys=True).encode(
                "utf-8"
            )
        ).hexdigest()
        if signature == _last_relationship_signature:
            return

        import google.generativeai as genai

        genai.configure(api_key=_get_api_key())
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        raw = model.generate_content(
            "You are compressing older chats into durable relationship memory.\n"
            "Infer only stable, useful, human-like long-lived context from the conversation history.\n"
            "Do not restate temporary requests unless they reflect a recurring pattern.\n"
            "Return ONLY valid JSON. Keep each value under 140 characters.\n"
            "If something is unknown, omit it.\n\n"
            "JSON format:\n"
            "{"
            '"relationship_profile":{'
            '"bond_style":{"value":"..."},'
            '"support_style":{"value":"..."},'
            '"recurring_goals":{"value":"..."},'
            '"important_people":{"value":"..."},'
            '"ongoing_projects":{"value":"..."},'
            '"open_loops":{"value":"..."}'
            "},"
            '"notes":{"relationship_summary":{"value":"..."}}'
            "}\n\n"
            f"Conversation history:\n{json.dumps(compact_history, ensure_ascii=False)}\n\nJSON:"
        ).text.strip()

        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        if not raw or raw == "{}":
            return

        data = json.loads(raw)
        if isinstance(data, dict) and data:
            update_memory(data)
            _last_relationship_signature = signature
            print("[Memory] 🤝 Relationship summary updated")

    except json.JSONDecodeError:
        pass
    except Exception as e:
        if "429" not in str(e):
            print(f"[Memory] ⚠️ Relationship summary error: {e}")


def _load_next_reminder_text() -> str:
    if not REMINDERS_PATH.exists():
        return ""
    try:
        records = json.loads(REMINDERS_PATH.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            return ""
        scheduled = [
            r
            for r in records
            if isinstance(r, dict) and str(r.get("status", "")).lower() == "scheduled"
        ]
        if not scheduled:
            return ""
        scheduled.sort(key=lambda r: str(r.get("when", "")))
        top = scheduled[0]
        when = str(top.get("when", "")).strip()
        msg = str(top.get("message", "")).strip()
        if when and msg:
            return f"Your next reminder is {msg} at {when}."
        if msg:
            return f"You have a pending reminder: {msg}."
    except Exception:
        return ""
    return ""


def _build_boot_greeting_instruction() -> str:
    memory = load_memory()
    relationship = memory.get("relationship_profile", {})
    notes = memory.get("notes", {})
    briefing = build_daily_briefing()
    personality = load_personality_mode()
    suggestion = briefing.get("summary") or _load_next_reminder_text()
    if not suggestion:
        relationship_summary = relationship.get("recurring_goals", {}).get(
            "value"
        ) or relationship.get("open_loops", {}).get("value")
        if relationship_summary:
            suggestion = (
                f"Based on my memory, you may want to focus on {relationship_summary}."
            )
    if not suggestion:
        note_summary = notes.get("relationship_summary", {}).get("value") or notes.get(
            "job", {}
        ).get("value")
        if note_summary:
            suggestion = (
                f"Based on my memory, I suggest we continue with {note_summary}."
            )
    if not suggestion:
        suggestion = "I suggest we continue refining your systems today."

    return (
        "You are booting up for the first time in this session. "
        "Greet your creator first in one or two short sentences. "
        "Address them as boss. "
        f"Your current personality mode is {personality.get('mode', 'witty')}. "
        f"Include this reminder or suggestion naturally: {suggestion}"
    )


TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the Windows computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')",
                }
            },
            "required": ["app_name"],
        },
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Search query"},
                "mode": {
                    "type": "STRING",
                    "description": "search (default) or compare",
                },
                "items": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Items to compare",
                },
                "aspect": {"type": "STRING", "description": "price | specs | reviews"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "weather_report",
        "description": "Gets real-time weather information for a city.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"city": {"type": "STRING", "description": "City name"}},
            "required": ["city"],
        },
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver": {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {
                    "type": "STRING",
                    "description": "The message to send",
                },
                "platform": {
                    "type": "STRING",
                    "description": "Platform: WhatsApp, Telegram, etc.",
                },
            },
            "required": ["receiver", "message_text", "platform"],
        },
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date": {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time": {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"},
            },
            "required": ["date", "time", "message"],
        },
    },
    {
        "name": "list_reminders",
        "description": "Lists all scheduled reminders.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "delete_reminder",
        "description": "Deletes a reminder by task name or message.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "task_name": {
                    "type": "STRING",
                    "description": "The task name of the reminder",
                },
                "message": {
                    "type": "STRING",
                    "description": "The message text of the reminder",
                },
            },
            "required": [],
        },
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "play | summarize | get_info | trending (default: play)",
                },
                "query": {
                    "type": "STRING",
                    "description": "Search query for play action",
                },
                "save": {
                    "type": "BOOLEAN",
                    "description": "Save summary to Notepad (summarize only)",
                },
                "region": {
                    "type": "STRING",
                    "description": "Country code for trending e.g. TR, US",
                },
                "url": {
                    "type": "STRING",
                    "description": "Video URL for get_info action",
                },
            },
            "required": [],
        },
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {
                    "type": "STRING",
                    "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'",
                },
                "text": {
                    "type": "STRING",
                    "description": "The question or instruction about the captured image",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "ALSO use for repeated actions: 'refresh 10 times', 'reload page 5 times' → action: reload_n, value: 10. "
            "Use for ANY single computer control command — even if repeated N times. "
            "NEVER route simple computer commands to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "The action to perform (if known). For repeated reload: 'reload_n'",
                },
                "description": {
                    "type": "STRING",
                    "description": "Natural language description of what to do",
                },
                "value": {
                    "type": "STRING",
                    "description": "Optional value: volume level, text to type, number of times, etc.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "browser_control",
        "description": (
            "Controls the web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, finding cheapest products, "
            "booking flights, any web-based task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | press | close",
                },
                "url": {"type": "STRING", "description": "URL for go_to action"},
                "query": {
                    "type": "STRING",
                    "description": "Search query for search action",
                },
                "selector": {
                    "type": "STRING",
                    "description": "CSS selector for click/type",
                },
                "text": {"type": "STRING", "description": "Text to click or type"},
                "description": {
                    "type": "STRING",
                    "description": "Element description for smart_click/smart_type",
                },
                "direction": {"type": "STRING", "description": "up or down for scroll"},
                "key": {"type": "STRING", "description": "Key name for press action"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "file_controller",
        "description": (
            "Manages files and folders. Use for: listing files, creating/deleting/moving/copying "
            "files, reading file contents, finding files by name or extension, checking disk usage, "
            "organizing the desktop, getting file info."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info",
                },
                "path": {
                    "type": "STRING",
                    "description": "File/folder path or shortcut: desktop, downloads, documents, home",
                },
                "destination": {
                    "type": "STRING",
                    "description": "Destination path for move/copy",
                },
                "new_name": {"type": "STRING", "description": "New name for rename"},
                "content": {
                    "type": "STRING",
                    "description": "Content for create_file/write",
                },
                "name": {"type": "STRING", "description": "File name to search for"},
                "extension": {
                    "type": "STRING",
                    "description": "File extension to search (e.g. .pdf)",
                },
                "count": {
                    "type": "INTEGER",
                    "description": "Number of results for largest",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "cmd_control",
        "description": (
            "Runs CMD/terminal commands by understanding natural language. "
            "Use when user wants to: find large files, check disk space, list processes, "
            "get system info, navigate folders, check network, find files by name, "
            "or do ANYTHING in the command line they don't know how to do themselves."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "task": {
                    "type": "STRING",
                    "description": "Natural language description of what to do. Example: 'find the 10 largest files on C drive'",
                },
                "visible": {
                    "type": "BOOLEAN",
                    "description": "Open visible CMD window so user can see. Default: true",
                },
                "command": {
                    "type": "STRING",
                    "description": "Optional: exact command if already known",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "desktop_control",
        "description": (
            "Controls the desktop. Use for: changing wallpaper, organizing desktop files, "
            "cleaning the desktop, listing desktop contents, or ANY other desktop-related task "
            "the user describes in natural language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task",
                },
                "path": {"type": "STRING", "description": "Image path for wallpaper"},
                "url": {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode": {
                    "type": "STRING",
                    "description": "by_type or by_date for organize",
                },
                "task": {
                    "type": "STRING",
                    "description": "Natural language description of any desktop task",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "code_helper",
        "description": (
            "Writes, edits, explains, runs, or self-builds code files. "
            "Use for ANY coding request: writing a script, fixing a file, "
            "editing existing code, running a file, or building and testing automatically."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "write | edit | explain | run | build | auto (default: auto)",
                },
                "description": {
                    "type": "STRING",
                    "description": "What the code should do, or what change to make",
                },
                "language": {
                    "type": "STRING",
                    "description": "Programming language (default: python)",
                },
                "output_path": {
                    "type": "STRING",
                    "description": "Where to save the file (full path or filename)",
                },
                "file_path": {
                    "type": "STRING",
                    "description": "Path to existing file for edit / explain / run / build",
                },
                "code": {
                    "type": "STRING",
                    "description": "Raw code string for explain",
                },
                "args": {
                    "type": "STRING",
                    "description": "CLI arguments for run/build",
                },
                "timeout": {
                    "type": "INTEGER",
                    "description": "Execution timeout in seconds (default: 30)",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "dev_agent",
        "description": (
            "Builds complete multi-file projects from scratch. "
            "Plans structure, writes all files, installs dependencies, "
            "opens VSCode, runs the project, and fixes errors automatically. "
            "Use for any project larger than a single script."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description": {
                    "type": "STRING",
                    "description": "What the project should do",
                },
                "language": {
                    "type": "STRING",
                    "description": "Programming language (default: python)",
                },
                "project_name": {
                    "type": "STRING",
                    "description": "Optional project folder name",
                },
                "timeout": {
                    "type": "INTEGER",
                    "description": "Run timeout in seconds (default: 30)",
                },
            },
            "required": ["description"],
        },
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks that require MULTIPLE DIFFERENT tools. "
            "Always respond to the user in the language they spoke. "
            "Examples: 'research X and save to file', 'find files and organize them', "
            "'fill a form on a website', 'write and test code'. "
            "DO NOT use for simple computer commands like volume, refresh, close, scroll, "
            "minimize, screenshot, restart, shutdown — use computer_settings for those. "
            "DO NOT use if the task can be done with a single tool call."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal": {
                    "type": "STRING",
                    "description": "Complete description of what needs to be accomplished",
                },
                "priority": {
                    "type": "STRING",
                    "description": "low | normal | high (default: normal)",
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "computer_control",
        "description": (
            "Direct computer control: type text, click buttons, use keyboard shortcuts, "
            "scroll, move mouse, take screenshots, fill forms, find elements on screen. "
            "Use when the user wants to interact with any app on the computer directly. "
            "Can generate random data for forms or use user's real info from memory."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data",
                },
                "text": {"type": "STRING", "description": "Text to type or paste"},
                "x": {"type": "INTEGER", "description": "X coordinate for click/move"},
                "y": {"type": "INTEGER", "description": "Y coordinate for click/move"},
                "keys": {
                    "type": "STRING",
                    "description": "Key combination e.g. 'ctrl+c'",
                },
                "key": {
                    "type": "STRING",
                    "description": "Single key to press e.g. 'enter'",
                },
                "direction": {
                    "type": "STRING",
                    "description": "Scroll direction: up | down | left | right",
                },
                "amount": {
                    "type": "INTEGER",
                    "description": "Scroll amount (default: 3)",
                },
                "seconds": {"type": "NUMBER", "description": "Seconds to wait"},
                "title": {
                    "type": "STRING",
                    "description": "Window title for focus_window",
                },
                "description": {
                    "type": "STRING",
                    "description": "Element description for screen_find/screen_click",
                },
                "type": {
                    "type": "STRING",
                    "description": "Data type for random_data: name|email|username|password|phone|birthday|address",
                },
                "field": {
                    "type": "STRING",
                    "description": "Field for user_data: name|email|city",
                },
                "clear_first": {
                    "type": "BOOLEAN",
                    "description": "Clear field before typing (default: true)",
                },
                "path": {"type": "STRING", "description": "Save path for screenshot"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "assistant_profile",
        "description": (
            "Manages FRIDAY personality and briefing preferences. "
            "Use for requests like 'be more formal', 'switch to witty mode', "
            "'use playful mode', or 'what personality mode are you in?'"
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "set_mode | get_mode"},
                "mode": {"type": "STRING", "description": "formal | witty | playful"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "voice_notes",
        "description": (
            "Stores, lists, and summarizes local voice notes. "
            "Use when the user says to save this as a voice note, list notes, or summarize notes."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "save_note | list_notes | summary",
                },
                "content": {
                    "type": "STRING",
                    "description": "The note content to save",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "flight_finder",
        "description": (
            "Searches for flights on Google Flights and speaks the best options. "
            "Use when user asks about flights, plane tickets, uçuş, bilet, etc."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin": {
                    "type": "STRING",
                    "description": "Departure city or airport code",
                },
                "destination": {
                    "type": "STRING",
                    "description": "Arrival city or airport code",
                },
                "date": {
                    "type": "STRING",
                    "description": "Departure date (any format)",
                },
                "return_date": {
                    "type": "STRING",
                    "description": "Return date for round trips",
                },
                "passengers": {
                    "type": "INTEGER",
                    "description": "Number of passengers (default: 1)",
                },
                "cabin": {
                    "type": "STRING",
                    "description": "economy | premium | business | first",
                },
                "save": {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"],
        },
    },
]


class JarvisLive:
    def __init__(self, ui: JarvisUI):
        self.ui = ui
        self.session = None
        self.audio_in_queue = None
        self.out_queue = None
        self._loop = None
        self._boot_greeted = False
        self._assistant_turn_active = threading.Event()
        self._assistant_last_audio_at = 0.0
        self._assistant_turn_started_at = 0.0
        self._mic_resume_delay = 0.45
        self._barge_in_chunks = 0
        self._barge_in_threshold = 3200
        self._barge_in_required_chunks = 6
        self._barge_in_min_speaking_time = 1.4

    def speak(self, text: str):
        """Thread-safe speak — any thread can call this."""
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]}, turn_complete=True
            ),
            self._loop,
        )

    def _set_assistant_turn_active(self, active: bool) -> None:
        if active:
            self._assistant_turn_active.set()
            self._assistant_turn_started_at = time.time()
            self._assistant_last_audio_at = time.time()
            try:
                self.ui.start_speaking()
            except Exception:
                pass
        else:
            self._assistant_turn_active.clear()
            self._assistant_turn_started_at = 0.0
            try:
                self.ui.stop_speaking()
            except Exception:
                pass

    async def _drop_pending_mic_audio(self) -> None:
        if not self.out_queue:
            return

        while True:
            try:
                self.out_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _drop_pending_output_audio(self) -> None:
        if not self.audio_in_queue:
            return
        while True:
            try:
                self.audio_in_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _send_boot_greeting_once(self) -> None:
        if self._boot_greeted or not self.session:
            return
        self._boot_greeted = True
        try:
            await self.session.send_client_content(
                turns={"parts": [{"text": _build_boot_greeting_instruction()}]},
                turn_complete=True,
            )
        except Exception:
            self._boot_greeted = False
            raise

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory = load_memory()
        mem_str = format_memory_for_prompt(memory)
        briefing = build_daily_briefing()
        personality = load_personality_mode()
        sys_prompt = _load_system_prompt()

        now = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders. "
            f"If user says 'in 2 minutes', add 2 minutes to this time.\n"
            f"Speak with smooth, natural pacing and a warm, human-sounding British-accented female delivery.\n"
            f"Avoid robotic cadence, clipped phrasing, and over-enunciated AI-style speech.\n"
            f"Use light dry humor occasionally when appropriate, but stay sharp and helpful.\n\n"
        )
        personality_ctx = (
            "[PERSONALITY MODE]\n"
            f"Current mode: {personality.get('mode', 'witty')}\n"
            f"Description: {personality.get('description', '')}\n\n"
        )
        briefing_ctx = (
            "[DAILY BRIEFING]\n"
            f"{briefing.get('summary', 'No urgent briefing items yet, boss.')}\n\n"
        )
        recent_ctx = format_recent_conversations_for_prompt()

        parts = [time_ctx, personality_ctx, briefing_ctx]
        if mem_str:
            parts.append(mem_str + "\n")
        if recent_ctx:
            parts.append(recent_ctx + "\n")
        parts.append(sys_prompt)
        sys_prompt = "".join(parts)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction=sys_prompt,
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Laomedeia"
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 TOOL: {name}  ARGS: {args}")

        loop = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(
                    None,
                    lambda: open_app(parameters=args, response=None, player=self.ui),
                )
                result = r or f"Opened {args.get('app_name')} successfully."

            elif name == "weather_report":
                r = await loop.run_in_executor(
                    None, lambda: weather_action(parameters=args, player=self.ui)
                )
                result = r or f"Weather report for {args.get('city')} delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(
                    None, lambda: browser_control(parameters=args, player=self.ui)
                )
                result = r or "Browser action completed."

            elif name == "file_controller":
                r = await loop.run_in_executor(
                    None, lambda: file_controller(parameters=args, player=self.ui)
                )
                result = r or "File operation completed."

            elif name == "send_message":
                r = await loop.run_in_executor(
                    None,
                    lambda: send_message(
                        parameters=args,
                        response=None,
                        player=self.ui,
                        session_memory=None,
                    ),
                )
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(
                    None,
                    lambda: reminder(parameters=args, response=None, player=self.ui),
                )
                result = (
                    r or f"Reminder set for {args.get('date')} at {args.get('time')}."
                )

            elif name == "list_reminders":
                from actions.reminder import list_reminders

                reminders = await loop.run_in_executor(None, list_reminders)
                if reminders:
                    lines = []
                    for item in reminders:
                        msg = item.get("message", "")
                        when = item.get("when", "")
                        status = item.get("status", "scheduled")
                        lines.append(f"- {msg} at {when} ({status})")
                    result = "Your reminders:\n" + "\n".join(lines)
                else:
                    result = "You have no reminders set."

            elif name == "delete_reminder":
                from actions.reminder import delete_reminder

                task_name = args.get("task_name")
                message = args.get("message")
                result = await loop.run_in_executor(
                    None, lambda: delete_reminder(task_name=task_name, message=message)
                )

            elif name == "youtube_video":
                r = await loop.run_in_executor(
                    None,
                    lambda: youtube_video(
                        parameters=args, response=None, player=self.ui
                    ),
                )
                result = r or "Done."

            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={
                        "parameters": args,
                        "response": None,
                        "player": self.ui,
                        "session_memory": None,
                    },
                    daemon=True,
                ).start()
                result = (
                    "Vision module activated. "
                    "Stay completely silent — vision module will speak directly."
                )

            elif name == "computer_settings":
                r = await loop.run_in_executor(
                    None,
                    lambda: computer_settings(
                        parameters=args, response=None, player=self.ui
                    ),
                )
                result = r or "Done."

            elif name == "cmd_control":
                r = await loop.run_in_executor(
                    None, lambda: cmd_control(parameters=args, player=self.ui)
                )
                result = r or "Command executed."

            elif name == "desktop_control":
                r = await loop.run_in_executor(
                    None, lambda: desktop_control(parameters=args, player=self.ui)
                )
                result = r or "Desktop action completed."
            elif name == "code_helper":
                r = await loop.run_in_executor(
                    None,
                    lambda: code_helper(
                        parameters=args, player=self.ui, speak=self.speak
                    ),
                )
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(
                    None,
                    lambda: dev_agent(
                        parameters=args, player=self.ui, speak=self.speak
                    ),
                )
                result = r or "Done."
            elif name == "agent_task":
                goal = args.get("goal", "")
                priority_str = args.get("priority", "normal").lower()

                from agent.task_queue import get_queue, TaskPriority

                priority_map = {
                    "low": TaskPriority.LOW,
                    "normal": TaskPriority.NORMAL,
                    "high": TaskPriority.HIGH,
                }
                priority = priority_map.get(priority_str, TaskPriority.NORMAL)

                queue = get_queue()
                task_id = queue.submit(
                    goal=goal,
                    priority=priority,
                    speak=self.speak,
                )
                result = f"Task started (ID: {task_id}). I'll update you as I make progress, sir."

            elif name == "web_search":
                r = await loop.run_in_executor(
                    None, lambda: web_search_action(parameters=args, player=self.ui)
                )
                result = r or "Search completed."
            elif name == "computer_control":
                r = await loop.run_in_executor(
                    None, lambda: computer_control(parameters=args, player=self.ui)
                )
                result = r or "Done."
            elif name == "assistant_profile":
                action = str(args.get("action", "get_mode")).strip().lower()
                if action == "set_mode":
                    mode = str(args.get("mode", "")).strip().lower()
                    data = await loop.run_in_executor(
                        None, lambda: set_personality_mode(mode)
                    )
                    if hasattr(self.ui, "refresh_memory_views"):
                        self.ui.refresh_memory_views()
                    result = f"Personality mode set to {data.get('mode', mode)}, boss."
                else:
                    data = await loop.run_in_executor(None, load_personality_mode)
                    result = f"My current personality mode is {data.get('mode', 'witty')}, boss."
            elif name == "voice_notes":
                r = await loop.run_in_executor(
                    None, lambda: voice_notes(parameters=args, player=self.ui)
                )
                result = r or "Voice note action completed, boss."

            elif name == "flight_finder":
                r = await loop.run_in_executor(
                    None, lambda: flight_finder(parameters=args, player=self.ui)
                )
                result = r or "Done."

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()

        print(f"[JARVIS] 📤 {name} → {result[:80]}")

        return types.FunctionResponse(id=fc.id, name=name, response={"result": result})

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE,
        )
        try:
            while True:
                data = await asyncio.to_thread(
                    stream.read, CHUNK_SIZE, exception_on_overflow=False
                )

                try:
                    mic_level = audioop.rms(data, 2)
                except Exception:
                    mic_level = 0

                # Keep draining the mic locally while JARVIS is speaking so
                # old voice input does not get sent late as a new turn.
                if self._assistant_turn_active.is_set():
                    speaking_for = time.time() - self._assistant_turn_started_at
                    if (
                        speaking_for >= self._barge_in_min_speaking_time
                        and mic_level >= self._barge_in_threshold
                    ):
                        self._barge_in_chunks += 1
                    else:
                        self._barge_in_chunks = max(0, self._barge_in_chunks - 1)

                    # Let the user interrupt naturally with a short burst of speech.
                    if self._barge_in_chunks >= self._barge_in_required_chunks:
                        self._barge_in_chunks = 0
                        await self._drop_pending_output_audio()
                        self._assistant_last_audio_at = 0.0
                        self._set_assistant_turn_active(False)
                    else:
                        continue
                else:
                    self._barge_in_chunks = 0

                if (
                    time.time() - self._assistant_last_audio_at
                ) < self._mic_resume_delay:
                    continue

                await self.out_queue.put({"data": data, "mime_type": "audio/pcm"})
        except Exception as e:
            print(f"[JARVIS] ❌ Mic error: {e}")
            raise
        finally:
            stream.close()

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf = []
        in_buf = []

        try:
            while True:
                turn = self.session.receive()
                async for response in turn:
                    if response.data:
                        if not self._assistant_turn_active.is_set():
                            await self._drop_pending_mic_audio()
                            self._set_assistant_turn_active(True)
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                in_buf.append(txt)

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = sc.output_transcription.text.strip()
                            if txt:
                                if not self._assistant_turn_active.is_set():
                                    await self._drop_pending_mic_audio()
                                    self._set_assistant_turn_active(True)
                                out_buf.append(txt)

                        if sc.turn_complete:
                            full_in = ""
                            full_out = ""

                            if in_buf:
                                full_in = " ".join(in_buf).strip()
                                if full_in:
                                    self.ui.write_log(f"You: {full_in}")
                            in_buf = []

                            if out_buf:
                                full_out = " ".join(out_buf).strip()
                                if full_out:
                                    self.ui.write_log(f"FRIDAY: {full_out}")
                            out_buf = []

                            if full_in and len(full_in) > 5:
                                append_conversation_turn(full_in, full_out)
                                threading.Thread(
                                    target=_update_memory_async,
                                    args=(full_in, full_out),
                                    daemon=True,
                                ).start()
                                threading.Thread(
                                    target=_update_relationship_memory_async,
                                    daemon=True,
                                ).start()
                                if hasattr(self.ui, "refresh_memory_views"):
                                    self.ui.refresh_memory_views()

                            self._assistant_last_audio_at = time.time()
                            self._set_assistant_turn_active(False)

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 Tool call: {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )

        except Exception as e:
            print(f"[JARVIS] ❌ Recv error: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
        )
        try:
            while True:
                chunk = await self.audio_in_queue.get()
                try:
                    level = min(1.0, audioop.rms(chunk, 2) / 12000.0)
                    if hasattr(self.ui, "update_audio_level"):
                        self.ui.update_audio_level(level)
                except Exception:
                    pass
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[JARVIS] ❌ Play error: {e}")
            raise
        finally:
            stream.close()

    async def run(self):
        client = genai.Client(
            api_key=_get_api_key(), http_options={"api_version": "v1beta"}
        )

        while True:
            try:
                print("[JARVIS] 🔌 Connecting...")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session = session
                    self._loop = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue = asyncio.Queue(maxsize=10)
                    self._boot_greeted = False

                    print("[JARVIS] ✅ Connected.")
                    self.ui.write_log("FRIDAY online.")
                    await self._send_boot_greeting_once()

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

            except Exception as e:
                print(f"[JARVIS] ⚠️  Error: {e}")
                traceback.print_exc()

            print("[JARVIS] 🔄 Reconnecting in 3s...")
            await asyncio.sleep(3)


def main():
    ui = JarvisUI("face.png")

    def runner():
        ui.wait_for_api_key()

        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.run()


if __name__ == "__main__":
    main()
