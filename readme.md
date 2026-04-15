# 🤖 FRIDAY — Your Personal AI Assistant

> *"I'm always on. I'm always listening. I'm FRIDAY."*

A powerful voice AI that lives on your computer. It sees, hears, thinks, and acts — all locally.

---

## 🎯 What is FRIDAY?

FRIDAY is **your own JARVIS** — a context-aware AI assistant with:

- 🎙️ **Natural Voice Chat** — Talk to it, it talks back
- 👁️ **Vision** — It can see your screen and understand what's happening
- 🧠 **Memory** — It remembers your preferences, relationships, and context
- ⚡ **Automation** — It plans and executes complex tasks autonomously

Built with Python + Gemini AI. Runs entirely on your machine.

---

## 📸 First Look

![FRIDAY HUD](screenshots/hud_main.png)

*The FRIDAY interface — minimal, clean, powerful*

---

## ✨ Features

### Core Capabilities
| Feature | What It Does |
|---------|--------------|
| 🎙️ Voice Input | Wake word + continuous conversation |
| 🗣️ Voice Output | Natural TTS responses |
| 👁️ Screen Vision | Analyzes screenshots in real-time |
| 🧠 Persistent Memory | Remembers preferences, relationships, notes |
| ⏰ Smart Reminders | Set reminders by voice |
| 📱 Messaging | Send WhatsApp/Telegram messages by voice |

### Tools Built-In
- 🌐 **Web Search** — Ask anything, get answers
- 📺 **YouTube** — Play, summarize, search videos
- 🌤️ **Weather** — Real-time weather reports
- 📁 **File Manager** — Create, read, delete, organize files
- 💻 **App Launcher** — Open apps with your voice
- 🖥️ **System Control** — Volume, brightness, screenshots, more
- ✈️ **Flight Finder** — Search flights
- 💻 **Code Helper** — Write and debug code

---

## 🚀 Get FRIDAY Running

### 1. Clone
```bash
git clone https://github.com/pynshai-nongsiej/Friday.git
cd Friday
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Setup
```bash
python setup.py
```

### 4. Run
```bash
python main.py
```

### 5. Add Your API Key
On first run, enter your **free Gemini API key** when prompted.

That's it. FRIDAY is ready.

---

## 📋 Requirements

- **OS**: Windows 10/11 or macOS
- **Python**: 3.10+
- **Mic**: Any working microphone
- **API Key**: Free from [Google AI Studio](https://aistudio.google.com)

---

## 📁 Project Structure

```
Friday/
├── actions/           # All the tools (reminder, weather, etc.)
├── agent/            # AI planning & execution
├── memory/           # Persistent storage
├── config/           # API keys & config
├── core/             # System prompts
├── ui.py             # The visual interface
├── main.py            # Entry point
└── readme.md        # You're here
```

---

## 🛠️ Configuration

Edit `config/api_keys.json`:

```json
{
  "GEMINI_API_KEY": "your-key-here"
}
```

Optional keys for extra features:
- `OPENWEATHER_API_KEY` — weather data
- Custom TTS voices

---

## 🎭 Talk to FRIDAY

Just speak naturally! Example commands:

| You Say | FRIDAY Does |
|---------|------------|
| "Hey FRIDAY, set a reminder for 3 PM" | Sets reminder |
| "What's the weather?" | Gives weather |
| "Play some music on YouTube" | Opens YouTube |
| "Send a message to John on WhatsApp" | Sends message |
| "Take a screenshot" | Captures screen |
| "Open Safari" | Launches app |
| "What's on my screen?" | Analyzes screen |

---

## 🔐 Privacy

- **Your data stays on your machine**
- No cloud recording
- You control everything
- API calls only go to Google for AI processing

---

## 🌍 Connect

- **Instagram**: [@pynshai._.nongsiej](https://instagram.com/pynshai._.nongsiej)

---



## ⭐ Show Support

If this project helps you — star it. It costs nothing and helps others find FRIDAY.

---

*Built with ❤️ by Pynshai Nongsiej*

*"I'm only as useful as you let me be, boss."*