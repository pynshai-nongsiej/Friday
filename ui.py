import json
import math
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path

import psutil
from PyQt6.QtCore import QObject, QPointF, QRectF, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from memory.memory_manager import load_memory
from memory.dashboard_state import load_personality_mode


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR = get_base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE = CONFIG_DIR / "api_keys.json"
REMINDERS_FILE = BASE_DIR / "memory" / "reminders.json"


class UIBridge(QObject):
    log_message = pyqtSignal(str)
    speaking_changed = pyqtSignal(bool)
    audio_level_changed = pyqtSignal(float)
    reminder_added = pyqtSignal(str, str, str)
    refresh_requested = pyqtSignal()
    shutdown_requested = pyqtSignal()


class AuthOverlay(QFrame):
    def __init__(self, parent: QWidget, on_authorize):
        super().__init__(parent)
        self._on_authorize = on_authorize
        self.setObjectName("authOverlay")
        self.setFixedSize(500, 250)
        self.setStyleSheet(
            """
            QFrame#authOverlay {
                background-color: rgba(7, 16, 28, 235);
                border: 1px solid rgba(74, 227, 255, 180);
                border-radius: 22px;
            }
            QLabel#title {
                color: rgb(222, 247, 255);
                font-size: 24px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            QLabel#subtitle {
                color: rgb(117, 181, 194);
                font-size: 13px;
            }
            QLineEdit {
                background-color: rgba(4, 12, 24, 220);
                color: rgb(225, 248, 255);
                border: 1px solid rgba(62, 181, 214, 120);
                border-radius: 14px;
                padding: 12px 14px;
                font-size: 14px;
            }
            QPushButton {
                background-color: rgb(255, 154, 77);
                color: rgb(8, 14, 20);
                border: none;
                border-radius: 14px;
                padding: 12px 18px;
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: rgb(255, 178, 116);
            }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        title = QLabel("CREATOR AUTHORIZATION")
        title.setObjectName("title")
        subtitle = QLabel("Enter your Gemini API key to bring FRIDAY online.")
        subtitle.setObjectName("subtitle")
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("Gemini API key")
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        button = QPushButton("AUTHORIZE")
        button.clicked.connect(self._submit)
        self.key_input.returnPressed.connect(self._submit)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(10)
        layout.addWidget(self.key_input)
        layout.addStretch(1)
        layout.addWidget(button)

        glow = QGraphicsOpacityEffect(self)
        glow.setOpacity(0.98)
        self.setGraphicsEffect(glow)

    def _submit(self):
        key = self.key_input.text().strip()
        if key:
            self._on_authorize(key)


class HUDWindow(QMainWindow):
    def __init__(self, face_path: str, bridge: UIBridge):
        super().__init__()
        self.bridge = bridge
        self.bridge.log_message.connect(self._append_log)
        self.bridge.speaking_changed.connect(self._set_speaking)
        self.bridge.audio_level_changed.connect(self._set_audio_level)
        self.bridge.reminder_added.connect(self._add_reminder_internal)
        self.bridge.refresh_requested.connect(self._refresh_memory)
        self.bridge.shutdown_requested.connect(self.close)

        self.setWindowTitle("F.R.I.D.A.Y - MARK XXX")
        self.setStyleSheet("background-color: rgb(3, 6, 12);")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)
        self.showFullScreen()

        self._start_time = time.perf_counter()
        self._frame_times = deque(maxlen=120)
        self._last_paint = None
        self._pulse_value = 0.0
        self._hud_offset = 0.0

        self.speaking = False
        self.status_text = "READY"
        self.audio_level = 0.0
        self.audio_level_display = 0.0
        self.recent_logs = deque(maxlen=18)
        self.reminders = self._load_reminders()
        self.memory_summary = self._load_memory_summary()
        self.personality_mode = load_personality_mode()
        self._type_index = 0

        self.stats_lock = threading.Lock()
        self.stats = {
            "cpu": 0.0,
            "ram": 0.0,
            "upload_kbps": 0.0,
            "download_kbps": 0.0,
            "battery": None,
            "battery_plugged": False,
        }
        self._stats_running = True
        self._stats_thread = threading.Thread(target=self._stats_loop, daemon=True, name="HUDStats")
        self._stats_thread.start()

        self.face_pixmap = self._load_face(face_path)
        self.static_cache = None
        self.cache_size = None

        self.api_ready = API_FILE.exists()
        self.api_ready_event = threading.Event()
        if self.api_ready:
            self.api_ready_event.set()

        self._build_overlay()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(24)

    def _build_overlay(self):
        self.auth_overlay = AuthOverlay(self, self._save_api_key)
        self.auth_overlay.setVisible(not self.api_ready)
        self._position_overlay()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.static_cache = None
        self._position_overlay()

    def _position_overlay(self):
        if not hasattr(self, "auth_overlay"):
            return
        size = self.auth_overlay.size()
        self.auth_overlay.move((self.width() - size.width()) // 2, (self.height() - size.height()) // 2)

    def _save_api_key(self, key: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(API_FILE, "w", encoding="utf-8") as file:
            json.dump({"gemini_api_key": key}, file, indent=4)
        self.api_ready = True
        self.api_ready_event.set()
        self.auth_overlay.hide()
        self._append_log("SYS: Creator authorization confirmed.")

    def _load_face(self, face_path: str):
        path = Path(face_path)
        if not path.is_absolute():
            path = BASE_DIR / face_path
        if not path.exists():
            return None
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return None
        return pixmap

    def _load_reminders(self):
        if not REMINDERS_FILE.exists():
            return []
        try:
            data = json.loads(REMINDERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        records = []
        for item in data[-10:]:
            if not isinstance(item, dict):
                continue
            records.append(
                {
                    "message": str(item.get("message", "")).strip(),
                    "when": str(item.get("when", "")).strip(),
                    "status": str(item.get("status", "scheduled")).strip().upper(),
                }
            )
        records.reverse()
        return records

    def _load_memory_summary(self):
        memory = load_memory()
        relation = memory.get("relationship_profile", {})
        notes = memory.get("notes", {})
        pieces = []
        note = notes.get("relationship_summary", {}).get("value")
        if note:
            pieces.append(str(note))
        for key in ("ongoing_projects", "open_loops", "recurring_goals", "support_style"):
            value = relation.get(key, {}).get("value")
            if value:
                pieces.append(str(value))
        return pieces[:4]

    def _refresh_memory(self):
        self.memory_summary = self._load_memory_summary()
        self.reminders = self._load_reminders()
        self.personality_mode = load_personality_mode()
        self.update()

    def _append_log(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        self.recent_logs.append(text)
        self.update()

    def _set_speaking(self, speaking: bool):
        self.speaking = speaking
        self.status_text = "RESPONDING" if speaking else "READY"
        if not speaking:
            self.audio_level = 0.0
        self.update()

    def _set_audio_level(self, level: float):
        self.audio_level = max(0.0, min(1.0, float(level)))

    def _add_reminder_internal(self, message: str, when_text: str, status: str):
        self.reminders.insert(
            0,
            {
                "message": message.strip(),
                "when": when_text.strip(),
                "status": status.strip().upper(),
            },
        )
        self.reminders = self.reminders[:10]
        self.update()

    def _tick(self):
        self._pulse_value += 0.020
        self._hud_offset += 0.45
        self.audio_level_display = (self.audio_level_display * 0.76) + (self.audio_level * 0.24)
        self._type_index += 1
        self.update()

    def closeEvent(self, event):
        self._stats_running = False
        self.timer.stop()
        super().closeEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_F11:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
            return
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        del event
        now = time.perf_counter()
        if self._last_paint is not None:
            dt = now - self._last_paint
            if dt > 0:
                self._frame_times.append(1.0 / dt)
        self._last_paint = now

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if self.static_cache is None or self.cache_size != self.size():
            self.static_cache = self._build_static_cache()
            self.cache_size = self.size()

        painter.drawPixmap(0, 0, self.static_cache)
        self._paint_dynamic(painter, now - self._start_time)
        painter.end()

    def _build_static_cache(self):
        pixmap = QPixmap(self.size())
        pixmap.fill(QColor(3, 6, 12))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        self._paint_background(painter)
        self._paint_shell(painter)

        painter.end()
        return pixmap

    def _paint_background(self, painter: QPainter):
        w = self.width()
        h = self.height()

        gradient = QLinearGradient(0, 0, 0, h)
        gradient.setColorAt(0.0, QColor(4, 3, 4))
        gradient.setColorAt(0.4, QColor(7, 5, 5))
        gradient.setColorAt(1.0, QColor(10, 6, 4))
        painter.fillRect(self.rect(), gradient)

        vignette = QRadialGradient(QPointF(w / 2, h / 2), min(w, h) * 0.58)
        vignette.setColorAt(0.0, QColor(52, 22, 8, 12))
        vignette.setColorAt(0.55, QColor(10, 8, 8, 28))
        vignette.setColorAt(1.0, QColor(0, 0, 0, 185))
        painter.fillRect(self.rect(), vignette)

        painter.setPen(QPen(QColor(34, 16, 10, 55), 1))
        step_x = max(110, w // 16)
        step_y = max(78, h // 12)
        for x in range(0, w, step_x):
            painter.drawLine(x, 0, x, h)
        for y in range(0, h, step_y):
            painter.drawLine(0, y, w, y)

        painter.setPen(QPen(QColor(18, 10, 8, 42), 1))
        for y in range(0, h, 5):
            painter.drawLine(0, y, w, y)

        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(220):
            x = (i * 97) % max(1, w)
            y = (i * 57 + (i % 11) * 31) % max(1, h)
            size = 0.8 + (i % 3) * 0.6
            alpha = 26 + (i % 5) * 12
            painter.setBrush(QColor(255, 138, 54, alpha))
            painter.drawEllipse(QPointF(x, y), size, size)

    def _paint_shell(self, painter: QPainter):
        w = self.width()
        h = self.height()

        painter.fillRect(QRectF(0, 0, w, h * 0.09), QColor(6, 6, 8, 160))
        painter.fillRect(QRectF(0, h * 0.84, w, h * 0.16), QColor(5, 5, 7, 150))

        painter.setPen(QPen(QColor(130, 58, 22, 135), 1))
        painter.drawLine(int(w * 0.12), int(h * 0.08), int(w * 0.88), int(h * 0.08))
        painter.drawLine(int(w * 0.12), int(h * 0.82), int(w * 0.88), int(h * 0.82))

        top_y = int(h * 0.025)
        painter.drawLine(int(w * 0.14), top_y + 10, int(w * 0.34), top_y + 10)
        painter.drawLine(int(w * 0.66), top_y + 10, int(w * 0.86), top_y + 10)
        painter.drawLine(int(w * 0.39), top_y + 10, int(w * 0.44), top_y - 6)
        painter.drawLine(int(w * 0.56), top_y - 6, int(w * 0.61), top_y + 10)
        painter.drawLine(int(w * 0.44), top_y - 6, int(w * 0.56), top_y - 6)

    def _draw_panel_shape(self, painter: QPainter, rect: QRectF):
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(12, 11, 13, 228))
        painter.drawRoundedRect(rect, 14, 14)

        painter.setPen(QPen(QColor(97, 55, 22, 200), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)

        painter.setPen(QPen(QColor(255, 148, 61, 120), 1))
        painter.drawLine(
            QPointF(rect.left() + 14, rect.top() + 14),
            QPointF(rect.right() - 14, rect.top() + 14),
        )
        painter.drawLine(
            QPointF(rect.left() + 14, rect.bottom() - 14),
            QPointF(rect.right() - 14, rect.bottom() - 14),
        )
        painter.restore()

    def _paint_dynamic(self, painter: QPainter, t: float):
        self._paint_top_bar(painter, t)
        self._paint_orb_falloff(painter, t)
        self._paint_center_core(painter, t)
        self._paint_floor_trace(painter, t)

    def _mood_palette(self):
        mode = str(self.personality_mode.get("mode", "witty")).lower()
        palettes = {
            "formal": {
                "primary": QColor(255, 176, 110, 220),
                "secondary": QColor(220, 122, 60, 170),
                "soft": QColor(255, 156, 92, 64),
                "text": QColor(244, 235, 226),
            },
            "witty": {
                "primary": QColor(255, 164, 86, 220),
                "secondary": QColor(255, 122, 54, 170),
                "soft": QColor(255, 140, 72, 74),
                "text": QColor(245, 232, 221),
            },
            "playful": {
                "primary": QColor(255, 198, 108, 220),
                "secondary": QColor(255, 148, 78, 175),
                "soft": QColor(255, 188, 102, 76),
                "text": QColor(250, 238, 224),
            },
        }
        return palettes.get(mode, palettes["witty"])

    def _paint_top_bar(self, painter: QPainter, t: float):
        w = self.width()
        cx = w / 2
        palette = self._mood_palette()
        painter.save()
        painter.setPen(palette["text"])
        painter.setFont(QFont("Helvetica", 27, QFont.Weight.DemiBold))
        title = "FRIDAY"
        painter.drawText(int(cx - self._text_width(painter, title) / 2), 40, title)
        painter.setPen(QColor(144, 116, 92))
        painter.setFont(QFont("Helvetica", 9))
        subtitle = "INTELLIGENT SYSTEMS INTERFACE"
        painter.drawText(int(cx - self._text_width(painter, subtitle) / 2), 56, subtitle)

        self._paint_micro_wave(painter, QRectF(w * 0.14, 18, w * 0.10, 18), t, compact=True)
        self._paint_mini_meter(painter, QRectF(w * 0.74, 16, w * 0.11, 20), t)
        painter.restore()

    def _paint_orb_falloff(self, painter: QPainter, t: float):
        w = self.width()
        h = self.height()
        cx = w / 2
        cy = h * 0.48
        base_r = min(h * 0.26, w * 0.20) * 1.06
        energy = 0.35 + self.audio_level_display * 1.25
        palette = self._mood_palette()

        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)

        for idx in range(40):
            lane = (idx % 18) / 17.0
            phase = (t * (0.095 + (idx % 7) * 0.014) + idx * 0.053) % 1.0
            start_angle = (idx / 40.0) * math.tau + math.sin(t * 0.17 + idx) * 0.12
            spread = base_r * (0.22 + lane * 0.46)
            source_x = cx + math.cos(start_angle) * spread
            source_y = cy + math.sin(start_angle) * spread * 0.82
            trail_angle = start_angle + math.sin(idx * 0.9 + t * 0.7) * 0.48
            distance = phase * (170 + lane * 260)
            head = QPointF(
                source_x + math.cos(trail_angle) * distance,
                source_y + math.sin(trail_angle) * distance * 0.86,
            )
            tail = QPointF(
                head.x() - math.cos(trail_angle) * (18 + energy * 48),
                head.y() - math.sin(trail_angle) * (18 + energy * 48),
            )
            alpha = int(34 + 150 * (1.0 - phase) * min(1.0, energy))
            painter.setPen(QPen(QColor(palette["primary"].red(), palette["primary"].green(), palette["primary"].blue(), alpha), 1))
            painter.drawLine(tail, head)

            if idx % 3 == 0:
                painter.setPen(Qt.PenStyle.NoPen)
                blur = 1.6 + (1.0 - phase) * 2.4 + energy * 0.8
                painter.setBrush(QColor(palette["text"].red(), palette["text"].green(), palette["text"].blue(), min(160, alpha)))
                painter.drawEllipse(head, blur, blur)
                painter.setBrush(QColor(palette["primary"].red(), palette["primary"].green(), palette["primary"].blue(), min(220, alpha + 30)))
                painter.drawEllipse(head, 0.8 + energy, 0.8 + energy)

        for idx in range(14):
            phase = (t * (0.17 + idx * 0.006) + idx * 0.19) % 1.0
            angle = (idx / 14.0) * math.tau + t * 0.08
            shard_len = 16 + 26 * energy
            src = QPointF(
                cx + math.cos(angle) * (base_r * 0.66),
                cy + math.sin(angle) * (base_r * 0.52),
            )
            head = QPointF(
                src.x() + math.cos(angle) * phase * (120 + idx * 6),
                src.y() + math.sin(angle) * phase * (90 + idx * 5),
            )
            left = QPointF(head.x() - 3.5, head.y() - shard_len * 0.25)
            right = QPointF(head.x() + 3.5, head.y() - shard_len * 0.25)
            tip = QPointF(head.x(), head.y() + shard_len * 0.75)
            painter.setPen(QPen(QColor(palette["secondary"].red(), palette["secondary"].green(), palette["secondary"].blue(), int(90 + 90 * (1.0 - phase))), 1))
            painter.setBrush(QColor(palette["primary"].red(), palette["primary"].green(), palette["primary"].blue(), int(34 + 60 * (1.0 - phase))))
            painter.drawPolygon(QPolygonF([left, tip, right]))

        painter.setPen(QPen(QColor(palette["soft"].red(), palette["soft"].green(), palette["soft"].blue(), 42), 1))
        for idx in range(10):
            angle = idx * (math.pi / 5.0) + t * 0.05
            radius = base_r * (1.05 + idx * 0.08)
            painter.drawLine(
                QPointF(cx - math.cos(angle) * radius, cy - math.sin(angle) * radius * 0.84),
                QPointF(cx + math.cos(angle) * radius, cy + math.sin(angle) * radius * 0.84),
            )
        painter.restore()

    def _paint_left_log(self, painter: QPainter):
        w = self.width()
        h = self.height()
        rect = QRectF(w * 0.015, h * 0.12, w * 0.215, h * 0.66)
        left = int(rect.left() + 16)
        top = int(rect.top())
        right = int(rect.right() - 16)

        painter.save()
        painter.setPen(QColor(239, 231, 226))
        painter.setFont(QFont("Helvetica", 11, QFont.Weight.DemiBold))
        painter.drawText(left, top + 24, "INTERACTION LOG")

        tabs = [("ALL", True), ("USER", False), ("FRIDAY", False), ("SYSTEM", False)]
        tab_x = left
        for label, active in tabs:
            tab_w = 34 + self._text_width(painter, label)
            tab_rect = QRectF(tab_x, top + 34, tab_w, 22)
            painter.setBrush(QColor(65, 35, 18, 220) if active else QColor(18, 16, 18, 220))
            painter.setPen(QPen(QColor(255, 148, 61, 180) if active else QColor(72, 55, 44, 180), 1))
            painter.drawRoundedRect(tab_rect, 10, 10)
            painter.setPen(QColor(255, 192, 140) if active else QColor(134, 116, 102))
            painter.setFont(QFont("Helvetica", 8, QFont.Weight.DemiBold))
            painter.drawText(int(tab_rect.left() + 10), int(tab_rect.top() + 15), label)
            tab_x += tab_w + 8

        painter.setPen(QColor(110, 96, 88))
        painter.setFont(QFont("Helvetica", 8, QFont.Weight.DemiBold))
        painter.drawText(left, top + 80, "TODAY")

        entries = list(self.recent_logs)[-7:] or [
            "USER: Set a reminder for 3 PM",
            "FRIDAY: Reminder set for 3 PM, boss.",
            "USER: Analyze the report data.",
            "FRIDAY: Processing data analysis, boss.",
        ]
        y = top + 98
        for idx, line in enumerate(reversed(entries)):
            if y > rect.bottom() - 54:
                break
            self._paint_log_entry(painter, QRectF(left, y, rect.width() - 32, 54), line, idx % 2 == 0)
            y += 62
        painter.restore()

    def _paint_right_context(self, painter: QPainter):
        w = self.width()
        h = self.height()
        rect = QRectF(w * 0.77, h * 0.12, w * 0.215, h * 0.66)
        left = int(rect.left() + 16)
        top = int(rect.top())

        painter.save()
        painter.setPen(QColor(239, 231, 226))
        painter.setFont(QFont("Helvetica", 11, QFont.Weight.DemiBold))
        painter.drawText(left, top + 24, "CONTEXT PANEL")

        blocks = [
            ("SCHEDULE", self._build_schedule_lines()),
            ("DATA ANALYSIS", self._build_analysis_lines()),
            ("EXECUTION TRACE", self._build_trace_lines()),
            ("ENVIRONMENT", ["Workspace: MARK-XXX", "Connection: STABLE", "Status: HEALTHY"]),
        ]
        y = top + 40
        for title, lines in blocks:
            block_h = 110 if title != "ENVIRONMENT" else 86
            self._paint_context_block(painter, QRectF(left, y, rect.width() - 32, block_h), title, lines)
            y += block_h + 12
            if y > rect.bottom() - 80:
                break
        painter.restore()

    def _paint_center_core(self, painter: QPainter, t: float):
        w = self.width()
        h = self.height()
        cx = w / 2
        cy = h * 0.48
        core_r = min(h * 0.26, w * 0.20)
        outer = core_r * 1.02
        mid = core_r * 0.78
        inner = core_r * 0.46
        audio_boost = 1.0 + (self.audio_level_display * 0.18)
        outer *= audio_boost
        mid *= 1.0 + (self.audio_level_display * 0.10)
        inner *= 1.0 + (self.audio_level_display * 0.06)
        palette = self._mood_palette()

        painter.save()
        glow = QRadialGradient(QPointF(cx, cy), outer + 80)
        glow.setColorAt(0.0, QColor(palette["primary"].red(), palette["primary"].green(), palette["primary"].blue(), 70))
        glow.setColorAt(0.35, QColor(palette["secondary"].red(), palette["secondary"].green(), palette["secondary"].blue(), 48))
        glow.setColorAt(0.72, QColor(80, 32, 12, 28))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(cx, cy), outer + 86, outer + 86)

        self._paint_orb_depth_rings(painter, cx, cy, outer, mid, inner, t)
        self._paint_orb_shell(painter, cx, cy, outer, t)
        self._paint_orb_volume(painter, cx, cy, outer, mid, inner, t)
        self._paint_orb_core(painter, cx, cy, inner, t)

        painter.setPen(Qt.PenStyle.NoPen)
        center_glow = QRadialGradient(QPointF(cx, cy), inner * (0.42 + self.audio_level_display * 0.10))
        center_glow.setColorAt(0.0, QColor(255, 224, 176, 255))
        center_glow.setColorAt(0.24, QColor(255, 167, 70, 230))
        center_glow.setColorAt(0.52, QColor(255, 120, 34, 160))
        center_glow.setColorAt(1.0, QColor(255, 90, 24, 12))
        painter.setBrush(center_glow)
        painter.drawEllipse(QPointF(cx, cy), inner * (0.42 + self.audio_level_display * 0.08), inner * (0.42 + self.audio_level_display * 0.08))

        if self.face_pixmap is not None and not self.face_pixmap.isNull():
            face_size = max(110, int(core_r * 0.36))
            scaled = self.face_pixmap.scaled(face_size, face_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            painter.setOpacity(0.08)
            painter.drawPixmap(int(cx - scaled.width() / 2), int(cy - scaled.height() / 2), scaled)
            painter.setOpacity(1.0)
        painter.restore()

    def _paint_orb_depth_rings(self, painter: QPainter, cx, cy, outer, mid, inner, t):
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for radius, alpha in (
            (outer * 1.26, 18),
            (outer * 1.22, 24),
            (outer * 1.10, 30),
            (outer, 42),
            (mid * 1.03, 54),
        ):
            painter.setPen(QPen(QColor(255, 138, 54, alpha), 1))
            painter.drawEllipse(QPointF(cx, cy), radius, radius)

        for idx, radius in enumerate((outer * 0.96, outer * 0.92, outer * 0.82, mid * 0.95)):
            painter.setPen(QPen(QColor(255, 194, 126, 30 + idx * 18), 1))
            rect = QRectF(cx - radius, cy - radius * 0.66, radius * 2, radius * 1.32)
            painter.drawArc(rect, int(-(14 + t * (9 + idx * 2.1)) * 16), int(-(210 - idx * 20) * 16))

        painter.setPen(QPen(QColor(255, 170, 100, 40), 1))
        for idx in range(7):
            radius = outer * (0.46 + idx * 0.082)
            rect = QRectF(cx - radius, cy - radius * 0.42, radius * 2, radius * 0.84)
            painter.drawArc(rect, int(-(120 + t * (6 + idx)) * 16), int(-220 * 16))
        painter.restore()

    def _paint_orb_shell(self, painter: QPainter, cx, cy, outer, t):
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        segments = 180
        for idx in range(segments):
            a1 = math.radians((idx / segments) * 360.0)
            a2 = math.radians(((idx + 1) / segments) * 360.0)
            mod1 = 1.0 + 0.07 * math.sin(idx * 0.37 + t * 0.71) + 0.034 * math.sin(idx * 1.17 + t * 1.91)
            mod2 = 1.0 + 0.07 * math.sin((idx + 1) * 0.37 + t * 0.71) + 0.034 * math.sin((idx + 1) * 1.17 + t * 1.91)
            r1 = outer * mod1
            r2 = outer * mod2
            p1 = QPointF(cx + math.cos(a1) * r1, cy + math.sin(a1) * r1)
            p2 = QPointF(cx + math.cos(a2) * r2, cy + math.sin(a2) * r2)
            alpha = 95 + int(130 * (0.5 + 0.5 * math.sin(t * 3.2 + idx * 0.2)))
            width = 0.6 + 2.8 * (0.5 + 0.5 * math.sin(t * 2.8 + idx * 0.13)) + (self.audio_level_display * 1.5)
            painter.setPen(QPen(QColor(255, 145, 56, alpha), width))
            painter.drawLine(p1, p2)

            if idx % 3 == 0:
                spike = 8 + 26 * (0.5 + 0.5 * math.sin(t * 2.1 + idx * 0.49))
                direction = QPointF(math.cos(a1), math.sin(a1))
                painter.setPen(QPen(QColor(255, 176, 98, 85), 1))
                painter.drawLine(p1, QPointF(p1.x() + direction.x() * spike, p1.y() + direction.y() * spike))

            if idx % 9 == 0:
                trail = 16 + 22 * (0.5 + 0.5 * math.sin(t * 1.6 + idx * 0.21))
                tangent = QPointF(-math.sin(a1), math.cos(a1))
                painter.setPen(QPen(QColor(255, 210, 150, 55), 1))
                painter.drawLine(p1, QPointF(p1.x() + tangent.x() * trail, p1.y() + tangent.y() * trail))
        painter.restore()

    def _paint_orb_volume(self, painter: QPainter, cx, cy, outer, mid, inner, t):
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)

        for idx in range(18):
            angle = math.radians(idx * 20 + t * (7.0 + (idx % 4)))
            rot = angle * 0.45
            start = QPointF(cx + math.cos(angle) * mid * 0.94, cy + math.sin(angle) * mid * 0.62)
            ctrl = QPointF(cx + math.cos(angle + rot) * inner * 0.32, cy + math.sin(angle + rot) * inner * 0.26)
            end = QPointF(cx + math.cos(angle + 1.0) * inner * 0.24, cy + math.sin(angle + 1.0) * inner * 0.18)
            painter.setPen(QPen(QColor(255, 196, 128, 105), 1))
            path = QPainterPath(start)
            path.quadTo(ctrl, end)
            painter.drawPath(path)

        painter.setPen(QPen(QColor(255, 176, 104, 88), 1))
        for idx in range(16):
            spin = t * (5.2 + idx * 0.16)
            rx = mid * (0.18 + idx * 0.033)
            ry = rx * (0.34 + 0.10 * math.sin(t * 0.7 + idx))
            rect = QRectF(cx - rx, cy - ry, rx * 2, ry * 2)
            painter.drawArc(rect, int(-(spin * 180 / math.pi) * 16), int(-(120 + idx * 3) * 16))

        particle_count = 240 + int(self.audio_level_display * 60)
        for idx in range(particle_count):
            theta = (idx * 2.399963229728653) + t * (0.18 + (idx % 7) * 0.013)
            phi = math.sin(idx * 0.71 + t * 0.23) * 1.05
            x3 = math.cos(theta) * math.cos(phi)
            y3 = math.sin(phi)
            z3 = math.sin(theta) * math.cos(phi)
            sx = cx + x3 * outer * (0.78 + 0.22 * z3)
            sy = cy + y3 * outer * (0.74 + 0.16 * z3)
            alpha = 34 + int((z3 + 1.0) * 90)
            size = 0.35 + (z3 + 1.0) * 1.8 + (self.audio_level_display * 0.6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 170, 86, alpha))
            painter.drawEllipse(QPointF(sx, sy), size, size)

            if idx % 17 == 0:
                painter.setPen(QPen(QColor(255, 186, 116, min(140, alpha)), 1))
                painter.drawLine(
                    QPointF(sx, sy),
                    QPointF(sx - x3 * 6.0, sy - y3 * 6.0),
                )

        painter.setPen(QPen(QColor(255, 150, 70, 116), 1))
        for idx in range(12):
            tilt = math.radians(idx * 36 + t * (5.8 + idx * 0.22))
            rx = mid * (0.34 + idx * 0.034)
            ry = rx * 0.44
            rect = QRectF(cx - rx, cy - ry, rx * 2, ry * 2)
            painter.drawArc(rect, int(-tilt * 16 * 180 / math.pi), int(-185 * 16))
        painter.restore()

    def _paint_orb_core(self, painter: QPainter, cx, cy, inner, t):
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 182, 116, 170), 1))
        prev = None
        spiral_steps = 180 + int(self.audio_level_display * 40)
        for idx in range(spiral_steps):
            f = idx / max(1, spiral_steps - 1)
            angle = t * 2.9 + f * 9.8
            radius = inner * (0.06 + 0.60 * f)
            point = QPointF(cx + math.cos(angle) * radius, cy + math.sin(angle * 0.92) * radius * (0.78 + 0.10 * f))
            if prev is not None:
                painter.drawLine(prev, point)
            prev = point

        painter.setPen(QPen(QColor(255, 146, 60, 220), 2))
        painter.drawEllipse(QPointF(cx, cy), inner * 0.58, inner * 0.58)
        painter.setPen(QPen(QColor(255, 210, 150, 150), 1))
        painter.drawEllipse(QPointF(cx, cy), inner * 0.42, inner * 0.42)
        painter.drawEllipse(QPointF(cx, cy), inner * 0.28, inner * 0.28)
        painter.drawEllipse(QPointF(cx, cy), inner * 0.14, inner * 0.14)

        painter.setPen(QPen(QColor(255, 200, 140, 130), 1))
        for idx in range(14):
            angle = math.radians(idx * (360 / 14) + t * 22)
            start_r = inner * 0.05
            end_r = inner * 0.54
            painter.drawLine(
                QPointF(cx + math.cos(angle) * start_r, cy + math.sin(angle) * start_r),
                QPointF(cx + math.cos(angle) * end_r, cy + math.sin(angle) * end_r),
            )

        painter.setPen(QPen(QColor(255, 178, 98, 170), 1))
        for idx in range(10):
            angle = math.radians(idx * 36 + t * 18)
            tri = QPolygonF(
                [
                    QPointF(cx + math.cos(angle) * inner * 0.10, cy + math.sin(angle) * inner * 0.10),
                    QPointF(cx + math.cos(angle + 0.14) * inner * 0.22, cy + math.sin(angle + 0.14) * inner * 0.22),
                    QPointF(cx + math.cos(angle - 0.14) * inner * 0.22, cy + math.sin(angle - 0.14) * inner * 0.22),
                ]
            )
            painter.drawPolygon(tri)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 226, 175, 230))
        painter.drawEllipse(QPointF(cx, cy), inner * 0.08, inner * 0.08)
        painter.restore()

    def _paint_bottom_mode(self, painter: QPainter):
        w = self.width()
        h = self.height()
        rect = QRectF(w * 0.015, h * 0.845, w * 0.17, h * 0.12)
        left = int(rect.left() + 14)
        top = int(rect.top())

        painter.save()
        painter.setPen(QColor(255, 187, 132))
        painter.setFont(QFont("Helvetica", 10, QFont.Weight.DemiBold))
        painter.drawText(left, top + 22, "MODE")

        buttons = [("CONVERSATION", True), ("EXECUTION", False), ("ANALYSIS", False)]
        y = top + 34
        for label, active in buttons:
            btn = QRectF(left, y, rect.width() - 28, 26)
            painter.setBrush(QColor(62, 31, 15, 220) if active else QColor(16, 14, 16, 220))
            painter.setPen(QPen(QColor(255, 138, 49, 180) if active else QColor(65, 48, 38, 180), 1))
            painter.drawRoundedRect(btn, 8, 8)
            painter.setPen(QColor(255, 214, 176) if active else QColor(137, 118, 104))
            painter.setFont(QFont("Helvetica", 9, QFont.Weight.DemiBold))
            painter.drawText(int(btn.left() + 12), int(btn.top() + 17), label)
            y += 31
        painter.restore()

    def _paint_bottom_voice(self, painter: QPainter, t: float):
        w = self.width()
        h = self.height()
        rect = QRectF(w * 0.22, h * 0.85, w * 0.56, h * 0.09)
        left = int(rect.left() + 16)
        right = int(rect.right() - 16)
        center_y = rect.center().y()

        painter.save()
        painter.setPen(QPen(QColor(255, 139, 48, 220), 1.2))
        mid_x = rect.center().x()
        width = rect.width() - 40
        bars = 104
        for i in range(bars):
            x = rect.left() + 20 + (width * i / (bars - 1))
            dist = abs((x - mid_x) / (width / 2))
            shape = max(0.0, 1.0 - dist ** 1.8)
            wave = 0.14 + 0.86 * abs(math.sin(t * 3.8 + i * 0.24))
            amp = 2 + shape * wave * 34
            amp *= 0.55 + (self.audio_level_display * 1.8) + (0.25 if self.speaking else 0.0)
            painter.drawLine(QPointF(x, center_y - amp / 2), QPointF(x, center_y + amp / 2))
        painter.restore()

    def _paint_floor_trace(self, painter: QPainter, t: float):
        w = self.width()
        h = self.height()
        y = h * 0.86
        left = w * 0.24
        right = w * 0.76
        palette = self._mood_palette()
        painter.save()
        painter.setPen(QPen(QColor(palette["secondary"].red(), palette["secondary"].green(), palette["secondary"].blue(), 70), 1))
        painter.drawLine(int(left), int(y), int(right), int(y))
        for i in range(72):
            x = left + (right - left) * i / 71.0
            amp = 1.0 + (2.0 + self.audio_level_display * 8.0) * abs(math.sin(t * 2.8 + i * 0.28))
            painter.setPen(QPen(QColor(palette["primary"].red(), palette["primary"].green(), palette["primary"].blue(), 90 if i % 6 else 140), 1))
            painter.drawLine(QPointF(x, y - amp), QPointF(x, y + amp))
        painter.restore()

    def _paint_status_card(self, painter: QPainter, rect: QRectF, title: str, rows):
        painter.save()
        painter.setBrush(QColor(10, 10, 12, 160))
        painter.setPen(QPen(QColor(112, 52, 22, 110), 1))
        painter.drawRoundedRect(rect, 10, 10)
        painter.setPen(QColor(255, 198, 152))
        painter.setFont(QFont("Helvetica", 10, QFont.Weight.DemiBold))
        painter.drawText(int(rect.left() + 12), int(rect.top() + 22), title)
        painter.setPen(QPen(QColor(92, 48, 24, 120), 1))
        painter.drawLine(QPointF(rect.left() + 12, rect.top() + 28), QPointF(rect.right() - 12, rect.top() + 28))
        painter.setFont(QFont("Helvetica", 8))
        y = rect.top() + 52
        for label, value in rows:
            painter.setPen(QColor(136, 113, 96))
            painter.drawText(int(rect.left() + 12), int(y), label)
            painter.setPen(QColor(236, 220, 208))
            painter.drawText(int(rect.right() - 12 - self._text_width(painter, value)), int(y), value)
            y += 24
        painter.restore()

    def _paint_control_card(self, painter: QPainter, rect: QRectF, title: str, level: float, hint: str):
        painter.save()
        painter.setBrush(QColor(10, 10, 12, 160))
        painter.setPen(QPen(QColor(112, 52, 22, 110), 1))
        painter.drawRoundedRect(rect, 10, 10)
        painter.setPen(QColor(255, 198, 152))
        painter.setFont(QFont("Helvetica", 10, QFont.Weight.DemiBold))
        painter.drawText(int(rect.left() + 12), int(rect.top() + 22), title)
        bar = QRectF(rect.left() + 12, rect.top() + 40, rect.width() - 24, 12)
        painter.setBrush(QColor(22, 18, 16, 220))
        painter.setPen(QPen(QColor(83, 52, 34, 120), 1))
        painter.drawRoundedRect(bar, 6, 6)
        fill = QRectF(bar.left() + 1, bar.top() + 1, max(6.0, (bar.width() - 2) * level), bar.height() - 2)
        painter.setBrush(QColor(255, 141, 52, 220))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(fill, 5, 5)
        painter.setPen(QColor(150, 125, 106))
        painter.setFont(QFont("Helvetica", 8))
        painter.drawText(int(rect.left() + 12), int(rect.bottom() - 14), self._elide_text(painter, hint, int(rect.width()) - 24))
        painter.restore()

    def _paint_micro_wave(self, painter: QPainter, rect: QRectF, t: float, compact: bool = False):
        painter.save()
        center_y = rect.center().y()
        bars = 26 if compact else 30
        for i in range(bars):
            x = rect.left() + (rect.width() * i / (bars - 1))
            amp = (2 if compact else 4) + abs(math.sin(t * 4.0 + i * 0.35)) * (6 if compact else 12)
            color = QColor(255, 132, 48) if i % 7 == 0 else QColor(255, 186, 118)
            painter.setPen(QPen(color, 1))
            painter.drawLine(QPointF(x, center_y - amp / 2), QPointF(x, center_y + amp / 2))
        painter.restore()

    def _paint_mini_meter(self, painter: QPainter, rect: QRectF, t: float):
        painter.save()
        bars = 12
        for i in range(bars):
            x = rect.left() + i * (rect.width() / bars)
            h = 4 + abs(math.sin(t * 2.8 + i * 0.45)) * 10
            color = QColor(255, 208, 146, 220) if i < 8 else QColor(116, 98, 84, 180)
            painter.setPen(QPen(color, 3))
            painter.drawLine(QPointF(x, rect.bottom()), QPointF(x, rect.bottom() - h))
        painter.restore()

    def _draw_arc(self, painter: QPainter, cx, cy, radius, start_deg, sweep_deg, color: QColor, width: int):
        painter.save()
        painter.setPen(QPen(color, width, cap=Qt.PenCapStyle.RoundCap))
        rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        painter.drawArc(rect, int(-start_deg * 16), int(-sweep_deg * 16))
        painter.restore()

    def _typewriter_text(self):
        if not self.recent_logs:
            return "FRIDAY: Ready for your command, boss."
        text = self.recent_logs[-1]
        reveal = min(len(text), max(1, self._type_index // 2 % (len(text) + 1)))
        return text[:reveal]

    def _text_width(self, painter: QPainter, text: str):
        return painter.fontMetrics().horizontalAdvance(text)

    def _clip_value(self, text: str, limit: int):
        text = (text or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    def _fit_text(self, text: str, limit: int):
        text = (text or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    def _elide_text(self, painter: QPainter, text: str, max_width: int):
        metrics = painter.fontMetrics()
        return metrics.elidedText((text or "").strip(), Qt.TextElideMode.ElideRight, max_width)

    def _wrap_text(self, painter: QPainter, text: str, max_width: int, max_lines: int):
        words = (text or "").strip().split()
        if not words:
            return [""]
        lines = []
        current = ""
        for word in words:
            trial = word if not current else f"{current} {word}"
            if painter.fontMetrics().horizontalAdvance(trial) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = word
                if len(lines) >= max_lines - 1:
                    break
        if len(lines) < max_lines and current:
            lines.append(self._elide_text(painter, current, max_width))
        elif lines:
            lines[-1] = self._elide_text(painter, lines[-1], max_width)
        return lines[:max_lines]

    def _stats_loop(self):
        last_net = psutil.net_io_counters()
        last_time = time.time()
        while self._stats_running:
            now = time.time()
            interval = max(0.25, now - last_time)
            net = psutil.net_io_counters()
            upload_kbps = ((net.bytes_sent - last_net.bytes_sent) * 8 / 1000.0) / interval
            download_kbps = ((net.bytes_recv - last_net.bytes_recv) * 8 / 1000.0) / interval
            battery = None
            plugged = False
            try:
                battery_info = psutil.sensors_battery()
                if battery_info is not None:
                    battery = float(battery_info.percent)
                    plugged = bool(battery_info.power_plugged)
            except Exception:
                battery = None
                plugged = False

            with self.stats_lock:
                self.stats = {
                    "cpu": float(psutil.cpu_percent(interval=None)),
                    "ram": float(psutil.virtual_memory().percent),
                    "upload_kbps": max(0.0, upload_kbps),
                    "download_kbps": max(0.0, download_kbps),
                    "battery": battery,
                    "battery_plugged": plugged,
                }
            last_net = net
            last_time = now
            time.sleep(0.45)


class JarvisUI:
    def __init__(self, face_path, size=None):
        del size
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.bridge = UIBridge()
        self.window = HUDWindow(face_path, self.bridge)
        self.window.show()

    def run(self):
        self.app.exec()

    def wait_for_api_key(self):
        self.window.api_ready_event.wait()

    def write_log(self, text: str):
        self.bridge.log_message.emit(text)

    def start_speaking(self):
        self.bridge.speaking_changed.emit(True)

    def stop_speaking(self):
        self.bridge.speaking_changed.emit(False)

    def update_audio_level(self, level: float):
        self.bridge.audio_level_changed.emit(float(level))

    def update_weather(self, city, summary, temperature="--", humidity="--", wind="--", updated_at=None):
        del city, summary, temperature, humidity, wind, updated_at

    def add_reminder(self, message, when_text, status="SCHEDULED"):
        self.bridge.reminder_added.emit(message, when_text, status)

    def refresh_memory_views(self):
        self.bridge.refresh_requested.emit()

    def shutdown(self):
        self.bridge.shutdown_requested.emit()
