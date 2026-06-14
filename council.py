import sys
import os
import json
import math
import random
import requests
import subprocess
import datetime
import platform
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QLineEdit, QPushButton, QLabel, QFrame, QSizePolicy,
    QSpinBox, QDialog, QDialogButtonBox, QColorDialog, QFileDialog,
    QScrollArea, QComboBox, QSlider, QCheckBox, QFontComboBox,
    QGraphicsOpacityEffect, QListWidget, QSystemTrayIcon, QMenu
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QPropertyAnimation,
    QEasingCurve, QRect, QPoint, QSize, QRectF
)
from PyQt6.QtGui import (
    QFont, QColor, QIcon, QPixmap, QPainter, QPen, QBrush,
    QLinearGradient, QRadialGradient, QPainterPath, QAction
)

# ── OS detection ───────────────────────────────────────────────────────────
PLATFORM = platform.system()   # "Linux", "Windows", "Darwin"
IS_LINUX   = PLATFORM == "Linux"
IS_WINDOWS = PLATFORM == "Windows"
IS_MAC     = PLATFORM == "Darwin"

# ── paths ──────────────────────────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR    = os.path.join(BASE_DIR, "assets")
DEBATES_DIR   = os.path.join(BASE_DIR, "debates")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
os.makedirs(DEBATES_DIR, exist_ok=True)

# ── globals ────────────────────────────────────────────────────────────────
LLAMA_HOST      = "http://127.0.0.1:8080"
LLAMA           = f"{LLAMA_HOST}/v1/chat/completions"
CURRENT_BACKEND = "llama.cpp"
CURRENT_MODEL   = ""

DEFAULT_SETTINGS = {
    "accent":         "#C8A96E",
    "card_bg":        "#1a1a1a",
    "bg_color":       "#0f0f0f",
    "font_family":    "Georgia",
    "font_size":      12,
    "temperature":    0.75,
    "max_tokens":     500,
    "ctx_override":   0,
    "rounds":         2,
    "show_moderator": True,
    "notifications":  True,
    "auto_save":      True,
    "backend":        "llama.cpp",
    "port":           "8080",
    "model_name":     "",
    "search_enabled": False,
    "search_backend": "DuckDuckGo",
    "search_api_key": "",
    "search_url":     "http://localhost:8888",
    "search_results": 3,
}
settings = dict(DEFAULT_SETTINGS)

def load_settings():
    global settings
    try:
        with open(SETTINGS_FILE) as f:
            settings.update(json.load(f))
    except FileNotFoundError:
        # First run — no settings file yet; write defaults so next launch is instant
        save_settings()
    except Exception:
        pass

def save_settings():
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass

load_settings()

# ── cross-platform desktop notification ────────────────────────────────────
_tray_icon: QSystemTrayIcon | None = None   # set up after QApplication exists

def _setup_tray(app_icon: QIcon | None = None):
    """Call once after QApplication is created."""
    global _tray_icon
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return
    _tray_icon = QSystemTrayIcon()
    if app_icon:
        _tray_icon.setIcon(app_icon)
    else:
        _tray_icon.setIcon(QIcon.fromTheme("dialog-information"))
    menu = QMenu()
    quit_action = QAction("Quit")
    quit_action.triggered.connect(QApplication.quit)
    menu.addAction(quit_action)
    _tray_icon.setContextMenu(menu)
    _tray_icon.show()

def send_notification(title: str, body: str):
    """Send a desktop notification cross-platform."""
    if not settings.get("notifications"):
        return
    # 1. Qt system tray (works everywhere Qt runs)
    if _tray_icon and _tray_icon.isVisible():
        _tray_icon.showMessage(title, body,
                               QSystemTrayIcon.MessageIcon.Information, 4000)
        return
    # 2. OS-native fallbacks (best-effort, never raise)
    try:
        if IS_LINUX:
            subprocess.Popen(["notify-send", title, body],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif IS_MAC:
            script = (f'display notification "{body}" with title "{title}"')
            subprocess.Popen(["osascript", "-e", script],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif IS_WINDOWS:
            # winotify / plyer optional; fall back silently if absent
            try:
                from winotify import Notification, audio
                toast = Notification(app_id="The Council",
                                     title=title, msg=body)
                toast.show()
            except ImportError:
                try:
                    from plyer import notification as plyer_notif
                    plyer_notif.notify(title=title, message=body,
                                       app_name="The Council", timeout=4)
                except ImportError:
                    pass
    except Exception:
        pass

# ── personas ───────────────────────────────────────────────────────────────
personas = [
    {"name": "The Analyst",         "color": "#C8A96E",
     "system": "You are a sharp analytical thinker on a decision-making council. Break problems into parts, identify risks, look for logical inconsistencies. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
    {"name": "The Devil's Advocate", "color": "#C0392B",
     "system": "You are a devil's advocate on a decision-making council. Challenge the direction the group is leaning, poke holes in assumptions, surface uncomfortable truths. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
    {"name": "The Pragmatist",       "color": "#7F8C8D",
     "system": "You are a pragmatic realist on a decision-making council. Ask what is actually achievable given real constraints. Push for the simplest thing that could work. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
    {"name": "The Empath",           "color": "#8E6DB5",
     "system": "You are an emotionally intelligent advisor on a decision-making council. Consider the human side of every decision. Push back when the group is too cold. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
    {"name": "The Visionary",        "color": "#2E86AB",
     "system": "You are a big-picture thinker on a decision-making council. Ask what the long-term consequences are. Challenge short-term thinking. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
]

MODERATOR_SYSTEM = (
    "You are a neutral moderator summarizing a council debate. "
    "Synthesize key points of agreement and disagreement, then give a clear "
    "actionable recommendation. Be concise. End with a single clear recommendation."
)

# ── search ─────────────────────────────────────────────────────────────────
def web_search(query):
    n       = settings["search_results"]
    sources = []
    try:
        b = settings["search_backend"]
        if b == "DuckDuckGo":
            r = requests.get("https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                timeout=5)
            data = r.json()
            out  = []
            if data.get("AbstractText"):
                out.append(data["AbstractText"])
                if data.get("AbstractURL"):
                    sources.append(data["AbstractURL"])
            for t in data.get("RelatedTopics", [])[:n]:
                if isinstance(t, dict) and t.get("Text"):
                    out.append(t["Text"])
                    if t.get("FirstURL"):
                        sources.append(t["FirstURL"])
            return "\n\n".join(out[:n]), sources
        elif b == "SearXNG":
            r     = requests.get(
                settings["search_url"].rstrip("/") + "/search",
                params={"q": query, "format": "json"}, timeout=5)
            items = r.json().get("results", [])[:n]
            sources = [x.get("url", "") for x in items if x.get("url")]
            return "\n\n".join(
                f"{x.get('title','')}: {x.get('content','')}" for x in items), sources
        elif b in ("Brave Search", "Tavily"):
            key = settings["search_api_key"]
            if not key:
                return "[no API key set]", []
            if b == "Brave Search":
                r     = requests.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    headers={"Accept": "application/json",
                             "X-Subscription-Token": key},
                    params={"q": query, "count": n}, timeout=5)
                items = r.json().get("web", {}).get("results", [])[:n]
                sources = [x.get("url", "") for x in items if x.get("url")]
                return "\n\n".join(
                    f"{x['title']}: {x.get('description','')}" for x in items), sources
            else:
                r     = requests.post(
                    "https://api.tavily.com/search",
                    json={"api_key": key, "query": query, "max_results": n},
                    timeout=5)
                items = r.json().get("results", [])[:n]
                sources = [x.get("url", "") for x in items if x.get("url")]
                return "\n\n".join(
                    f"{x['title']}: {x.get('content','')}" for x in items), sources
    except Exception as e:
        return f"[search error: {e}]", []
    return "", []

# ── context ────────────────────────────────────────────────────────────────
def get_context_size(backend, model_name=""):
    if settings["ctx_override"] > 0:
        return settings["ctx_override"]
    try:
        if backend in ("llama.cpp", "Odysseus"):
            r = requests.get(f"{LLAMA_HOST}/props", timeout=2)
            n = r.json().get("n_ctx")
            if n: return n
        elif backend == "Ollama":
            r   = requests.post(f"{LLAMA_HOST}/api/show",
                                json={"name": model_name}, timeout=2)
            ctx = r.json().get("model_info", {}).get("llama.context_length")
            if ctx: return ctx
        elif backend == "koboldcpp":
            r = requests.get(
                f"{LLAMA_HOST}/api/extra/true_max_context_length", timeout=2)
            n = r.json().get("value")
            if n: return n
        elif backend == "Oobabooga":
            r   = requests.get(f"{LLAMA_HOST}/api/v1/model", timeout=2)
            ctx = r.json().get("result", {}).get("truncation_length")
            if ctx: return ctx
        elif backend == "TabbyAPI":
            r   = requests.get(f"{LLAMA_HOST}/v1/model", timeout=2)
            ctx = r.json().get("parameters", {}).get("max_seq_len")
            if ctx: return ctx
        elif backend in ("LM Studio", "Jan", "OpenAI / OpenRouter"):
            r      = requests.get(f"{LLAMA_HOST}/v1/models", timeout=2)
            models = r.json().get("data", [])
            if models:
                ctx = models[0].get("context_length")
                if ctx: return ctx
    except Exception:
        pass
    return 4096

# ── worker ─────────────────────────────────────────────────────────────────
class CouncilWorker(QThread):
    persona_start = pyqtSignal(str)
    persona_done  = pyqtSignal(str, str, int)
    round_start   = pyqtSignal(int)
    verdict_start = pyqtSignal()
    verdict_done  = pyqtSignal(str)
    search_done   = pyqtSignal(str)
    finished      = pyqtSignal()

    def __init__(self, question, rounds):
        super().__init__()
        self.question = question
        self.rounds   = rounds
        self.ctx_size = get_context_size(CURRENT_BACKEND, CURRENT_MODEL)

    def ask(self, system, messages):
        try:
            r = requests.post(LLAMA, json={
                "model": CURRENT_MODEL if CURRENT_MODEL else "local",
                "messages": [{"role": "system", "content": system}] + messages,
                "temperature": settings.get("temperature", 0.7),
                "max_tokens":  min(settings.get("max_tokens", 512),
                                   self.ctx_size // 16),
            }, timeout=30)
            data = r.json()
            return (data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "[no response]"))
        except Exception as e:
            return f"[error: {e}]"

    def run(self):
        search_context = ""
        if settings["search_enabled"]:
            results, sources = web_search(self.question)
            if results:
                search_context = f"\n\nCurrent web search results:\n{results}\n"
                sources_text   = "\n".join(sources) if sources else "No sources found"
                self.search_done.emit(
                    f"SOURCES:\n{sources_text}\n\nCONTEXT:\n{results}")

        histories = {
            p["name"]: [{"role": "user",
                          "content": self.question + search_context}]
            for p in personas
        }
        responses = {}

        for round_num in range(self.rounds):
            self.round_start.emit(round_num + 1)
            new_responses = {}
            for p in personas:
                self.persona_start.emit(p["name"])
                if round_num > 0:
                    others = "\n\n".join(
                        f"{name}: {text[:self.ctx_size // 20]}"
                        for name, text in responses.items()
                        if name != p["name"]
                    )
                    histories[p["name"]].append({
                        "role":    "user",
                        "content": (f"The other council members said:\n\n"
                                    f"{others}\n\nRespond to them.")
                    })
                reply = self.ask(p["system"], histories[p["name"]])
                new_responses[p["name"]] = reply
                histories[p["name"]].append({"role": "assistant", "content": reply})
                self.persona_done.emit(p["name"], reply, round_num + 1)
            responses = new_responses

        self.verdict_start.emit()
        all_responses = "\n\n".join(
            f"{n}: {t[:self.ctx_size // 20]}" for n, t in responses.items()
        )
        summary = self.ask(
            MODERATOR_SYSTEM,
            [{"role": "user",
              "content": f"The council debated: '{self.question}'\n\n{all_responses}"}]
        )
        self.verdict_done.emit(summary)
        self.finished.emit()

# ── particle background ────────────────────────────────────────────────────
class ParticleBackground(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.particles = []
        self._init_particles()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def _init_particles(self):
        self.particles = []
        for _ in range(40):
            self.particles.append({
                "x":     random.random(),
                "y":     random.random(),
                "vx":    (random.random() - 0.5) * 0.0008,
                "vy":    (random.random() - 0.5) * 0.0008,
                "size":  random.uniform(1, 2.5),
                "alpha": random.uniform(0.03, 0.12),
            })

    def _tick(self):
        for p in self.particles:
            p["x"] = (p["x"] + p["vx"]) % 1.0
            p["y"] = (p["y"] + p["vy"]) % 1.0
        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = QColor(settings.get("accent", "#C8A96E"))
        w, h = self.width(), self.height()
        for p in self.particles:
            color = QColor(a)
            color.setAlphaF(p["alpha"])
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(
                QRectF(p["x"] * w - p["size"], p["y"] * h - p["size"],
                       p["size"] * 2, p["size"] * 2)
            )
        painter.end()

    def resizeEvent(self, e):
        super().resizeEvent(e)

# ── animated title line ────────────────────────────────────────────────────
class TitleLine(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self._phase  = 0.0
        self._active = False
        self._timer  = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

    def set_active(self, active):
        self._active = active

    def _tick(self):
        if self._active:
            self._phase = (self._phase + 0.05) % (2 * math.pi)
            self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        a = QColor(settings.get("accent", "#C8A96E"))
        if self._active:
            grad   = QLinearGradient(0, 0, w, 0)
            center = (math.sin(self._phase) + 1) / 2
            grad.setColorAt(max(0, center - 0.3),
                            QColor(a.red(), a.green(), a.blue(), 20))
            grad.setColorAt(center,
                            QColor(a.red(), a.green(), a.blue(), 200))
            grad.setColorAt(min(1, center + 0.3),
                            QColor(a.red(), a.green(), a.blue(), 20))
        else:
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0,   QColor(a.red(), a.green(), a.blue(), 0))
            grad.setColorAt(0.5, QColor(a.red(), a.green(), a.blue(), 60))
            grad.setColorAt(1,   QColor(a.red(), a.green(), a.blue(), 0))
        painter.fillRect(0, 0, w, 3, QBrush(grad))
        painter.end()

# ── persona card ───────────────────────────────────────────────────────────
class PersonaCard(QFrame):
    def __init__(self, name, color):
        super().__init__()
        self.name      = name
        self.color     = color
        self.collapsed = False
        self._pulse_timer  = QTimer()
        self._pulse_state  = False
        self._think_states = ["thinking.", "thinking..", "thinking..."]
        self._think_index  = 0
        self._pulse_timer.timeout.connect(self._do_pulse)
        self.setMinimumWidth(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._apply_idle_style()

        # fade-in effect
        self._opacity  = QGraphicsOpacityEffect()
        self._opacity.setOpacity(1.0)
        self._fade_anim = QPropertyAnimation(self._opacity, b"opacity")
        self._fade_anim.setDuration(400)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        L = QVBoxLayout(self)
        L.setContentsMargins(12, 10, 12, 12)
        L.setSpacing(8)

        hrow = QHBoxLayout()
        self.name_label = QLabel(name)
        self.name_label.setFont(QFont(settings["font_family"], 10))
        self.name_label.setStyleSheet(
            f"color:{color};font-size:11px;font-weight:bold;"
            f"letter-spacing:1px;border:none;background:transparent;")
        hrow.addWidget(self.name_label)
        hrow.addStretch()

        self.collapse_btn = QPushButton("▾")
        self.collapse_btn.setFixedSize(20, 20)
        self.collapse_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;color:{color}88;"
            f"font-size:14px;}}"
            f"QPushButton:hover{{color:{color};}}")
        self.collapse_btn.clicked.connect(self.toggle_collapse)
        hrow.addWidget(self.collapse_btn)
        L.addLayout(hrow)

        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFont(settings["font_family"], settings["font_size"]))
        self.text.setStyleSheet(
            "QTextEdit{background:transparent;border:none;color:#cccccc;}"
            "QScrollBar:vertical{width:4px;background:#111;}"
            "QScrollBar::handle:vertical{background:#333;border-radius:2px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}")
        self.text.setMinimumHeight(120)
        L.addWidget(self.text)

        self.copy_btn = QPushButton("COPY")
        self.copy_btn.setFixedHeight(22)
        self.copy_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:1px solid {color}44;"
            f"color:{color}88;font-size:9px;letter-spacing:1px;border-radius:2px;}}"
            f"QPushButton:hover{{border-color:{color}aa;color:{color};}}")
        self.copy_btn.clicked.connect(self.copy_text)
        L.addWidget(self.copy_btn)

    def copy_text(self):
        QApplication.clipboard().setText(self.text.toPlainText())
        self.copy_btn.setText("COPIED")
        QTimer.singleShot(1500, lambda: self.copy_btn.setText("COPY"))

    def toggle_collapse(self):
        self.collapsed = not self.collapsed
        self.text.setVisible(not self.collapsed)
        self.copy_btn.setVisible(not self.collapsed)
        self.collapse_btn.setText("▸" if self.collapsed else "▾")

    def _bg(self):
        return settings.get("card_bg", "#1a1a1a")

    def _apply_idle_style(self):
        self.setStyleSheet(
            f"QFrame{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f"stop:0 {self._bg()},stop:1 #0d0d0d);"
            f"border:3px solid {self.color};"
            f"border-radius:6px;}}")

    def _apply_thinking_style(self, op):
        c = QColor(self.color)
        c.setAlpha(op)
        rgba = f"rgba({c.red()},{c.green()},{c.blue()},{op})"
        self.setStyleSheet(
            f"QFrame{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f"stop:0 {self._bg()},stop:1 #0d0d0d);"
            f"border:3px solid {rgba};"
            f"border-radius:6px;}}")

    def _do_pulse(self):
        self._pulse_phase = getattr(self, '_pulse_phase', 0.0)
        self._pulse_phase = (self._pulse_phase + 0.05) % (2 * math.pi)
        op = int(abs(math.sin(self._pulse_phase)) * 255)
        self._apply_thinking_style(op)
        self._pulse_tick = getattr(self, '_pulse_tick', 0) + 1
        if self._pulse_tick % 8 == 0:
            self._think_index = (self._think_index + 1) % len(self._think_states)
            self.text.setPlainText(self._think_states[self._think_index])

    def start_thinking(self):
        self._think_index = 0
        self.text.setPlainText(self._think_states[0])
        self._pulse_timer.start(50)

    def stop_thinking(self):
        self._pulse_timer.stop()

    def set_waiting(self):
        self.stop_thinking()
        self.text.setPlainText("...")
        self._apply_idle_style()

    def set_active(self, text):
        self.stop_thinking()
        self.text.setFont(QFont(settings["font_family"], settings["font_size"]))
        self.text.setGraphicsEffect(self._opacity)
        self._opacity.setOpacity(0.0)
        self.text.setPlainText(text)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()
        self._apply_idle_style()

    def refresh_style(self):
        font = QFont(settings["font_family"], settings["font_size"])
        self.text.setFont(font)
        existing = self.text.toPlainText()
        if existing and existing not in (
                "...", "thinking.", "thinking..", "thinking..."):
            self.text.setPlainText(existing)
        self._apply_idle_style()

    def update_color(self, color):
        self.color = color
        self.name_label.setStyleSheet(
            f"color:{color};font-size:11px;font-weight:bold;"
            f"letter-spacing:1px;border:none;background:transparent;")
        self.collapse_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;color:{color}88;"
            f"font-size:14px;}}"
            f"QPushButton:hover{{color:{color};}}")
        self.copy_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:1px solid {color}44;"
            f"color:{color}88;font-size:9px;letter-spacing:1px;border-radius:2px;}}"
            f"QPushButton:hover{{border-color:{color}aa;color:{color};}}")
        self._apply_idle_style()

# ── round indicator ────────────────────────────────────────────────────────
class RoundIndicator(QWidget):
    def __init__(self, total_rounds=2):
        super().__init__()
        self.setFixedHeight(28)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self.pills = []
        self._build(total_rounds)

    def _pill_style(self, state):
        a = settings["accent"]
        if state == "done":
            return (f"QLabel{{background:#1a1a1a;border:1px solid {a}44;color:{a}66;"
                    f"font-size:9px;letter-spacing:1px;padding:2px 10px;"
                    f"border-radius:11px;}}")
        if state == "active":
            return (f"QLabel{{background:{a}22;border:1px solid {a};color:{a};"
                    f"font-size:9px;letter-spacing:1px;padding:2px 10px;"
                    f"border-radius:11px;}}")
        return ("QLabel{background:#111;border:1px solid #2a2a2a;color:#333;"
                "font-size:9px;letter-spacing:1px;padding:2px 10px;border-radius:11px;}")

    def _build(self, n):
        for i in reversed(range(self._layout.count())):
            w = self._layout.itemAt(i).widget()
            if w: w.deleteLater()
        self.pills = []
        for i in range(n):
            pill = QLabel(f"Round {i+1}")
            pill.setFixedHeight(22)
            pill.setStyleSheet(self._pill_style("idle"))
            pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._layout.addWidget(pill)
            self.pills.append(pill)
        stretch = QWidget()
        stretch.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._layout.addWidget(stretch)

    def set_rounds(self, n): self._build(n)

    def set_round(self, r):
        for i, pill in enumerate(self.pills):
            if i + 1 < r:    pill.setStyleSheet(self._pill_style("done"))
            elif i + 1 == r: pill.setStyleSheet(self._pill_style("active"))
            else:            pill.setStyleSheet(self._pill_style("idle"))

    def reset(self):
        for pill in self.pills:
            pill.setStyleSheet(self._pill_style("idle"))

# ── persona editor ─────────────────────────────────────────────────────────
class PersonaEditorDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Council Personas")
        self.setMinimumSize(700, 500)
        self.setStyleSheet(
            "QDialog{background:#111;} QWidget{background:#111;color:#ccc;} "
            "QLabel{background:transparent;}"
            "QTextEdit,QLineEdit{background:#1a1a1a;border:1px solid #333;color:#eee;"
            "padding:6px;border-radius:2px;} QScrollArea{border:none;}")
        self.color_buttons = []
        self.name_edits    = []
        self.system_edits  = []

        main = QVBoxLayout(self)
        main.setSpacing(12)
        title = QLabel("COUNCIL PERSONAS")
        title.setStyleSheet(
            f"color:{settings['accent']};font-size:11px;"
            f"letter-spacing:3px;font-weight:bold;")
        main.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        sw = QWidget()
        sl = QVBoxLayout(sw)
        sl.setSpacing(16)

        for i, p in enumerate(personas):
            group = QFrame()
            group.setStyleSheet(
                "QFrame{border:1px solid #2a2a2a;border-radius:4px;padding:4px;}")
            gl = QVBoxLayout(group)
            gl.setSpacing(8)
            row1 = QHBoxLayout()
            ne = QLineEdit(p["name"])
            ne.setFixedHeight(32)
            self.name_edits.append(ne)
            row1.addWidget(ne)
            cb = QPushButton("  COLOR")
            cb.setFixedSize(90, 32)
            cb.setStyleSheet(
                f"background:{p['color']};color:#111;font-size:10px;"
                f"font-weight:bold;border:none;border-radius:2px;")
            cb.clicked.connect(lambda checked, idx=i: self.pick_color(idx))
            self.color_buttons.append(cb)
            row1.addWidget(cb)
            gl.addLayout(row1)
            se = QTextEdit(p["system"])
            se.setMinimumHeight(80)
            self.system_edits.append(se)
            gl.addWidget(se)
            sl.addWidget(group)

        scroll.setWidget(sw)
        main.addWidget(scroll)
        btn_row = QHBoxLayout()

        reset_all_btn = QPushButton("Reset to Defaults")
        reset_all_btn.setStyleSheet(
            "QPushButton{background:#1a1a1a;border:1px solid #333;color:#666;"
            "padding:6px 20px;border-radius:2px;font-size:11px;}"
            "QPushButton:hover{border-color:#c0392b;color:#c0392b;}")
        reset_all_btn.clicked.connect(self.reset_to_defaults)
        btn_row.addWidget(reset_all_btn)
        btn_row.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.setStyleSheet(
            "QPushButton{background:#1a1a1a;border:1px solid #333;color:#ccc;"
            "padding:6px 20px;border-radius:2px;}"
            "QPushButton:hover{border-color:#C8A96E;color:#C8A96E;}")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        btn_row.addWidget(buttons)
        main.addLayout(btn_row)

    def pick_color(self, idx):
        c = QColorDialog.getColor(QColor(personas[idx]["color"]), self)
        if c.isValid():
            h = c.name()
            self.color_buttons[idx].setStyleSheet(
                f"background:{h};color:#111;font-size:10px;font-weight:bold;"
                f"border:none;border-radius:2px;")
            self.color_buttons[idx].setProperty("chosen_color", h)

    def get_values(self):
        result = []
        for i in range(len(personas)):
            color = (self.color_buttons[i].property("chosen_color")
                     or personas[i]["color"])
            result.append({
                "name":   self.name_edits[i].text().strip(),
                "color":  color,
                "system": self.system_edits[i].toPlainText().strip()
            })
        return result

    def reset_to_defaults(self):
        defaults = [
            {"name": "The Analyst",         "color": "#C8A96E",
             "system": "You are a sharp analytical thinker on a decision-making council. Break problems into parts, identify risks, look for logical inconsistencies. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
            {"name": "The Devil's Advocate", "color": "#C0392B",
             "system": "You are a devil's advocate on a decision-making council. Challenge the direction the group is leaning, poke holes in assumptions, surface uncomfortable truths. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
            {"name": "The Pragmatist",       "color": "#7F8C8D",
             "system": "You are a pragmatic realist on a decision-making council. Ask what is actually achievable given real constraints. Push for the simplest thing that could work. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
            {"name": "The Empath",           "color": "#8E6DB5",
             "system": "You are an emotionally intelligent advisor on a decision-making council. Consider the human side of every decision. Push back when the group is too cold. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
            {"name": "The Visionary",        "color": "#2E86AB",
             "system": "You are a big-picture thinker on a decision-making council. Ask what the long-term consequences are. Challenge short-term thinking. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
        ]
        for i, d in enumerate(defaults):
            self.name_edits[i].setText(d["name"])
            self.system_edits[i].setPlainText(d["system"])
            self.color_buttons[i].setStyleSheet(
                f"background:{d['color']};color:#111;font-size:10px;"
                f"font-weight:bold;border:none;border-radius:2px;")
            self.color_buttons[i].setProperty("chosen_color", d["color"])

# ── settings panel ─────────────────────────────────────────────────────────
class SettingsPanel(QFrame):
    changed = pyqtSignal()

    COMBO_STYLE = """
        QComboBox {
            background: #1a1a1a; border: 1px solid #444; color: #C8A96E;
            padding: 4px 8px; font-size: 11px; border-radius: 2px;
        }
        QComboBox::drop-down { width: 0px; border: none; background: transparent; }
        QComboBox::down-arrow { image: none; width: 0px; height: 0px; }
        QComboBox QAbstractItemView {
            background: #1a1a1a; border: 1px solid #444; color: #C8A96E;
            selection-background-color: #2a2a2a; outline: none;
        }
    """
    PANEL_WIDTH = 400

    def __init__(self, parent):
        super().__init__(parent)
        self.setVisible(False)
        self.setFixedWidth(self.PANEL_WIDTH)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            SettingsPanel {
                background: #0c0c0c;
                border-left: 1px solid #333;
                border-radius: 0px;
            }
            QLabel { background: transparent; color: #999; font-size: 11px; }
            QLineEdit {
                background: #1a1a1a; border: 1px solid #444; color: #eee;
                padding: 4px 8px; border-radius: 2px; font-size: 12px;
            }
            QCheckBox { color: #999; font-size: 11px; spacing: 8px; }
            QCheckBox::indicator {
                width: 14px; height: 14px;
                background: #1a1a1a; border: 1px solid #444; border-radius: 2px;
            }
            QCheckBox::indicator:checked { background: #C8A96E; border-color: #C8A96E; }
            QSpinBox {
                background: #1a1a1a; border: 1px solid #444;
                color: #C8A96E; padding: 4px 8px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 16px; background: #222; border: none;
            }
            QSlider::groove:horizontal {
                height: 4px; background: #2a2a2a; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 14px; height: 14px; background: #C8A96E;
                border-radius: 7px; margin: -5px 0;
            }
            QSlider::sub-page:horizontal { background: #C8A96E; border-radius: 2px; }
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { width: 4px; background: #0c0c0c; }
            QScrollBar::handle:vertical { background: #2a2a2a; border-radius: 2px; }
        """)

        # slide animation
        self._anim = QPropertyAnimation(self, b"maximumWidth")
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # fixed header
        hdr_widget = QWidget()
        hdr_widget.setStyleSheet(
            "background:#0c0c0c;border-bottom:1px solid #1e1e1e;")
        hdr_widget.setFixedHeight(50)
        hdr = QHBoxLayout(hdr_widget)
        hdr.setContentsMargins(18, 0, 14, 0)
        t = QLabel("SETTINGS")
        t.setStyleSheet(
            f"color:{settings['accent']};font-size:12px;letter-spacing:3px;"
            f"font-weight:bold;background:transparent;")
        hdr.addWidget(t)
        hdr.addStretch()
        close_btn = QPushButton("✕  Close")
        close_btn.setFixedHeight(28)
        close_btn.setStyleSheet(
            "QPushButton{background:#1a1a1a;border:1px solid #444;color:#aaa;"
            "font-size:11px;padding:0 10px;border-radius:2px;}"
            "QPushButton:hover{background:#2a2a2a;color:#fff;border-color:#666;}")
        close_btn.clicked.connect(self.hide_panel)
        hdr.addWidget(close_btn)
        outer.addWidget(hdr_widget)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        L = QVBoxLayout(inner)
        L.setContentsMargins(18, 16, 40, 24)
        L.setSpacing(10)

        def sep(title):
            L.addSpacing(6)
            lbl = QLabel(title)
            lbl.setStyleSheet(
                "color:#444;font-size:9px;letter-spacing:2px;font-weight:bold;")
            L.addWidget(lbl)
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setStyleSheet("background:#1e1e1e;border:none;max-height:1px;")
            L.addWidget(line)

        def lrow(lbl_text, widget, lw=110):
            r = QHBoxLayout()
            r.setSpacing(8)
            lbl = QLabel(lbl_text)
            lbl.setFixedWidth(lw)
            lbl.setStyleSheet("color:#888;font-size:11px;background:transparent;")
            r.addWidget(lbl)
            r.addWidget(widget)
            r.addStretch()
            L.addLayout(r)

        def srow(lbl_text, slider, val_lbl):
            r = QHBoxLayout()
            r.setSpacing(8)
            lbl = QLabel(lbl_text)
            lbl.setFixedWidth(110)
            lbl.setStyleSheet("color:#888;font-size:11px;background:transparent;")
            slider.setMaximumWidth(160)
            val_lbl.setFixedWidth(32)
            val_lbl.setStyleSheet(
                "color:#888;font-size:10px;background:transparent;")
            r.addWidget(lbl)
            r.addWidget(slider)
            r.addWidget(val_lbl)
            r.addStretch()
            L.addLayout(r)

        def color_row(lbl_text, btn, default_hex, key):
            r = QHBoxLayout()
            r.setSpacing(6)
            lbl = QLabel(lbl_text)
            lbl.setFixedWidth(110)
            lbl.setStyleSheet("color:#888;font-size:11px;background:transparent;")
            reset = QPushButton("Default")
            reset.setFixedSize(58, 26)
            reset.setStyleSheet(
                "QPushButton{background:#1a1a1a;border:1px solid #333;color:#777;"
                "font-size:10px;padding:0 4px;border-radius:2px;}"
                "QPushButton:hover{border-color:#c0392b;color:#c0392b;}")
            reset.clicked.connect(
                lambda: self._reset_color(key, default_hex, btn))
            r.addWidget(lbl)
            r.addWidget(btn)
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            r.addWidget(reset)
            r.addStretch()
            L.addLayout(r)

        # ── APPEARANCE ──────────────────────────────────────────────────────
        sep("APPEARANCE")

        self.accent_btn = QPushButton()
        self.accent_btn.setFixedSize(24, 24)
        self.accent_btn.setMaximumSize(24, 24)
        self.accent_btn.setMinimumSize(24, 24)
        self.accent_btn.setStyleSheet(
            f"background:{settings['accent']};"
            f"border:1px solid #555;border-radius:4px;")
        self.accent_btn.clicked.connect(self.pick_accent)
        color_row("Accent color", self.accent_btn,
                  DEFAULT_SETTINGS["accent"], "accent")

        self.card_bg_btn = QPushButton()
        self.card_bg_btn.setFixedSize(24, 24)
        self.card_bg_btn.setMaximumSize(24, 24)
        self.card_bg_btn.setMinimumSize(24, 24)
        self.card_bg_btn.setStyleSheet(
            f"background:{settings['card_bg']};"
            f"border:1px solid #555;border-radius:4px;")
        self.card_bg_btn.clicked.connect(self.pick_card_bg)
        color_row("Card background", self.card_bg_btn,
                  DEFAULT_SETTINGS["card_bg"], "card_bg")

        self.bg_btn = QPushButton()
        self.bg_btn.setFixedSize(24, 24)
        self.bg_btn.setMaximumSize(24, 24)
        self.bg_btn.setMinimumSize(24, 24)
        self.bg_btn.setStyleSheet(
            f"background:{settings.get('bg_color','#0f0f0f')};"
            f"border:1px solid #555;border-radius:4px;")
        self.bg_btn.clicked.connect(self.pick_bg)
        color_row("Background", self.bg_btn, "#0f0f0f", "bg_color")

        self.font_btn = QPushButton(settings["font_family"])
        self.font_btn.setFixedHeight(28)
        self.font_btn.setMaximumWidth(220)
        self.font_btn.setStyleSheet(
            "QPushButton{background:#1a1a1a;border:1px solid #444;color:#C8A96E;"
            "font-size:11px;padding:0 8px;border-radius:2px;text-align:left;}"
            "QPushButton:hover{border-color:#666;}")
        self.font_btn.clicked.connect(self.pick_font)
        lrow("Font", self.font_btn)

        self.font_size_slider = QSlider(Qt.Orientation.Horizontal)
        self.font_size_slider.setRange(8, 22)
        self.font_size_slider.setValue(settings["font_size"])
        self.font_size_lbl = QLabel(f"{settings['font_size']}pt")
        self.font_size_lbl.setStyleSheet(
            "color:#888;font-size:10px;background:transparent;")
        self.font_size_lbl.setFixedWidth(32)
        self.font_size_slider.valueChanged.connect(self._on_font_size)
        srow("Font size", self.font_size_slider, self.font_size_lbl)

        # ── BEHAVIOUR ───────────────────────────────────────────────────────
        sep("BEHAVIOUR")

        self.temp_slider = QSlider(Qt.Orientation.Horizontal)
        self.temp_slider.setRange(0, 100)
        self.temp_slider.setValue(int(settings["temperature"] * 100))
        self.temp_lbl = QLabel(f"{settings['temperature']:.2f}")
        self.temp_slider.valueChanged.connect(self._on_temp)
        srow("Temperature", self.temp_slider, self.temp_lbl)

        self.max_tokens_input = QLineEdit(str(settings["max_tokens"]))
        self.max_tokens_input.setFixedHeight(28)
        self.max_tokens_input.setMaximumWidth(80)
        self.max_tokens_input.textChanged.connect(
            lambda v: self._set("max_tokens", int(v)) if v.isdigit() else None)
        lrow("Max tokens", self.max_tokens_input)

        self.ctx_input = QLineEdit(
            "" if not settings["ctx_override"]
            else str(settings["ctx_override"]))
        self.ctx_input.setPlaceholderText("auto")
        self.ctx_input.setFixedHeight(28)
        self.ctx_input.setMaximumWidth(80)
        self.ctx_input.textChanged.connect(
            lambda v: self._set("ctx_override", int(v) if v.isdigit() else 0))
        lrow("Context override", self.ctx_input)

        self.rounds_spin = QSpinBox()
        self.rounds_spin.setRange(1, 5)
        self.rounds_spin.setValue(settings["rounds"])
        self.rounds_spin.setFixedHeight(28)
        self.rounds_spin.setMaximumWidth(60)
        self.rounds_spin.valueChanged.connect(lambda v: self._set("rounds", v))
        lrow("Default rounds", self.rounds_spin)

        self.mod_check = QCheckBox("Show moderator card")
        self.mod_check.setChecked(settings["show_moderator"])
        self.mod_check.stateChanged.connect(
            lambda v: self._set("show_moderator", bool(v)))
        L.addWidget(self.mod_check)

        # Notification checkbox — label reflects the OS
        _notif_label = {
            "Linux":   "Desktop notifications",
            "Darwin":  "Desktop notifications",
            "Windows": "Desktop notifications",
        }.get(PLATFORM, "Desktop notifications")
        self.notif_check = QCheckBox(_notif_label)
        self.notif_check.setChecked(settings["notifications"])
        self.notif_check.stateChanged.connect(
            lambda v: self._set("notifications", bool(v)))
        L.addWidget(self.notif_check)

        self.autosave_check = QCheckBox("Auto-save debates")
        self.autosave_check.setChecked(settings["auto_save"])
        self.autosave_check.stateChanged.connect(
            lambda v: self._set("auto_save", bool(v)))
        L.addWidget(self.autosave_check)

        # ── BACKEND ─────────────────────────────────────────────────────────
        sep("BACKEND")

        self.backend_combo = QComboBox()
        self.backend_combo.addItems([
            "llama.cpp", "Odysseus", "Ollama", "LM Studio",
            "Jan", "koboldcpp", "Oobabooga", "TabbyAPI",
            "OpenAI / OpenRouter"
        ])
        self.backend_combo.setCurrentText(settings["backend"])
        self.backend_combo.setFixedHeight(28)
        self.backend_combo.setMaximumWidth(180)
        self.backend_combo.setStyleSheet(self.COMBO_STYLE)
        self.backend_combo.currentTextChanged.connect(self._on_backend)
        lrow("Backend", self.backend_combo)

        self.port_input = QLineEdit(settings["port"])
        self.port_input.setFixedHeight(28)
        self.port_input.setMaximumWidth(80)
        self.port_input.textChanged.connect(self._on_port)
        lrow("Port", self.port_input)

        self.model_lbl = QLabel("Model name")
        self.model_lbl.setFixedWidth(110)
        self.model_lbl.setStyleSheet(
            "color:#888;font-size:11px;background:transparent;")
        self.model_input = QLineEdit(settings["model_name"])
        self.model_input.setFixedHeight(28)
        self.model_input.setMaximumWidth(160)
        self.model_input.setPlaceholderText("for Ollama")
        self.model_input.textChanged.connect(self._on_model_changed)
        mr = QHBoxLayout()
        mr.setSpacing(8)
        mr.addWidget(self.model_lbl)
        mr.addWidget(self.model_input)
        mr.addStretch()
        L.addLayout(mr)
        vis = settings["backend"] == "Ollama"
        self.model_lbl.setVisible(vis)
        self.model_input.setVisible(vis)

        self.test_btn = QPushButton("TEST CONNECTION")
        self.test_btn.setFixedHeight(30)
        self.test_btn.setStyleSheet(
            "QPushButton{background:#1a1a1a;border:1px solid #444;color:#777;"
            "font-size:10px;letter-spacing:1px;border-radius:2px;}"
            "QPushButton:hover{border-color:#C8A96E;color:#C8A96E;}")
        self.test_btn.clicked.connect(self.test_connection)
        L.addWidget(self.test_btn)

        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        self.test_result.setStyleSheet(
            "color:#555;font-size:10px;background:transparent;")
        L.addWidget(self.test_result)

        # ── WEB SEARCH ──────────────────────────────────────────────────────
        sep("WEB SEARCH")

        self.search_check = QCheckBox("Enable web search")
        self.search_check.setChecked(settings["search_enabled"])
        self.search_check.stateChanged.connect(
            lambda v: self._set("search_enabled", bool(v)))
        L.addWidget(self.search_check)

        self.search_backend_combo = QComboBox()
        self.search_backend_combo.addItems(
            ["DuckDuckGo", "SearXNG", "Brave Search", "Tavily", "Perplexity"])
        self.search_backend_combo.setCurrentText(settings["search_backend"])
        self.search_backend_combo.setFixedHeight(28)
        self.search_backend_combo.setMaximumWidth(160)
        self.search_backend_combo.setStyleSheet(self.COMBO_STYLE)
        self.search_backend_combo.currentTextChanged.connect(
            self._on_search_backend)
        lrow("Search engine", self.search_backend_combo)

        self.search_url_lbl = QLabel("SearXNG URL")
        self.search_url_lbl.setFixedWidth(110)
        self.search_url_lbl.setStyleSheet(
            "color:#888;font-size:11px;background:transparent;")
        self.search_url_input = QLineEdit(settings["search_url"])
        self.search_url_input.setFixedHeight(28)
        self.search_url_input.setMaximumWidth(160)
        self.search_url_input.textChanged.connect(
            lambda v: self._set("search_url", v))
        su_r = QHBoxLayout()
        su_r.setSpacing(8)
        su_r.addWidget(self.search_url_lbl)
        su_r.addWidget(self.search_url_input)
        su_r.addStretch()
        L.addLayout(su_r)

        self.search_key_lbl = QLabel("API key")
        self.search_key_lbl.setFixedWidth(110)
        self.search_key_lbl.setStyleSheet(
            "color:#888;font-size:11px;background:transparent;")
        self.search_key_input = QLineEdit(settings["search_api_key"])
        self.search_key_input.setFixedHeight(28)
        self.search_key_input.setMaximumWidth(160)
        self.search_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.search_key_input.textChanged.connect(
            lambda v: self._set("search_api_key", v))
        sk_r = QHBoxLayout()
        sk_r.setSpacing(8)
        sk_r.addWidget(self.search_key_lbl)
        sk_r.addWidget(self.search_key_input)
        sk_r.addStretch()
        L.addLayout(sk_r)

        self.search_results_spin = QSpinBox()
        self.search_results_spin.setRange(1, 10)
        self.search_results_spin.setValue(settings["search_results"])
        self.search_results_spin.setFixedHeight(28)
        self.search_results_spin.setMaximumWidth(60)
        self.search_results_spin.valueChanged.connect(
            lambda v: self._set("search_results", v))
        lrow("Results to inject", self.search_results_spin)

        self._update_search_ui()

        self.search_test_btn = QPushButton("TEST SEARCH")
        self.search_test_btn.setFixedHeight(30)
        self.search_test_btn.setStyleSheet(
            "QPushButton{background:#1a1a1a;border:1px solid #444;color:#777;"
            "font-size:10px;letter-spacing:1px;border-radius:2px;}"
            "QPushButton:hover{border-color:#C8A96E;color:#C8A96E;}")
        self.search_test_btn.clicked.connect(self.test_search)
        L.addWidget(self.search_test_btn)
        self.search_test_result = QLabel("")
        self.search_test_result.setWordWrap(True)
        self.search_test_result.setStyleSheet(
            "color:#555;font-size:10px;background:transparent;")
        L.addWidget(self.search_test_result)

        L.addStretch()

        reset_btn = QPushButton("Reset All to Defaults")
        reset_btn.setFixedHeight(32)
        reset_btn.setStyleSheet(
            "QPushButton{background:#1a1a1a;border:1px solid #333;color:#666;"
            "font-size:11px;letter-spacing:1px;border-radius:2px;}"
            "QPushButton:hover{border-color:#c0392b;color:#c0392b;}")
        reset_btn.clicked.connect(self._reset_all)
        L.addWidget(reset_btn)

        scroll.setWidget(inner)
        outer.addWidget(scroll)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _set(self, key, value):
        settings[key] = value
        save_settings()
        self.changed.emit()

    def _reset_color(self, key, default, btn):
        self._set(key, default)
        btn.setStyleSheet(
            f"background:{default};border:1px solid #555;border-radius:2px;")

    def _on_font_size(self, v):
        self.font_size_lbl.setText(f"{v}pt")
        self._set("font_size", v)

    def _on_temp(self, v):
        t = v / 100.0
        self.temp_lbl.setText(f"{t:.2f}")
        self._set("temperature", t)

    def _on_backend(self, v):
        global CURRENT_BACKEND
        CURRENT_BACKEND = v
        self._set("backend", v)
        vis = v == "Ollama"
        self.model_lbl.setVisible(vis)
        self.model_input.setVisible(vis)

    def _on_model_changed(self, v):
        global CURRENT_MODEL
        CURRENT_MODEL = v
        self._set("model_name", v)

    def _on_port(self, v):
        global LLAMA_HOST, LLAMA
        self._set("port", v)
        LLAMA_HOST = f"http://127.0.0.1:{v}"
        LLAMA      = f"{LLAMA_HOST}/v1/chat/completions"

    def _on_search_backend(self, v):
        self._set("search_backend", v)
        self._update_search_ui()

    def _update_search_ui(self):
        b         = settings["search_backend"]
        needs_url = b == "SearXNG"
        needs_key = b in ("Brave Search", "Tavily", "Perplexity")
        self.search_url_lbl.setVisible(needs_url)
        self.search_url_input.setVisible(needs_url)
        self.search_key_lbl.setVisible(needs_key)
        self.search_key_input.setVisible(needs_key)

    def pick_accent(self):
        c = QColorDialog.getColor(QColor(settings["accent"]), self)
        if c.isValid():
            self._set("accent", c.name())
            self.accent_btn.setStyleSheet(
                f"background:{c.name()};border:1px solid #555;border-radius:2px;")

    def pick_card_bg(self):
        c = QColorDialog.getColor(QColor(settings["card_bg"]), self)
        if c.isValid():
            self._set("card_bg", c.name())
            self.card_bg_btn.setStyleSheet(
                f"background:{c.name()};border:1px solid #555;border-radius:2px;")

    def pick_font(self):
        from PyQt6.QtGui import QFontDatabase
        dialog = QDialog(self)
        dialog.setWindowTitle("Font")
        dialog.setFixedSize(260, 360)
        dialog.setStyleSheet(
            "QDialog{background:#111;}"
            "QListWidget{background:#1a1a1a;color:#eee;border:1px solid #333;"
            "font-size:12px;outline:none;}"
            "QListWidget::item:selected{background:#2a2a2a;color:#C8A96E;}"
            "QLineEdit{background:#1a1a1a;border:1px solid #333;color:#eee;"
            "padding:4px 8px;font-size:12px;}"
            "QPushButton{background:#C8A96E;color:#111;border:none;padding:6px;"
            "font-weight:bold;border-radius:2px;}"
            "QPushButton:hover{background:#d4b87a;}")
        layout = QVBoxLayout(dialog)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        search = QLineEdit()
        search.setPlaceholderText("Search fonts...")
        layout.addWidget(search)

        lst = QListWidget()
        fonts = sorted(QFontDatabase.families())
        lst.addItems(fonts)
        if settings["font_family"] in fonts:
            lst.setCurrentRow(fonts.index(settings["font_family"]))
            lst.scrollToItem(lst.currentItem())
        layout.addWidget(lst)

        def filter_fonts(text):
            for i in range(lst.count()):
                item = lst.item(i)
                item.setHidden(text.lower() not in item.text().lower())

        search.textChanged.connect(filter_fonts)
        lst.itemDoubleClicked.connect(lambda: dialog.accept())

        ok_btn = QPushButton("SELECT")
        layout.addWidget(ok_btn)
        ok_btn.clicked.connect(dialog.accept)

        if dialog.exec() == QDialog.DialogCode.Accepted and lst.currentItem():
            if not lst.currentItem().isHidden():
                font = lst.currentItem().text()
                self._set("font_family", font)
                self.font_btn.setText(font)

    def pick_bg(self):
        c = QColorDialog.getColor(
            QColor(settings.get("bg_color", "#0f0f0f")), self)
        if c.isValid():
            self._set("bg_color", c.name())
            self.bg_btn.setStyleSheet(
                f"background:{c.name()};border:1px solid #555;border-radius:4px;")

    def test_connection(self):
        self.test_result.setText("Testing...")
        self.test_btn.setEnabled(False)
        QApplication.processEvents()
        try:
            ctx = get_context_size(settings["backend"], settings["model_name"])
            r = requests.post(LLAMA, json={
                "model": CURRENT_MODEL if CURRENT_MODEL else "local",
                "messages": [{"role": "user",
                               "content": "reply with just the word ok"}],
                "max_tokens":  settings.get("max_tokens", 10),
                "temperature": settings.get("temperature", 0.1),
            }, timeout=10)
            reply = r.json()["choices"][0]["message"]["content"]
            self.test_result.setStyleSheet(
                "color:#5a9;font-size:10px;background:transparent;")
            self.test_result.setText(
                f"✓ Connected — ctx: {ctx} — replied: {reply[:40]}")
        except Exception as e:
            self.test_result.setStyleSheet(
                "color:#c0392b;font-size:10px;background:transparent;")
            self.test_result.setText(f"✗ Failed: {e}")
        self.test_btn.setEnabled(True)

    def test_search(self):
        self.search_test_result.setText("Testing...")
        self.search_test_btn.setEnabled(False)
        QApplication.processEvents()
        try:
            results, sources = web_search("capital of France")
            if results and not results.startswith("["):
                self.search_test_result.setStyleSheet(
                    "color:#5a9;font-size:10px;background:transparent;")
                self.search_test_result.setText(
                    f"✓ Search working — {len(sources)} source(s) found")
            else:
                self.search_test_result.setStyleSheet(
                    "color:#e67e22;font-size:10px;background:transparent;")
                self.search_test_result.setText(
                    "⚠ Connected but no results returned")
        except Exception as e:
            self.search_test_result.setStyleSheet(
                "color:#c0392b;font-size:10px;background:transparent;")
            self.search_test_result.setText(f"✗ Failed: {e}")
        self.search_test_btn.setEnabled(True)

    def _reset_all(self):
        global settings
        settings.update(DEFAULT_SETTINGS)
        save_settings()
        self.blockSignals(True)
        self.accent_btn.setStyleSheet(
            f"background:{DEFAULT_SETTINGS['accent']};"
            f"border:1px solid #555;border-radius:2px;")
        self.card_bg_btn.setStyleSheet(
            f"background:{DEFAULT_SETTINGS['card_bg']};"
            f"border:1px solid #555;border-radius:2px;")
        self.font_size_slider.setValue(DEFAULT_SETTINGS["font_size"])
        self.temp_slider.setValue(int(DEFAULT_SETTINGS["temperature"] * 100))
        self.max_tokens_input.setText(str(DEFAULT_SETTINGS["max_tokens"]))
        self.rounds_spin.setValue(DEFAULT_SETTINGS["rounds"])
        self.mod_check.setChecked(DEFAULT_SETTINGS["show_moderator"])
        self.notif_check.setChecked(DEFAULT_SETTINGS["notifications"])
        self.autosave_check.setChecked(DEFAULT_SETTINGS["auto_save"])
        self.backend_combo.setCurrentText(DEFAULT_SETTINGS["backend"])
        self.port_input.setText(DEFAULT_SETTINGS["port"])
        self.search_check.setChecked(DEFAULT_SETTINGS["search_enabled"])
        self.search_backend_combo.setCurrentText(
            DEFAULT_SETTINGS["search_backend"])
        self.search_results_spin.setValue(DEFAULT_SETTINGS["search_results"])
        self.bg_btn.setStyleSheet(
            "background:#0f0f0f;border:1px solid #555;border-radius:4px;")
        self.blockSignals(False)
        self.changed.emit()

    def show_panel(self):
        p = self.parent()
        if p:
            self.setGeometry(
                p.width() - self.PANEL_WIDTH - 8, 0,
                self.PANEL_WIDTH, p.height())
        self.setMaximumWidth(0)
        self.setVisible(True)
        self.raise_()
        self._anim.setStartValue(0)
        self._anim.setEndValue(self.PANEL_WIDTH)
        self._anim.start()

    def hide_panel(self):
        self._anim.setStartValue(self.PANEL_WIDTH)
        self._anim.setEndValue(0)
        self._anim.start()
        QTimer.singleShot(290, lambda: self.setVisible(False))
        self.changed.emit()

# ── main window ────────────────────────────────────────────────────────────
class CouncilApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("The Council")
        self.setMinimumSize(1100, 760)
        self.worker     = None
        self.debate_log = []

        icon_path = os.path.join(ASSETS_DIR, "icon.png")
        app_icon  = None
        if os.path.exists(icon_path):
            app_icon = QIcon(icon_path)
            self.setWindowIcon(app_icon)

        # Set up system-tray notifications now that QApplication exists
        _setup_tray(app_icon)

        self.setStyleSheet(
            "QMainWindow{background:#0f0f0f;}"
            "QWidget{background:#0f0f0f;color:#cccccc;}"
            "QLabel{background:transparent;}")

        central = QWidget()
        self.setCentralWidget(central)

        main = QVBoxLayout(central)
        main.setContentsMargins(24, 20, 24, 20)
        main.setSpacing(12)

        # ── header ──────────────────────────────────────────────────────────
        self._header_frame = QFrame()
        self._header_frame.setStyleSheet(
            "QFrame{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #1a1a1a,stop:1 #0f0f0f);border:none;"
            "border-bottom:1px solid #1e1e1e;border-radius:6px;}")
        self._header_frame.setFixedHeight(70)
        hf_layout = QHBoxLayout(self._header_frame)
        hf_layout.setContentsMargins(16, 0, 16, 0)

        crest_path = os.path.join(ASSETS_DIR, "crest.png")
        if os.path.exists(crest_path):
            cl = QLabel()
            pix = QPixmap(crest_path).scaled(
                44, 44,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            cl.setPixmap(pix)
            hf_layout.addWidget(cl)

        self.title_lbl = QLabel("THE COUNCIL")
        self.title_lbl.setFont(QFont("serif", 22, QFont.Weight.Bold))
        self.title_lbl.setStyleSheet(
            f"color:{settings['accent']};letter-spacing:6px;background:transparent;")
        hf_layout.addWidget(self.title_lbl)
        hf_layout.addStretch()

        a    = settings["accent"]
        hbtn = (f"QPushButton{{background:transparent;border:1px solid #2a2a2a;"
                f"color:#666;font-size:10px;letter-spacing:1px;"
                f"padding:4px 12px;border-radius:3px;}}"
                f"QPushButton:hover{{border-color:{a}77;color:{a};}}")

        self.personas_btn = QPushButton("PERSONAS")
        self.personas_btn.setStyleSheet(hbtn)
        self.personas_btn.clicked.connect(self.open_persona_editor)
        hf_layout.addWidget(self.personas_btn)

        self.reset_btn = QPushButton("RESET")
        self.reset_btn.setStyleSheet(hbtn)
        self.reset_btn.clicked.connect(self.reset_council)
        hf_layout.addWidget(self.reset_btn)

        self.export_btn = QPushButton("EXPORT")
        self.export_btn.setStyleSheet(hbtn)
        self.export_btn.clicked.connect(self.export_debate)
        self.export_btn.setEnabled(False)
        hf_layout.addWidget(self.export_btn)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setFixedSize(34, 34)
        self.settings_btn.setCheckable(True)
        self.settings_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:1px solid #2a2a2a;"
            f"color:#666;font-size:16px;border-radius:3px;}}"
            f"QPushButton:hover{{border-color:{a}77;color:{a};}}"
            f"QPushButton:checked{{background:{a}18;border-color:{a}66;color:{a};}}")
        self.settings_btn.clicked.connect(self.toggle_settings)
        hf_layout.addWidget(self.settings_btn)
        main.addWidget(self._header_frame)

        # animated title underline
        self._title_line = TitleLine()
        main.addWidget(self._title_line)

        self.round_indicator = RoundIndicator(settings["rounds"])
        main.addWidget(self.round_indicator)

        cards_widget = QWidget()
        cards_widget.setStyleSheet("background:transparent;")
        self.cards_layout = QHBoxLayout(cards_widget)
        self.cards_layout.setSpacing(10)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards = {}
        self.build_cards()
        main.addWidget(cards_widget, stretch=3)

        self.moderator_card = PersonaCard("The Moderator", "#A0A8B0")
        self.moderator_card.setMaximumHeight(160)
        self.moderator_card.text.setMinimumHeight(80)
        self.moderator_card.setVisible(settings["show_moderator"])
        main.addWidget(self.moderator_card, stretch=1)

        self.search_label = QLabel("WEB SEARCH CONTEXT")
        self.search_label.setStyleSheet(
            "color:#444;font-size:9px;letter-spacing:2px;")
        self.search_label.setVisible(False)
        main.addWidget(self.search_label)

        self.search_box = QTextEdit()
        self.search_box.setReadOnly(True)
        self.search_box.setFixedHeight(60)
        self.search_box.setVisible(False)
        self.search_box.setStyleSheet(
            "QTextEdit{background:#0a0a0a;border:1px solid #1e1e1e;"
            "color:#444;font-size:11px;padding:6px;border-radius:4px;}")
        main.addWidget(self.search_box)

        self.status_label = QLabel("Consult the council.")
        self.status_label.setStyleSheet(
            "color:#444;font-size:11px;letter-spacing:1px;")
        main.addWidget(self.status_label)

        input_row = QHBoxLayout()
        input_row.setSpacing(10)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Bring your question to the council...")
        self.input.setFixedHeight(44)
        self.input.setStyleSheet(
            f"QLineEdit{{background:#141414;border:1px solid #2a2a2a;"
            f"border-bottom:1px solid {a}55;color:#eee;font-size:14px;"
            f"padding:0 14px;border-radius:4px;}}"
            f"QLineEdit:focus{{border-bottom:1px solid {a};background:#161616;}}")
        self.input.setFont(QFont(settings["font_family"], 13))
        self.input.returnPressed.connect(self.submit)
        input_row.addWidget(self.input)

        self._btn_normal_style = (
            f"QPushButton{{background:{a};color:#111;border:none;font-size:11px;"
            f"font-weight:bold;letter-spacing:2px;border-radius:4px;}}"
            f"QPushButton:hover{{background:#d4b87a;}}"
            f"QPushButton:pressed{{background:#b8994e;}}"
            f"QPushButton:disabled{{background:#1e1e1e;color:#444;}}")
        self.btn = QPushButton("CONVENE")
        self.btn.setFixedSize(110, 44)
        self.btn.setStyleSheet(self._btn_normal_style)
        self.btn.clicked.connect(self.submit)
        input_row.addWidget(self.btn)
        main.addLayout(input_row)

        self._btn_pulse_state = 0
        self._btn_pulse_timer = QTimer()
        self._btn_pulse_timer.timeout.connect(self._pulse_btn)

        # floating settings panel
        self.settings_panel = SettingsPanel(central)
        self.settings_panel.changed.connect(self._on_settings_changed)

        global CURRENT_BACKEND, CURRENT_MODEL, LLAMA_HOST, LLAMA
        CURRENT_BACKEND = settings["backend"]
        CURRENT_MODEL   = settings["model_name"]
        LLAMA_HOST      = f"http://127.0.0.1:{settings['port']}"
        LLAMA           = f"{LLAMA_HOST}/v1/chat/completions"

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cw = self.centralWidget()
        if cw and hasattr(self, "settings_panel") \
                and self.settings_panel.isVisible():
            pw = self.settings_panel.PANEL_WIDTH
            self.settings_panel.setGeometry(
                cw.width() - pw - 8, 0, pw, cw.height())

    def _pulse_btn(self):
        frames = ["|", "/", "-", "\\"]
        self._btn_pulse_state = (self._btn_pulse_state + 1) % len(frames)
        a = settings["accent"]
        self.status_label.setStyleSheet(
            f"color:{a};font-size:11px;letter-spacing:1px;font-weight:bold;")
        self.status_label.setText(
            f"{frames[self._btn_pulse_state]}  deliberating...")

    def build_cards(self):
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.cards = {}
        for p in personas:
            card = PersonaCard(p["name"], p["color"])
            self.cards[p["name"]] = card
            self.cards_layout.addWidget(card)

    def toggle_settings(self, checked):
        if checked:
            cw = self.centralWidget()
            pw = self.settings_panel.PANEL_WIDTH
            self.settings_panel.setGeometry(
                cw.width() - pw - 8, 0, pw, cw.height())
            self.settings_panel.show_panel()
        else:
            self.settings_panel.hide_panel()

    def _on_settings_changed(self):
        QTimer.singleShot(300, lambda: self.settings_btn.setChecked(
            self.settings_panel.isVisible()))
        bg = settings.get("bg_color", "#0f0f0f")
        self.setStyleSheet(
            f"QMainWindow{{background:{bg};}}"
            f"QWidget{{background:{bg};color:#cccccc;}}"
            f"QLabel{{background:transparent;}}")
        self._header_frame.setStyleSheet(
            f"QFrame{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f"stop:0 #1a1a1a,stop:1 {bg});border:none;"
            f"border-bottom:1px solid #1e1e1e;border-radius:6px;}}")
        a = settings["accent"]
        self.input.setStyleSheet(
            f"QLineEdit{{background:{bg};border:1px solid #2a2a2a;"
            f"border-bottom:1px solid {a}55;color:#eee;font-size:14px;"
            f"padding:0 14px;border-radius:4px;}}"
            f"QLineEdit:focus{{border-bottom:1px solid {a};background:{bg};}}")
        for card in self.cards.values():
            card.refresh_style()
        self.moderator_card.refresh_style()
        self.moderator_card.setVisible(settings["show_moderator"])
        self.round_indicator.set_rounds(settings["rounds"])
        self.input.setFont(QFont(settings["font_family"], 13))
        a    = settings["accent"]
        hbtn = (f"QPushButton{{background:transparent;border:1px solid #2a2a2a;"
                f"color:#666;font-size:10px;letter-spacing:1px;"
                f"padding:4px 12px;border-radius:3px;}}"
                f"QPushButton:hover{{border-color:{a}77;color:{a};}}")
        self.personas_btn.setStyleSheet(hbtn)
        self.reset_btn.setStyleSheet(hbtn)
        self.export_btn.setStyleSheet(hbtn)
        self.settings_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:1px solid #2a2a2a;"
            f"color:#666;font-size:16px;border-radius:3px;}}"
            f"QPushButton:hover{{border-color:{a}77;color:{a};}}"
            f"QPushButton:checked{{background:{a}18;border-color:{a}66;color:{a};}}")
        self._btn_normal_style = (
            f"QPushButton{{background:{a};color:#111;border:none;font-size:11px;"
            f"font-weight:bold;letter-spacing:2px;border-radius:4px;}}"
            f"QPushButton:hover{{background:#d4b87a;}}"
            f"QPushButton:pressed{{background:#b8994e;}}"
            f"QPushButton:disabled{{background:#1e1e1e;color:#444;}}")
        self.btn.setStyleSheet(self._btn_normal_style)
        self.title_lbl.setStyleSheet(
            f"color:{a};letter-spacing:6px;background:transparent;")

    def open_persona_editor(self):
        dialog = PersonaEditorDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_vals = dialog.get_values()
            for i, p in enumerate(personas):
                p["name"]   = new_vals[i]["name"]
                p["color"]  = new_vals[i]["color"]
                p["system"] = new_vals[i]["system"]
            self.build_cards()

    def export_debate(self):
        if not self.debate_log: return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Debate", "council_debate.md",
            "Markdown (*.md);;Text (*.txt)")
        if not path: return
        is_txt = path.endswith(".txt")
        with open(path, "w", encoding="utf-8") as f:
            for entry in self.debate_log:
                if is_txt:
                    f.write(f"Question: {entry['question']}\n\n")
                    for rn, rd in enumerate(entry["rounds"], 1):
                        f.write(f"Round {rn}\n\n")
                        for name, text in rd.items():
                            f.write(f"{name}\n{text}\n\n")
                    f.write(f"Verdict\n{entry['verdict']}\n\n---\n\n")
                else:
                    f.write(
                        f"# Council Debate\n\n"
                        f"## Question\n\n{entry['question']}\n\n")
                    for rn, rd in enumerate(entry["rounds"], 1):
                        f.write(f"### Round {rn}\n\n")
                        for name, text in rd.items():
                            f.write(f"**{name}**\n\n{text}\n\n")
                    f.write(
                        f"### Verdict\n\n{entry['verdict']}\n\n---\n\n")

    def reset_council(self):
        for card in self.cards.values():
            card.set_waiting()
        self.moderator_card.set_waiting()
        self.round_indicator.reset()
        self.search_label.setVisible(False)
        self.search_box.setVisible(False)
        self.status_label.setText("Consult the council.")
        self.export_btn.setEnabled(False)
        self._title_line.set_active(False)
        self.input.clear()
        self.input.setFocus()

    def _auto_save(self, question, rounds_data, verdict):
        if not settings["auto_save"]: return
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(DEBATES_DIR, f"debate_{ts}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                f"# Council Debate\n\n## Question\n\n{question}\n\n")
            for rn, rd in enumerate(rounds_data, 1):
                f.write(f"### Round {rn}\n\n")
                for name, text in rd.items():
                    f.write(f"**{name}**\n\n{text}\n\n")
            f.write(f"### Verdict\n\n{verdict}\n\n")

    def submit(self):
        question = self.input.text().strip()
        if not question or self.worker is not None: return
        try:
            requests.get(f"{LLAMA_HOST}/props", timeout=2)
        except Exception:
            try:
                requests.get(f"{LLAMA_HOST}/v1/models", timeout=2)
            except Exception:
                self.status_label.setText(
                    "Cannot reach server — check port and backend in ⚙ settings.")
                return

        self.current_question   = question
        self.current_rounds     = []
        self.current_round_data = {}
        self.btn.setEnabled(False)
        self._btn_pulse_timer.start(200)
        self._title_line.set_active(True)
        self.input.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.moderator_card.set_waiting()
        self.round_indicator.reset()
        self.status_label.setText("The council convenes...")

        show_search = settings["search_enabled"]
        self.search_label.setVisible(show_search)
        self.search_box.setVisible(show_search)
        if show_search:
            self.search_box.setPlainText("Searching...")

        for card in self.cards.values():
            card.set_waiting()

        rounds = self.settings_panel.rounds_spin.value()
        self.worker = CouncilWorker(question, rounds)
        self.worker.persona_start.connect(self.on_persona_start)
        self.worker.persona_done.connect(self.on_persona_done)
        self.worker.round_start.connect(self.on_round_start)
        self.worker.verdict_start.connect(
            lambda: self.moderator_card.start_thinking())
        self.worker.verdict_done.connect(self.on_verdict)
        self.worker.search_done.connect(
            lambda t: self.search_box.setPlainText(t))
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def on_round_start(self, round_num):
        self.round_indicator.set_round(round_num)
        if self.current_round_data:
            self.current_rounds.append(dict(self.current_round_data))
        self.current_round_data = {}

    def on_persona_start(self, name):
        if name in self.cards:
            self.cards[name].start_thinking()
            QApplication.processEvents()

    def on_persona_done(self, name, text, round_num):
        if name in self.cards:
            self.cards[name].set_active(text)
            QApplication.processEvents()
        self.current_round_data[name] = text

    def on_verdict(self, text):
        if self.current_round_data:
            self.current_rounds.append(dict(self.current_round_data))
        self.debate_log.append({
            "question": self.current_question,
            "rounds":   self.current_rounds,
            "verdict":  text
        })
        self._auto_save(self.current_question, self.current_rounds, text)
        self.moderator_card.set_active(text)
        self.status_label.setText("The council has spoken.")
        self.export_btn.setEnabled(True)
        self._title_line.set_active(False)
        # Cross-platform notification
        send_notification("The Council has spoken", "Your verdict is ready.")

    def on_finished(self):
        self._btn_pulse_timer.stop()
        self.worker = None
        self.btn.setEnabled(True)
        self.btn.setText("CONVENE")
        self.btn.setStyleSheet(self._btn_normal_style)
        try:
            self.btn.clicked.disconnect()
        except Exception:
            pass
        self.btn.clicked.connect(self.submit)
        self.input.setEnabled(True)
        self.input.clear()
        self.input.setFocus()
        self.status_label.setStyleSheet(
            "color:#444;font-size:11px;letter-spacing:1px;")

# ── entry point ────────────────────────────────────────────────────────────
def main():
    # Windows: tell Qt to use its own DPI awareness instead of the OS default
    if IS_WINDOWS:
        try:
            from PyQt6.QtCore import Qt as _Qt
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                _Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # macOS: keep the app alive in the dock when the window is closed
    if IS_MAC:
        app.setQuitOnLastWindowClosed(False)

    window = CouncilApp()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
