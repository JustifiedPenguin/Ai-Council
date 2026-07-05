import sys
import os
import json
import math
import random
import requests
import subprocess
import datetime
import platform
import backend_manager as bm
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QLineEdit, QPushButton, QLabel, QFrame, QSizePolicy,
    QSpinBox, QDialog, QDialogButtonBox, QColorDialog, QFileDialog,
    QScrollArea, QComboBox, QSlider, QCheckBox, QFontComboBox,
    QGraphicsOpacityEffect, QListWidget, QSystemTrayIcon, QMenu,
    QProgressBar
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QPropertyAnimation,
    QEasingCurve, QRect, QPoint, QSize, QRectF
)
from PyQt6.QtGui import (
    QFont, QColor, QIcon, QPixmap, QPainter, QPen, QBrush,
    QLinearGradient, QRadialGradient, QPainterPath, QAction
)

PLATFORM   = platform.system()
IS_LINUX   = PLATFORM == "Linux"
IS_WINDOWS = PLATFORM == "Windows"
IS_MAC     = PLATFORM == "Darwin"

def resource_path(path):
    """For bundled assets (read-only, inside the EXE)."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, path)
    # running from source: src/main.py -> project root is one level up
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, path)

def user_data_dir():
    if hasattr(sys, "_MEIPASS"):
        if IS_WINDOWS:
            base = os.environ.get("APPDATA", os.path.expanduser("~"))
        elif IS_MAC:
            base = os.path.expanduser("~/Library/Application Support")
        else:
            base = os.path.expanduser("~/.config")
        path = os.path.join(base, "TheCouncil")
        os.makedirs(path, exist_ok=True)
        return path
    if os.environ.get("APPIMAGE"):
        config = os.path.join(os.path.expanduser("~"), ".config", "TheCouncil")
        os.makedirs(config, exist_ok=True)
        return config
    # Running from source: anchor to the project root (one level up from
    # src/main.py), not the launch-time current working directory. Using
    # cwd here meant settings.json/debates/bin would silently fragment
    # into a different location depending on which folder you happened
    # to be in when you typed `python3 main.py` -- anchoring to the file's
    # own location keeps it consistent no matter where it's launched from,
    # while still being separate from the real installed-app data dir.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE_DIR      = user_data_dir()
ASSETS_DIR    = resource_path("icons")
STYLES_DIR    = resource_path("styles")
DEBATES_DIR   = os.path.join(BASE_DIR, "debates")
BIN_DIR       = os.path.join(BASE_DIR, "bin")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
os.makedirs(DEBATES_DIR, exist_ok=True)
os.makedirs(BIN_DIR, exist_ok=True)

LLAMA_HOST      = "http://127.0.0.1:8080"
LLAMA           = f"{LLAMA_HOST}/v1/chat/completions"
# CURRENT_BACKEND = "llama.cpp"
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
    "llamacpp_mode":      "manual",   # "bundled" | "custom" | "manual"
    "bundled_binary_path": "",        # set after a successful Download Backend
    "custom_binary_path":  "",        # user-supplied llama-server build (e.g. custom ROCm)
    "model_gguf_path":     "",        # .gguf model used by bundled/custom modes
    "n_gpu_layers":         99,
    "gpu_layers_auto":      True,     # let llama-server's --fit logic pick layers (recommended)
    "verbose_logs":         False,    # pass --log-verbose to the managed backend
    "search_enabled": False,
    "search_url":     "https://searx.be",
    "search_results": 3,
}
settings = dict(DEFAULT_SETTINGS)

def load_settings():
    global settings
    try:
        with open(SETTINGS_FILE) as f:
            settings.update(json.load(f))
    except FileNotFoundError:
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

_tray_icon = None

def _setup_tray(app_icon=None):
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

def send_notification(title, body):
    if not settings.get("notifications"):
        return
    if _tray_icon and _tray_icon.isVisible():
        _tray_icon.showMessage(title, body,
                               QSystemTrayIcon.MessageIcon.Information, 4000)
        return
    try:
        if IS_LINUX:
            subprocess.Popen(["notify-send", title, body],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif IS_MAC:
            script = 'display notification "' + body + '" with title "' + title + '"'
            subprocess.Popen(["osascript", "-e", script],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif IS_WINDOWS:
            try:
                from winotify import Notification
                toast = Notification(app_id="The Council", title=title, msg=body)
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

personas = [
    {"name": "The Analyst", "color": "#C8A96E",
     "system": "You are a sharp analytical thinker on a decision-making council. Break problems into parts, identify risks, look for logical inconsistencies. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
    {"name": "The Devil's Advocate", "color": "#C0392B",
     "system": "You are a devil's advocate on a decision-making council. Challenge the direction the group is leaning, poke holes in assumptions, surface uncomfortable truths. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
    {"name": "The Pragmatist", "color": "#7F8C8D",
     "system": "You are a pragmatic realist on a decision-making council. Ask what is actually achievable given real constraints. Push for the simplest thing that could work. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
    {"name": "The Empath", "color": "#8E6DB5",
     "system": "You are an emotionally intelligent advisor on a decision-making council. Consider the human side of every decision. Push back when the group is too cold. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
    {"name": "The Visionary", "color": "#2E86AB",
     "system": "You are a big-picture thinker on a decision-making council. Ask what the long-term consequences are. Challenge short-term thinking. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
]

MODERATOR_SYSTEM = (
    "You are a neutral moderator summarizing a council debate. "
    "Synthesize key points of agreement and disagreement, then give a clear "
    "actionable recommendation. Be concise. End with a single clear recommendation."
)

def web_search(query):
    n = settings["search_results"]
    try:
        r = requests.get(
            settings["search_url"].rstrip("/") + "/search",
            params={"q": query, "format": "json"},
            timeout=5)
        items = r.json().get("results", [])[:n]
        sources = [x.get("url", "") for x in items if x.get("url")]
        text = "\n\n".join(
            x.get("title", "") + ": " + x.get("content", "") for x in items)
        return text, sources
    except Exception as e:
        return "[search error: " + str(e) + "]", []

def get_context_size(backend, model_name=""):
    if settings["ctx_override"] > 0:
        return settings["ctx_override"]
    try:
        if backend == "llama.cpp":
            r = requests.get(f"{LLAMA_HOST}/props", timeout=2)
            n = r.json().get("n_ctx")
            if n:
                return n
        elif backend == "Ollama":
            r = requests.post(f"{LLAMA_HOST}/api/show",
                              json={"name": model_name}, timeout=2)
            ctx = r.json().get("model_info", {}).get("llama.context_length")
            if ctx:
                return ctx
    except Exception:
        pass
    return 4096


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
                "max_tokens": min(settings.get("max_tokens", 512),
                                  self.ctx_size // 16),
            }, timeout=120)
            data = r.json()
            return (data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "[no response]"))
        except Exception as e:
            return "[error: " + str(e) + "]"

    def run(self):
        search_context = ""
        if settings["search_enabled"]:
            results, sources = web_search(self.question)
            if results:
                search_context = "\n\nCurrent web search results:\n" + results + "\n"
                sources_text = "\n".join(sources) if sources else "No sources found"
                self.search_done.emit("SOURCES:\n" + sources_text + "\n\nCONTEXT:\n" + results)

        histories = {
            p["name"]: [{"role": "user", "content": self.question + search_context}]
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
                        name + ": " + text[:self.ctx_size // 20]
                        for name, text in responses.items()
                        if name != p["name"]
                    )
                    histories[p["name"]].append({
                        "role": "user",
                        "content": "The other council members said:\n\n" + others + "\n\nRespond to them."
                    })
                reply = self.ask(p["system"], histories[p["name"]])
                new_responses[p["name"]] = reply
                histories[p["name"]].append({"role": "assistant", "content": reply})
                self.persona_done.emit(p["name"], reply, round_num + 1)
            responses = new_responses

        self.verdict_start.emit()
        all_responses = "\n\n".join(
            n + ": " + t[:self.ctx_size // 20] for n, t in responses.items()
        )
        summary = self.ask(
            MODERATOR_SYSTEM,
            [{"role": "user",
              "content": "The council debated: '" + self.question + "'\n\n" + all_responses}]
        )
        self.verdict_done.emit(summary)
        self.finished.emit()


class BackendStartWorker(QThread):
    """
    Launches a LlamaServerManager and waits for it to become healthy,
    off the UI thread. wait_until_ready() involves blocking sleeps, so
    this must not run on the Qt event loop thread.
    """
    ready = pyqtSignal(bool)

    def __init__(self, manager, timeout=90):
        super().__init__()
        self.manager = manager
        self.timeout = timeout

    def run(self):
        try:
            self.manager.start()
        except Exception:
            self.ready.emit(False)
            return
        ok = self.manager.wait_until_ready(self.timeout)
        self.ready.emit(ok)


class BackendDownloadWorker(QThread):
    """
    Runs GPU detection + release lookup + download + verify + extract for
    the "Bundled (auto)" backend mode, off the UI thread (network I/O).
    """
    status   = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    done     = pyqtSignal(bool, str, object)  # success, message_or_path, commit_verified (True/False/None)

    def __init__(self, dest_dir):
        super().__init__()
        self.dest_dir = dest_dir

    def run(self):
        try:
            variant = bm.detect_gpu()
            self.status.emit(bm.gpu_detection_label(variant))

            self.status.emit("Checking latest llama.cpp release...")
            release = bm.fetch_latest_release()

            # Best-effort secondary signal: was the release's target commit
            # pushed through GitHub's normal verified-signing flow? Not a
            # substitute for the checksum check below (that's what actually
            # confirms this specific binary asset is hash-correct) -- this
            # just adds one more honest data point about provenance.
            commit_verified = bm.get_release_commit_verified(release)

            asset = bm.find_asset(release, variant)
            if asset is None:
                self.done.emit(
                    False,
                    "No matching prebuilt binary found for your platform ("
                    + variant + "). Try Custom Binary Path instead.",
                    None)
                return

            self.status.emit("Downloading " + asset.get("name", "") + "...")
            archive_path = bm.download_and_verify(
                asset, self.dest_dir,
                progress_callback=lambda d, t: self.progress.emit(d, t))

            self.status.emit("Extracting...")
            extract_dir = os.path.join(self.dest_dir, "extracted")
            bm.extract_archive(archive_path, extract_dir)

            binary_name = "llama-server.exe" if bm.IS_WINDOWS else "llama-server"
            binary_path = None
            for root, _dirs, files in os.walk(extract_dir):
                if binary_name in files:
                    binary_path = os.path.join(root, binary_name)
                    break

            if not binary_path:
                self.done.emit(
                    False, "Downloaded archive did not contain " + binary_name + ".",
                    commit_verified)
                return

            if not bm.IS_WINDOWS:
                try:
                    os.chmod(binary_path, 0o755)
                except Exception:
                    pass

            self.done.emit(True, binary_path, commit_verified)
        except bm.DownloadError as e:
            self.done.emit(False, str(e), None)
        except Exception as e:
            self.done.emit(False, "Unexpected error: " + str(e), None)


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
                "x": random.random(), "y": random.random(),
                "vx": (random.random() - 0.5) * 0.0008,
                "vy": (random.random() - 0.5) * 0.0008,
                "size": random.uniform(1, 2.5),
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
                       p["size"] * 2, p["size"] * 2))
        painter.end()

    def resizeEvent(self, e):
        super().resizeEvent(e)


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
            grad.setColorAt(max(0, center - 0.3), QColor(a.red(), a.green(), a.blue(), 20))
            grad.setColorAt(center, QColor(a.red(), a.green(), a.blue(), 200))
            grad.setColorAt(min(1, center + 0.3), QColor(a.red(), a.green(), a.blue(), 20))
        else:
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0,   QColor(a.red(), a.green(), a.blue(), 0))
            grad.setColorAt(0.5, QColor(a.red(), a.green(), a.blue(), 60))
            grad.setColorAt(1,   QColor(a.red(), a.green(), a.blue(), 0))
        painter.fillRect(0, 0, w, 3, QBrush(grad))
        painter.end()


class PersonaCard(QFrame):
    def __init__(self, name, color):
        super().__init__()
        self.name      = name
        self.color     = color
        self.collapsed = False
        self._pulse_timer  = QTimer()
        self._think_states = ["thinking.", "thinking..", "thinking..."]
        self._think_index  = 0
        self._pulse_timer.timeout.connect(self._do_pulse)
        self.setMinimumWidth(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._apply_idle_style()

        self._opacity   = QGraphicsOpacityEffect()
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
            "color:" + color + ";font-size:11px;font-weight:bold;"
            "letter-spacing:1px;border:none;background:transparent;")
        hrow.addWidget(self.name_label)
        hrow.addStretch()

        self.collapse_btn = QPushButton("▾")
        self.collapse_btn.setFixedSize(20, 20)
        self.collapse_btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:" + color + "88;font-size:14px;}"
            "QPushButton:hover{color:" + color + ";}")
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
            "QPushButton{background:transparent;border:1px solid " + color + "44;"
            "color:" + color + "88;font-size:9px;letter-spacing:1px;border-radius:2px;}"
            "QPushButton:hover{border-color:" + color + "aa;color:" + color + ";}")
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
            "QFrame{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 " + self._bg() + ",stop:1 #0d0d0d);"
            "border:3px solid " + self.color + ";border-radius:6px;}")

    def _apply_thinking_style(self, op):
        c = QColor(self.color)
        c.setAlpha(op)
        rgba = "rgba(" + str(c.red()) + "," + str(c.green()) + "," + str(c.blue()) + "," + str(op) + ")"
        self.setStyleSheet(
            "QFrame{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 " + self._bg() + ",stop:1 #0d0d0d);"
            "border:3px solid " + rgba + ";border-radius:6px;}")

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
        if existing and existing not in ("...", "thinking.", "thinking..", "thinking..."):
            self.text.setPlainText(existing)
        self._apply_idle_style()

    def update_color(self, color):
        self.color = color
        self.name_label.setStyleSheet(
            "color:" + color + ";font-size:11px;font-weight:bold;"
            "letter-spacing:1px;border:none;background:transparent;")
        self.collapse_btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:" + color + "88;font-size:14px;}"
            "QPushButton:hover{color:" + color + ";}")
        self.copy_btn.setStyleSheet(
            "QPushButton{background:transparent;border:1px solid " + color + "44;"
            "color:" + color + "88;font-size:9px;letter-spacing:1px;border-radius:2px;}"
            "QPushButton:hover{border-color:" + color + "aa;color:" + color + ";}")
        self._apply_idle_style()


class RoundIndicator(QWidget):
    def __init__(self, total_rounds=2):
        super().__init__()
        self.setFixedHeight(28)
        self.setStyleSheet("QWidget { background: transparent; } QLabel { background: #111; }")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self.pills = []
        self._current_round = 0
        self._build(total_rounds)

    def _pill_style(self, state):
        a = QColor(settings["accent"])
        ar, ag, ab = a.red(), a.green(), a.blue()
        if state == "done":
            return ("QLabel{background:transparent;"
                    "border:1px solid rgba(" + str(ar) + "," + str(ag) + "," + str(ab) + ",80);"
                    "color:rgba(" + str(ar) + "," + str(ag) + "," + str(ab) + ",120);"
                    "font-size:9px;letter-spacing:1px;padding:2px 10px;border-radius:11px;}")
        if state == "active":
            return ("QLabel{background:rgba(" + str(ar) + "," + str(ag) + "," + str(ab) + ",40);"
                    "border:1px solid rgba(" + str(ar) + "," + str(ag) + "," + str(ab) + ",255);"
                    "color:rgba(" + str(ar) + "," + str(ag) + "," + str(ab) + ",255);"
                    "font-size:9px;letter-spacing:1px;padding:2px 10px;border-radius:11px;}")
        return "QLabel{background:transparent;border:1px solid #333;color:#444;font-size:9px;letter-spacing:1px;padding:2px 10px;border-radius:11px;}"

    def _build(self, n):
        for i in reversed(range(self._layout.count())):
            w = self._layout.itemAt(i).widget()
            if w:
                w.deleteLater()
        self.pills = []
        for i in range(n):
            pill = QLabel("Round " + str(i + 1))
            pill.setFixedHeight(22)
            pill.setStyleSheet(self._pill_style("idle"))
            pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._layout.addWidget(pill)
            self.pills.append(pill)
        stretch = QWidget()
        stretch.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._layout.addWidget(stretch)

    def set_rounds(self, n):
        if n != len(self.pills):
            self._build(n)

    def set_round(self, r):
        self._current_round = r
        for i, pill in enumerate(self.pills):
            if i + 1 < r:
                pill.setStyleSheet(self._pill_style("done"))
            elif i + 1 == r:
                pill.setStyleSheet(self._pill_style("active"))
            else:
                pill.setStyleSheet(self._pill_style("idle"))

    def reset(self):
        self._current_round = 0
        for pill in self.pills:
            pill.setStyleSheet(self._pill_style("idle"))

    def reapply(self):
        if self._current_round > 0:
            self.set_round(self._current_round)


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
            "color:" + settings['accent'] + ";font-size:11px;letter-spacing:3px;font-weight:bold;")
        main.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        sw = QWidget()
        sl = QVBoxLayout(sw)
        sl.setSpacing(16)

        for i, p in enumerate(personas):
            group = QFrame()
            group.setStyleSheet("QFrame{border:1px solid #2a2a2a;border-radius:4px;padding:4px;}")
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
                "background:" + p['color'] + ";color:#111;font-size:10px;"
                "font-weight:bold;border:none;border-radius:2px;")
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
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
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
                "background:" + h + ";color:#111;font-size:10px;"
                "font-weight:bold;border:none;border-radius:2px;")
            self.color_buttons[idx].setProperty("chosen_color", h)

    def get_values(self):
        result = []
        for i in range(len(personas)):
            color = self.color_buttons[i].property("chosen_color") or personas[i]["color"]
            result.append({
                "name":   self.name_edits[i].text().strip(),
                "color":  color,
                "system": self.system_edits[i].toPlainText().strip()
            })
        return result

    def reset_to_defaults(self):
        defaults = [
            {"name": "The Analyst", "color": "#C8A96E",
             "system": "You are a sharp analytical thinker on a decision-making council. Break problems into parts, identify risks, look for logical inconsistencies. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
            {"name": "The Devil's Advocate", "color": "#C0392B",
             "system": "You are a devil's advocate on a decision-making council. Challenge the direction the group is leaning, poke holes in assumptions, surface uncomfortable truths. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
            {"name": "The Pragmatist", "color": "#7F8C8D",
             "system": "You are a pragmatic realist on a decision-making council. Ask what is actually achievable given real constraints. Push for the simplest thing that could work. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
            {"name": "The Empath", "color": "#8E6DB5",
             "system": "You are an emotionally intelligent advisor on a decision-making council. Consider the human side of every decision. Push back when the group is too cold. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
            {"name": "The Visionary", "color": "#2E86AB",
             "system": "You are a big-picture thinker on a decision-making council. Ask what the long-term consequences are. Challenge short-term thinking. Keep responses to 4-5 sentences. Never soften your position under social pressure."},
        ]
        for i, d in enumerate(defaults):
            self.name_edits[i].setText(d["name"])
            self.system_edits[i].setPlainText(d["system"])
            self.color_buttons[i].setStyleSheet(
                "background:" + d['color'] + ";color:#111;font-size:10px;"
                "font-weight:bold;border:none;border-radius:2px;")
            self.color_buttons[i].setProperty("chosen_color", d["color"])


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
            SettingsPanel { background: #0c0c0c; border-left: 1px solid #333; border-radius: 0px; }
            QLabel { background: transparent; color: #999; font-size: 11px; }
            QLineEdit { background: #1a1a1a; border: 1px solid #444; color: #eee; padding: 4px 8px; border-radius: 2px; font-size: 12px; }
            QCheckBox { color: #999; font-size: 11px; spacing: 8px; }
            QCheckBox::indicator { width: 14px; height: 14px; background: #1a1a1a; border: 1px solid #444; border-radius: 2px; }
            QCheckBox::indicator:checked { background: #C8A96E; border-color: #C8A96E; }
            QSpinBox { background: #1a1a1a; border: 1px solid #444; color: #C8A96E; padding: 4px 8px; }
            QSpinBox::up-button, QSpinBox::down-button { width: 16px; background: #222; border: none; }
            QSlider::groove:horizontal { height: 4px; background: #2a2a2a; border-radius: 2px; }
            QSlider::handle:horizontal { width: 14px; height: 14px; background: #C8A96E; border-radius: 7px; margin: -5px 0; }
            QSlider::sub-page:horizontal { background: #C8A96E; border-radius: 2px; }
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { width: 4px; background: #0c0c0c; }
            QScrollBar::handle:vertical { background: #2a2a2a; border-radius: 2px; }
        """)

        self._anim = QPropertyAnimation(self, b"maximumWidth")
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        hdr_widget = QWidget()
        hdr_widget.setStyleSheet("background:#0c0c0c;border-bottom:1px solid #1e1e1e;")
        hdr_widget.setFixedHeight(50)
        hdr = QHBoxLayout(hdr_widget)
        hdr.setContentsMargins(18, 0, 14, 0)
        t = QLabel("SETTINGS")
        t.setStyleSheet(
            "color:" + settings['accent'] + ";font-size:12px;letter-spacing:3px;"
            "font-weight:bold;background:transparent;")
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
            lbl.setStyleSheet("color:#444;font-size:9px;letter-spacing:2px;font-weight:bold;")
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
            val_lbl.setStyleSheet("color:#888;font-size:10px;background:transparent;")
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
            reset.clicked.connect(lambda: self._reset_color(key, default_hex, btn))
            r.addWidget(lbl)
            r.addWidget(btn)
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            r.addWidget(reset)
            r.addStretch()
            L.addLayout(r)

        sep("APPEARANCE")

        self.accent_btn = QPushButton()
        self.accent_btn.setFixedSize(24, 24)
        self.accent_btn.setStyleSheet(
            "background:" + settings['accent'] + ";border:1px solid #555;border-radius:4px;")
        self.accent_btn.clicked.connect(self.pick_accent)
        color_row("Accent color", self.accent_btn, DEFAULT_SETTINGS["accent"], "accent")

        self.card_bg_btn = QPushButton()
        self.card_bg_btn.setFixedSize(24, 24)
        self.card_bg_btn.setStyleSheet(
            "background:" + settings['card_bg'] + ";border:1px solid #555;border-radius:4px;")
        self.card_bg_btn.clicked.connect(self.pick_card_bg)
        color_row("Card background", self.card_bg_btn, DEFAULT_SETTINGS["card_bg"], "card_bg")

        self.bg_btn = QPushButton()
        self.bg_btn.setFixedSize(24, 24)
        self.bg_btn.setStyleSheet(
            "background:" + settings.get('bg_color', '#0f0f0f') + ";border:1px solid #555;border-radius:4px;")
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
        self.font_size_lbl = QLabel(str(settings["font_size"]) + "pt")
        self.font_size_lbl.setStyleSheet("color:#888;font-size:10px;background:transparent;")
        self.font_size_lbl.setFixedWidth(32)
        self.font_size_slider.valueChanged.connect(self._on_font_size)
        srow("Font size", self.font_size_slider, self.font_size_lbl)

        sep("BEHAVIOUR")

        self.temp_slider = QSlider(Qt.Orientation.Horizontal)
        self.temp_slider.setRange(0, 100)
        self.temp_slider.setValue(int(settings["temperature"] * 100))
        self.temp_lbl = QLabel(str(settings["temperature"]))
        self.temp_slider.valueChanged.connect(self._on_temp)
        srow("Temperature", self.temp_slider, self.temp_lbl)

        self.max_tokens_input = QLineEdit(str(settings["max_tokens"]))
        self.max_tokens_input.setFixedHeight(28)
        self.max_tokens_input.setMaximumWidth(80)
        self.max_tokens_input.textChanged.connect(
            lambda v: self._set("max_tokens", int(v)) if v.isdigit() else None)
        lrow("Max tokens", self.max_tokens_input)

        self.ctx_input = QLineEdit("" if not settings["ctx_override"] else str(settings["ctx_override"]))
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
        self.mod_check.stateChanged.connect(lambda v: self._set("show_moderator", bool(v)))
        L.addWidget(self.mod_check)

        self.notif_check = QCheckBox("Desktop notifications")
        self.notif_check.setChecked(settings["notifications"])
        self.notif_check.stateChanged.connect(lambda v: self._set("notifications", bool(v)))
        L.addWidget(self.notif_check)

        self.autosave_check = QCheckBox("Auto-save debates")
        self.autosave_check.setChecked(settings["auto_save"])
        self.autosave_check.stateChanged.connect(lambda v: self._set("auto_save", bool(v)))
        L.addWidget(self.autosave_check)

        sep("BACKEND")

        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["llama.cpp", "Ollama"])
        self.backend_combo.setCurrentText(settings["backend"])
        self.backend_combo.setFixedHeight(28)
        self.backend_combo.setMaximumWidth(120)
        self.backend_combo.setStyleSheet(self.COMBO_STYLE)
        self.backend_combo.currentTextChanged.connect(self._on_backend)
        lrow("Backend", self.backend_combo)

        # --- llama.cpp mode selector (Bundled / Custom Binary / Manual) ---
        self.llamacpp_mode_row = QWidget()
        lcr = QHBoxLayout(self.llamacpp_mode_row)
        lcr.setContentsMargins(0, 0, 0, 0)
        lcr.setSpacing(8)
        lcm_lbl = QLabel("Mode")
        lcm_lbl.setFixedWidth(110)
        lcm_lbl.setStyleSheet("color:#888;font-size:11px;background:transparent;")
        self.llamacpp_mode_combo = QComboBox()
        self.llamacpp_mode_combo.addItems(
            ["Bundled (auto)", "Custom Binary Path", "Manual / External Server"])
        mode_display = {
            "bundled": "Bundled (auto)",
            "custom":  "Custom Binary Path",
            "manual":  "Manual / External Server",
        }.get(settings["llamacpp_mode"], "Manual / External Server")
        self.llamacpp_mode_combo.setCurrentText(mode_display)
        self.llamacpp_mode_combo.setFixedHeight(28)
        self.llamacpp_mode_combo.setMaximumWidth(220)
        self.llamacpp_mode_combo.setStyleSheet(self.COMBO_STYLE)
        self.llamacpp_mode_combo.currentTextChanged.connect(self._on_llamacpp_mode)
        lcr.addWidget(lcm_lbl)
        lcr.addWidget(self.llamacpp_mode_combo)
        lcr.addStretch()
        L.addWidget(self.llamacpp_mode_row)

        self.port_row = QWidget()
        pr = QHBoxLayout(self.port_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(8)
        port_lbl = QLabel("Port")
        port_lbl.setFixedWidth(110)
        port_lbl.setStyleSheet("color:#888;font-size:11px;background:transparent;")
        self.port_input = QLineEdit(settings["port"])
        self.port_input.setFixedHeight(28)
        self.port_input.setMaximumWidth(80)
        self.port_input.textChanged.connect(self._on_port)
        pr.addWidget(port_lbl)
        pr.addWidget(self.port_input)
        pr.addStretch()
        L.addWidget(self.port_row)

        self.model_row = QWidget()
        mr = QHBoxLayout(self.model_row)
        mr.setContentsMargins(0, 0, 0, 0)
        mr.setSpacing(8)
        self.model_lbl = QLabel("Model name")
        self.model_lbl.setFixedWidth(110)
        self.model_lbl.setStyleSheet("color:#888;font-size:11px;background:transparent;")
        self.model_input = QLineEdit(settings["model_name"])
        self.model_input.setFixedHeight(28)
        self.model_input.setMaximumWidth(160)
        self.model_input.setPlaceholderText("for Ollama")
        self.model_input.textChanged.connect(self._on_model_changed)
        mr.addWidget(self.model_lbl)
        mr.addWidget(self.model_input)
        mr.addStretch()
        L.addWidget(self.model_row)

        # --- Model (.gguf) picker — shown for Bundled and Custom modes ---
        self.gguf_row = QWidget()
        gr = QHBoxLayout(self.gguf_row)
        gr.setContentsMargins(0, 0, 0, 0)
        gr.setSpacing(8)
        gguf_lbl = QLabel("Model (.gguf)")
        gguf_lbl.setFixedWidth(110)
        gguf_lbl.setStyleSheet("color:#888;font-size:11px;background:transparent;")
        self.gguf_btn = QPushButton(
            self._short_path(settings["model_gguf_path"]) or "Choose .gguf file...")
        self.gguf_btn.setFixedHeight(28)
        self.gguf_btn.setMaximumWidth(220)
        self.gguf_btn.setStyleSheet(
            "QPushButton{background:#1a1a1a;border:1px solid #444;color:#C8A96E;"
            "font-size:10px;padding:0 8px;border-radius:2px;text-align:left;}"
            "QPushButton:hover{border-color:#666;}")
        self.gguf_btn.clicked.connect(self.pick_model_gguf)
        gr.addWidget(gguf_lbl)
        gr.addWidget(self.gguf_btn)
        gr.addStretch()
        L.addWidget(self.gguf_row)

        # --- GPU detection label + Download Backend — Bundled mode only ---
        self.gpu_detect_label = QLabel("")
        self.gpu_detect_label.setWordWrap(True)
        self.gpu_detect_label.setStyleSheet("color:#777;font-size:10px;background:transparent;")
        L.addWidget(self.gpu_detect_label)
        try:
            self.gpu_detect_label.setText(bm.gpu_detection_label(bm.detect_gpu()))
        except Exception:
            self.gpu_detect_label.setText("GPU detection unavailable.")

        self.download_btn = QPushButton("DOWNLOAD BACKEND")
        self.download_btn.setFixedHeight(30)
        self.download_btn.setStyleSheet(
            "QPushButton{background:#1a1a1a;border:1px solid #444;color:#777;"
            "font-size:10px;letter-spacing:1px;border-radius:2px;}"
            "QPushButton:hover{border-color:#C8A96E;color:#C8A96E;}"
            "QPushButton:disabled{color:#444;border-color:#2a2a2a;}")
        L.addWidget(self.download_btn)

        self.download_status_label = QLabel("")
        self.download_status_label.setWordWrap(True)
        self.download_status_label.setStyleSheet("color:#555;font-size:10px;background:transparent;")
        L.addWidget(self.download_status_label)

        self.download_progress_bar = QProgressBar()
        self.download_progress_bar.setRange(0, 100)
        self.download_progress_bar.setValue(0)
        self.download_progress_bar.setFixedHeight(14)
        self.download_progress_bar.setTextVisible(True)
        self.download_progress_bar.setVisible(False)
        self.download_progress_bar.setStyleSheet(
            "QProgressBar{background:#1a1a1a;border:1px solid #444;border-radius:2px;"
            "color:#ccc;font-size:9px;text-align:center;}"
            "QProgressBar::chunk{background:" + settings["accent"] + ";border-radius:2px;}")
        L.addWidget(self.download_progress_bar)

        # --- Custom binary path + test — Custom mode only ---
        self.custom_binary_row = QWidget()
        cbr = QHBoxLayout(self.custom_binary_row)
        cbr.setContentsMargins(0, 0, 0, 0)
        cbr.setSpacing(8)
        cb_lbl = QLabel("Binary path")
        cb_lbl.setFixedWidth(110)
        cb_lbl.setStyleSheet("color:#888;font-size:11px;background:transparent;")
        self.custom_binary_input = QLineEdit(settings["custom_binary_path"])
        self.custom_binary_input.setFixedHeight(28)
        self.custom_binary_input.setMaximumWidth(160)
        self.custom_binary_input.setPlaceholderText("/path/to/llama-server")
        self.custom_binary_input.textChanged.connect(
            lambda v: self._set("custom_binary_path", v))
        cbr.addWidget(cb_lbl)
        cbr.addWidget(self.custom_binary_input)
        cbr.addStretch()
        L.addWidget(self.custom_binary_row)

        self.custom_binary_browse_btn = QPushButton("BROWSE FOR BINARY")
        self.custom_binary_browse_btn.setFixedHeight(28)
        self.custom_binary_browse_btn.setStyleSheet(
            "QPushButton{background:#1a1a1a;border:1px solid #444;color:#777;"
            "font-size:10px;letter-spacing:1px;border-radius:2px;}"
            "QPushButton:hover{border-color:#C8A96E;color:#C8A96E;}")
        self.custom_binary_browse_btn.clicked.connect(self.browse_custom_binary)
        L.addWidget(self.custom_binary_browse_btn)

        self.custom_test_btn = QPushButton("TEST BINARY")
        self.custom_test_btn.setFixedHeight(28)
        self.custom_test_btn.setStyleSheet(
            "QPushButton{background:#1a1a1a;border:1px solid #444;color:#777;"
            "font-size:10px;letter-spacing:1px;border-radius:2px;}"
            "QPushButton:hover{border-color:#C8A96E;color:#C8A96E;}")
        L.addWidget(self.custom_test_btn)

        self.custom_test_result = QLabel("")
        self.custom_test_result.setWordWrap(True)
        self.custom_test_result.setStyleSheet("color:#555;font-size:10px;background:transparent;")
        L.addWidget(self.custom_test_result)

        # --- GPU layers — Bundled and Custom modes ---
        self.ngl_auto_check = QCheckBox("Auto-fit GPU layers (recommended)")
        self.ngl_auto_check.setChecked(settings["gpu_layers_auto"])
        self.ngl_auto_check.stateChanged.connect(self._on_ngl_auto_changed)
        L.addWidget(self.ngl_auto_check)

        self.ngl_row = QWidget()
        nglr = QHBoxLayout(self.ngl_row)
        nglr.setContentsMargins(0, 0, 0, 0)
        nglr.setSpacing(8)
        ngl_lbl = QLabel("GPU layers")
        ngl_lbl.setFixedWidth(110)
        ngl_lbl.setStyleSheet("color:#888;font-size:11px;background:transparent;")
        self.ngl_spin = QSpinBox()
        self.ngl_spin.setRange(0, 200)
        self.ngl_spin.setValue(settings["n_gpu_layers"])
        self.ngl_spin.setFixedHeight(28)
        self.ngl_spin.setMaximumWidth(70)
        self.ngl_spin.setEnabled(not settings["gpu_layers_auto"])
        self.ngl_spin.valueChanged.connect(lambda v: self._set("n_gpu_layers", v))
        nglr.addWidget(ngl_lbl)
        nglr.addWidget(self.ngl_spin)
        nglr.addStretch()
        L.addWidget(self.ngl_row)

        # --- Start/Stop managed subprocess — Bundled and Custom modes ---
        self.backend_toggle_btn = QPushButton("START BACKEND")
        self.backend_toggle_btn.setFixedHeight(30)
        self.backend_toggle_btn.setStyleSheet(
            "QPushButton{background:#1a1a1a;border:1px solid #444;color:#777;"
            "font-size:10px;letter-spacing:1px;border-radius:2px;}"
            "QPushButton:hover{border-color:#C8A96E;color:#C8A96E;}"
            "QPushButton:disabled{color:#444;border-color:#2a2a2a;}")
        L.addWidget(self.backend_toggle_btn)

        self.backend_status_label = QLabel("Status: " + bm.STATUS_STOPPED)
        self.backend_status_label.setStyleSheet("color:#666;font-size:10px;background:transparent;")
        L.addWidget(self.backend_status_label)

        self.verbose_logs_check = QCheckBox("Verbose backend logs (restart backend to apply)")
        self.verbose_logs_check.setChecked(settings["verbose_logs"])
        self.verbose_logs_check.stateChanged.connect(
            lambda v: self._set("verbose_logs", bool(v)))
        L.addWidget(self.verbose_logs_check)

        self.view_log_btn = QPushButton("VIEW BACKEND LOG")
        self.view_log_btn.setFixedHeight(26)
        self.view_log_btn.setStyleSheet(
            "QPushButton{background:#1a1a1a;border:1px solid #333;color:#666;"
            "font-size:9px;letter-spacing:1px;border-radius:2px;}"
            "QPushButton:hover{border-color:#888;color:#aaa;}")
        L.addWidget(self.view_log_btn)

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
        self.test_result.setStyleSheet("color:#555;font-size:10px;background:transparent;")
        L.addWidget(self.test_result)

        self._update_backend_visibility()

        sep("WEB SEARCH")

        self.search_check = QCheckBox("Enable web search")
        self.search_check.setChecked(settings["search_enabled"])
        self.search_check.stateChanged.connect(lambda v: self._set("search_enabled", bool(v)))
        L.addWidget(self.search_check)

        search_info = QLabel("Powered by SearXNG")
        search_info.setWordWrap(True)
        search_info.setStyleSheet("color:#555;font-size:10px;background:transparent;")
        L.addWidget(search_info)

        self.search_url_input = QLineEdit(settings["search_url"])
        self.search_url_input.setFixedHeight(28)
        self.search_url_input.setMaximumWidth(220)
        self.search_url_input.textChanged.connect(lambda v: self._set("search_url", v))
        lrow("SearXNG URL", self.search_url_input, 90)

        self.search_results_spin = QSpinBox()
        self.search_results_spin.setRange(1, 10)
        self.search_results_spin.setValue(settings["search_results"])
        self.search_results_spin.setFixedHeight(28)
        self.search_results_spin.setMaximumWidth(60)
        self.search_results_spin.valueChanged.connect(lambda v: self._set("search_results", v))
        lrow("Results", self.search_results_spin, 90)

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
        self.search_test_result.setStyleSheet("color:#555;font-size:10px;background:transparent;")
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

    def _set(self, key, value):
        settings[key] = value
        save_settings()
        self.changed.emit()

    def _reset_color(self, key, default, btn):
        self._set(key, default)
        btn.setStyleSheet("background:" + default + ";border:1px solid #555;border-radius:2px;")

    def _on_font_size(self, v):
        self.font_size_lbl.setText(str(v) + "pt")
        self._set("font_size", v)

    def _on_temp(self, v):
        t = v / 100.0
        self.temp_lbl.setText(str(round(t, 2)))
        self._set("temperature", t)

    def _on_backend(self, v):
        global CURRENT_BACKEND
        CURRENT_BACKEND = v
        self._set("backend", v)
        self._update_backend_visibility()

    def _on_model_changed(self, v):
        global CURRENT_MODEL
        CURRENT_MODEL = v
        self._set("model_name", v)

    def _on_port(self, v):
        global LLAMA_HOST, LLAMA
        self._set("port", v)
        LLAMA_HOST = "http://127.0.0.1:" + v
        LLAMA      = LLAMA_HOST + "/v1/chat/completions"

    def _short_path(self, path, max_len=26):
        if not path:
            return ""
        name = os.path.basename(path)
        if len(name) <= max_len:
            return name
        return "..." + name[-(max_len - 3):]

    def _on_ngl_auto_changed(self, state):
        is_auto = bool(state)
        self._set("gpu_layers_auto", is_auto)
        self.ngl_spin.setEnabled(not is_auto)

    def _on_llamacpp_mode(self, v):
        mode_map = {
            "Bundled (auto)":            "bundled",
            "Custom Binary Path":        "custom",
            "Manual / External Server":  "manual",
        }
        self._set("llamacpp_mode", mode_map.get(v, "manual"))
        self._update_backend_visibility()

    def _update_backend_visibility(self):
        is_llamacpp = self.backend_combo.currentText() == "llama.cpp"
        mode = settings["llamacpp_mode"]
        managed = is_llamacpp and mode in ("bundled", "custom")

        self.llamacpp_mode_row.setVisible(is_llamacpp)
        self.model_row.setVisible(self.backend_combo.currentText() == "Ollama")
        self.port_row.setVisible(not managed)
        self.gguf_row.setVisible(managed)
        self.ngl_row.setVisible(managed)
        self.ngl_auto_check.setVisible(managed)
        self.backend_toggle_btn.setVisible(managed)
        self.backend_status_label.setVisible(managed)
        self.verbose_logs_check.setVisible(managed)
        self.view_log_btn.setVisible(managed)

        self.gpu_detect_label.setVisible(is_llamacpp and mode == "bundled")
        self.download_btn.setVisible(is_llamacpp and mode == "bundled")
        self.download_status_label.setVisible(is_llamacpp and mode == "bundled")

        self.custom_binary_row.setVisible(is_llamacpp and mode == "custom")
        self.custom_binary_browse_btn.setVisible(is_llamacpp and mode == "custom")
        self.custom_test_btn.setVisible(is_llamacpp and mode == "custom")
        self.custom_test_result.setVisible(is_llamacpp and mode == "custom")

    def pick_model_gguf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select model (.gguf)", os.path.expanduser("~"),
            "GGUF models (*.gguf);;All files (*)")
        if path:
            self._set("model_gguf_path", path)
            self.gguf_btn.setText(self._short_path(path))

    def browse_custom_binary(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select llama-server binary", os.path.expanduser("~"))
        if path:
            self.custom_binary_input.setText(path)
            self._set("custom_binary_path", path)

    def pick_accent(self):
        c = QColorDialog.getColor(QColor(settings["accent"]), self)
        if c.isValid():
            self._set("accent", c.name())
            self.accent_btn.setStyleSheet(
                "background:" + c.name() + ";border:1px solid #555;border-radius:2px;")

    def pick_card_bg(self):
        c = QColorDialog.getColor(QColor(settings["card_bg"]), self)
        if c.isValid():
            self._set("card_bg", c.name())
            self.card_bg_btn.setStyleSheet(
                "background:" + c.name() + ";border:1px solid #555;border-radius:2px;")

    def pick_font(self):
        from PyQt6.QtGui import QFontDatabase
        dialog = QDialog(self)
        dialog.setWindowTitle("Font")
        dialog.setFixedSize(260, 360)
        dialog.setStyleSheet(
            "QDialog{background:#111;}"
            "QListWidget{background:#1a1a1a;color:#eee;border:1px solid #333;font-size:12px;outline:none;}"
            "QListWidget::item:selected{background:#2a2a2a;color:#C8A96E;}"
            "QLineEdit{background:#1a1a1a;border:1px solid #333;color:#eee;padding:4px 8px;font-size:12px;}"
            "QPushButton{background:#C8A96E;color:#111;border:none;padding:6px;font-weight:bold;border-radius:2px;}"
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
        c = QColorDialog.getColor(QColor(settings.get("bg_color", "#0f0f0f")), self)
        if c.isValid():
            self._set("bg_color", c.name())
            self.bg_btn.setStyleSheet(
                "background:" + c.name() + ";border:1px solid #555;border-radius:4px;")

    def test_connection(self):
        self.test_result.setText("Testing...")
        self.test_btn.setEnabled(False)
        QApplication.processEvents()
        try:
            ctx = get_context_size(settings["backend"], settings["model_name"])
            r = requests.post(LLAMA, json={
                "model": CURRENT_MODEL if CURRENT_MODEL else "local",
                "messages": [{"role": "user", "content": "reply with just the word ok"}],
                "max_tokens": settings.get("max_tokens", 10),
                "temperature": settings.get("temperature", 0.1),
            }, timeout=10)
            reply = r.json()["choices"][0]["message"]["content"]
            self.test_result.setStyleSheet("color:#5a9;font-size:10px;background:transparent;")
            self.test_result.setText("Connected: ctx " + str(ctx) + ", replied: " + reply[:40])
        except Exception as e:
            self.test_result.setStyleSheet("color:#c0392b;font-size:10px;background:transparent;")
            self.test_result.setText("Failed: " + str(e))
        self.test_btn.setEnabled(True)

    def test_search(self):
        self.search_test_result.setText("Testing...")
        self.search_test_btn.setEnabled(False)
        QApplication.processEvents()
        try:
            results, sources = web_search("capital of France")
            if results and not results.startswith("["):
                self.search_test_result.setStyleSheet("color:#5a9;font-size:10px;background:transparent;")
                self.search_test_result.setText("Search working — " + str(len(sources)) + " source(s) found")
            else:
                self.search_test_result.setStyleSheet("color:#e67e22;font-size:10px;background:transparent;")
                self.search_test_result.setText(results)
        except Exception as e:
            self.search_test_result.setStyleSheet("color:#c0392b;font-size:10px;background:transparent;")
            self.search_test_result.setText("Failed: " + str(e))
        self.search_test_btn.setEnabled(True)

    def _reset_all(self):
        global settings
        settings.update(DEFAULT_SETTINGS)
        save_settings()
        self.blockSignals(True)
        self.accent_btn.setStyleSheet(
            "background:" + DEFAULT_SETTINGS['accent'] + ";border:1px solid #555;border-radius:2px;")
        self.card_bg_btn.setStyleSheet(
            "background:" + DEFAULT_SETTINGS['card_bg'] + ";border:1px solid #555;border-radius:2px;")
        self.font_size_slider.setValue(DEFAULT_SETTINGS["font_size"])
        self.temp_slider.setValue(int(DEFAULT_SETTINGS["temperature"] * 100))
        self.max_tokens_input.setText(str(DEFAULT_SETTINGS["max_tokens"]))
        self.rounds_spin.setValue(DEFAULT_SETTINGS["rounds"])
        self.mod_check.setChecked(DEFAULT_SETTINGS["show_moderator"])
        self.notif_check.setChecked(DEFAULT_SETTINGS["notifications"])
        self.autosave_check.setChecked(DEFAULT_SETTINGS["auto_save"])
        self.backend_combo.setCurrentText(DEFAULT_SETTINGS["backend"])
        self.port_input.setText(DEFAULT_SETTINGS["port"])
        self.llamacpp_mode_combo.setCurrentText("Manual / External Server")
        self.custom_binary_input.setText(DEFAULT_SETTINGS["custom_binary_path"])
        self.gguf_btn.setText("Choose .gguf file...")
        self.ngl_spin.setValue(DEFAULT_SETTINGS["n_gpu_layers"])
        self.ngl_auto_check.setChecked(DEFAULT_SETTINGS["gpu_layers_auto"])
        self.ngl_spin.setEnabled(not DEFAULT_SETTINGS["gpu_layers_auto"])
        self.verbose_logs_check.setChecked(DEFAULT_SETTINGS["verbose_logs"])
        self._update_backend_visibility()
        self.search_check.setChecked(DEFAULT_SETTINGS["search_enabled"])
        self.search_url_input.setText(DEFAULT_SETTINGS["search_url"])
        self.search_results_spin.setValue(DEFAULT_SETTINGS["search_results"])
        self.bg_btn.setStyleSheet("background:#0f0f0f;border:1px solid #555;border-radius:4px;")
        self.blockSignals(False)
        self.changed.emit()

    def show_panel(self):
        p = self.parent()
        if p:
            self.setGeometry(p.width() - self.PANEL_WIDTH - 8, 0, self.PANEL_WIDTH, p.height())
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


class CouncilApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("The Council")
        self.setMinimumSize(1100, 760)
        self.worker     = None
        self.debate_log = []

        icon_path = resource_path(os.path.join("icons", "ai-council.ico"))
        app_icon = QIcon(icon_path)
        self.setWindowIcon(app_icon)
        _setup_tray(app_icon)

        bg = settings.get("bg_color", "#0f0f0f")
        self.setStyleSheet(
            "QMainWindow{background:" + bg + ";}"
            "CouncilApp > QWidget{background:" + bg + ";color:#cccccc;}"
            "QLabel{background:transparent;}")

        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)
        main.setContentsMargins(24, 20, 24, 20)
        main.setSpacing(12)

        self._header_frame = QFrame()
        self._header_frame.setStyleSheet(
            "QFrame{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #1a1a1a,stop:1 " + bg + ");border:none;"
            "border-bottom:1px solid #1e1e1e;border-radius:6px;}")
        self._header_frame.setFixedHeight(70)
        hf_layout = QHBoxLayout(self._header_frame)
        hf_layout.setContentsMargins(16, 0, 16, 0)

        crest_path = os.path.join(ASSETS_DIR, "crest.png")
        if os.path.exists(crest_path):
            cl = QLabel()
            pix = QPixmap(crest_path).scaled(
                44, 44, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            cl.setPixmap(pix)
            hf_layout.addWidget(cl)

        self.title_lbl = QLabel("THE COUNCIL")
        self.title_lbl.setFont(QFont("serif", 22, QFont.Weight.Bold))
        self.title_lbl.setStyleSheet(
            "color:" + settings['accent'] + ";letter-spacing:6px;background:transparent;")
        hf_layout.addWidget(self.title_lbl)
        hf_layout.addStretch()

        a = settings["accent"]
        hbtn = (
            "QPushButton{background:transparent;border:1px solid #2a2a2a;"
            "color:#666;font-size:10px;letter-spacing:1px;padding:4px 12px;border-radius:3px;}"
            "QPushButton:hover{border-color:" + a + "77;color:" + a + ";}")

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
            "QPushButton{background:transparent;border:1px solid #2a2a2a;"
            "color:#666;font-size:16px;border-radius:3px;}"
            "QPushButton:hover{border-color:" + a + "77;color:" + a + ";}"
            "QPushButton:checked{background:" + a + "18;border-color:" + a + "66;color:" + a + ";}")
        self.settings_btn.clicked.connect(self.toggle_settings)
        hf_layout.addWidget(self.settings_btn)
        main.addWidget(self._header_frame)

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
        self.search_label.setStyleSheet("color:#444;font-size:9px;letter-spacing:2px;")
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
        self.status_label.setStyleSheet("color:#444;font-size:11px;letter-spacing:1px;")
        main.addWidget(self.status_label)

        input_row = QHBoxLayout()
        input_row.setSpacing(10)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Bring your question to the council...")
        self.input.setFixedHeight(44)
        card_bg = settings.get("card_bg", "#1a1a1a")
        self.input.setStyleSheet(
            "QLineEdit{background:" + card_bg + ";border:2px solid #444444;"
            "color:#eee;font-size:14px;padding:0 14px;border-radius:4px;}"
            "QLineEdit:focus{border:2px solid #888888;background:" + card_bg + ";}")
        self.input.setFont(QFont(settings["font_family"], 13))
        self.input.returnPressed.connect(self.submit)
        input_row.addWidget(self.input)

        self._btn_normal_style = (
            "QPushButton{background:" + a + ";color:#111;border:none;font-size:11px;"
            "font-weight:bold;letter-spacing:2px;border-radius:4px;}"
            "QPushButton:hover{background:#d4b87a;}"
            "QPushButton:pressed{background:#b8994e;}"
            "QPushButton:disabled{background:#1e1e1e;color:#444;}")
        self.btn = QPushButton("CONVENE")
        self.btn.setFixedSize(110, 44)
        self.btn.setStyleSheet(self._btn_normal_style)
        self.btn.clicked.connect(self.submit)
        input_row.addWidget(self.btn)
        main.addLayout(input_row)

        self._btn_pulse_state = 0
        self._btn_pulse_timer = QTimer()
        self._btn_pulse_timer.timeout.connect(self._pulse_btn)

        self.settings_panel = SettingsPanel(central)
        self.settings_panel.changed.connect(self._on_settings_changed)

        self.llama_manager = None
        self._pending_submit_after_start = False
        self._pending_question = None
        self._backend_poll_timer = QTimer()
        self._backend_poll_timer.timeout.connect(self._poll_backend_health)
        self.settings_panel.download_btn.clicked.connect(self.handle_download_backend)
        self.settings_panel.backend_toggle_btn.clicked.connect(self.handle_backend_toggle)
        self.settings_panel.custom_test_btn.clicked.connect(self.handle_test_custom_binary)
        self.settings_panel.view_log_btn.clicked.connect(self.handle_view_backend_log)

        global CURRENT_BACKEND, CURRENT_MODEL, LLAMA_HOST, LLAMA
        CURRENT_BACKEND = settings["backend"]
        CURRENT_MODEL   = settings["model_name"]
        LLAMA_HOST      = "http://127.0.0.1:" + settings['port']
        LLAMA           = LLAMA_HOST + "/v1/chat/completions"

    def closeEvent(self, event):
        global _tray_icon
        if self.llama_manager is not None:
            self.llama_manager.stop()
            self.llama_manager = None
        if _tray_icon:
            _tray_icon.hide()
            _tray_icon = None
        event.accept()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cw = self.centralWidget()
        if cw and hasattr(self, "settings_panel") and self.settings_panel.isVisible():
            pw = self.settings_panel.PANEL_WIDTH
            self.settings_panel.setGeometry(cw.width() - pw - 8, 0, pw, cw.height())

    def _pulse_btn(self):
        spinners = ["|", "/", "-", "\\"]
        dots = ["", ".", "..", "..."]
        self._btn_pulse_state = (self._btn_pulse_state + 1) % 20
        spin = spinners[self._btn_pulse_state % len(spinners)]
        dot = dots[self._btn_pulse_state % len(dots)]
        a = settings["accent"]
        self.status_label.setStyleSheet(
            "color:" + a + ";font-size:11px;letter-spacing:1px;font-weight:bold;")
        self.status_label.setFont(QFont("Courier New", 11))
        self.status_label.setText(spin + " deliberating" + dot)

    def build_cards(self):
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.cards = {}
        for p in personas:
            card = PersonaCard(p["name"], p["color"])
            self.cards[p["name"]] = card
            self.cards_layout.addWidget(card)

    def toggle_settings(self, checked):
        if checked:
            cw = self.centralWidget()
            pw = self.settings_panel.PANEL_WIDTH
            self.settings_panel.setGeometry(cw.width() - pw - 8, 0, pw, cw.height())
            self.settings_panel.show_panel()
        else:
            self.settings_panel.hide_panel()

    def _on_settings_changed(self):
        QTimer.singleShot(300, lambda: self.settings_btn.setChecked(
            self.settings_panel.isVisible()))
        bg = settings.get("bg_color", "#0f0f0f")
        card_bg = settings.get("card_bg", "#1a1a1a")
        a = settings["accent"]
        self.setStyleSheet(
            "QMainWindow { background: " + bg + "; }"
            "CouncilApp > QWidget { background: " + bg + "; }")
        self._header_frame.setStyleSheet(
            "QFrame{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #1a1a1a,stop:1 " + bg + ");border:none;"
            "border-bottom:1px solid #1e1e1e;border-radius:6px;}")
        self.input.setStyleSheet(
            "QLineEdit{background:" + card_bg + ";border:2px solid #444444;"
            "color:#eee;font-size:14px;padding:0 14px;border-radius:4px;}"
            "QLineEdit:focus{border:2px solid #888888;background:" + card_bg + ";}")
        self.status_label.setStyleSheet("color:" + a + ";font-size:11px;letter-spacing:1px;")
        for card in self.cards.values():
            card.refresh_style()
        self.moderator_card.refresh_style()
        self.moderator_card.setVisible(settings["show_moderator"])
        self.round_indicator.set_rounds(settings["rounds"])
        self.input.setFont(QFont(settings["font_family"], 13))
        hbtn = (
            "QPushButton{background:transparent;border:1px solid #2a2a2a;"
            "color:#666;font-size:10px;letter-spacing:1px;padding:4px 12px;border-radius:3px;}"
            "QPushButton:hover{border-color:" + a + "77;color:" + a + ";}")
        self.personas_btn.setStyleSheet(hbtn)
        self.reset_btn.setStyleSheet(hbtn)
        self.export_btn.setStyleSheet(hbtn)
        self.settings_btn.setStyleSheet(
            "QPushButton{background:transparent;border:1px solid #2a2a2a;"
            "color:#666;font-size:16px;border-radius:3px;}"
            "QPushButton:hover{border-color:" + a + "77;color:" + a + ";}"
            "QPushButton:checked{background:" + a + "18;border-color:" + a + "66;color:" + a + ";}")
        self._btn_normal_style = (
            "QPushButton{background:" + a + ";color:#111;border:none;font-size:11px;"
            "font-weight:bold;letter-spacing:2px;border-radius:4px;}"
            "QPushButton:hover{background:#d4b87a;}"
            "QPushButton:pressed{background:#b8994e;}"
            "QPushButton:disabled{background:#1e1e1e;color:#444;}")
        self.btn.setStyleSheet(self._btn_normal_style)
        self.title_lbl.setStyleSheet("color:" + a + ";letter-spacing:6px;background:transparent;")
        self.round_indicator.reapply()

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
        if not self.debate_log:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Debate", "council_debate.md",
            "Markdown (*.md);;Text (*.txt)")
        if not path:
            return
        is_txt = path.endswith(".txt")
        with open(path, "w", encoding="utf-8") as f:
            for entry in self.debate_log:
                q = entry["question"]
                v = entry["verdict"]
                if is_txt:
                    f.write("Question: " + q + "\n\n")
                    for rn, rd in enumerate(entry["rounds"], 1):
                        f.write("Round " + str(rn) + "\n\n")
                        for name, text in rd.items():
                            f.write(name + "\n" + text + "\n\n")
                    f.write("Verdict\n" + v + "\n\n---\n\n")
                else:
                    f.write("# Council Debate\n\n## Question\n\n" + q + "\n\n")
                    for rn, rd in enumerate(entry["rounds"], 1):
                        f.write("### Round " + str(rn) + "\n\n")
                        for name, text in rd.items():
                            f.write("**" + name + "**\n\n" + text + "\n\n")
                    f.write("### Verdict\n\n" + v + "\n\n---\n\n")

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
        if not settings["auto_save"]:
            return
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(DEBATES_DIR, "debate_" + ts + ".md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Council Debate\n\n## Question\n\n" + question + "\n\n")
            for rn, rd in enumerate(rounds_data, 1):
                f.write("### Round " + str(rn) + "\n\n")
                for name, text in rd.items():
                    f.write("**" + name + "**\n\n" + text + "\n\n")
            f.write("### Verdict\n\n" + verdict + "\n\n")

    # --- Bundled/Custom llama.cpp backend management ---

    def _resolve_backend_binary(self):
        mode = settings["llamacpp_mode"]
        if mode == "bundled":
            return settings.get("bundled_binary_path", "")
        if mode == "custom":
            return settings.get("custom_binary_path", "")
        return ""

    def handle_download_backend(self):
        sp = self.settings_panel
        sp.download_btn.setEnabled(False)
        sp.download_status_label.setStyleSheet("color:#777;font-size:10px;background:transparent;")
        sp.download_status_label.setText("Starting download...")
        sp.download_progress_bar.setRange(0, 100)
        sp.download_progress_bar.setValue(0)
        sp.download_progress_bar.setFormat("")
        sp.download_progress_bar.setVisible(False)
        self._download_worker = BackendDownloadWorker(BIN_DIR)
        self._download_worker.status.connect(lambda s: sp.download_status_label.setText(s))
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.done.connect(self._on_download_done)
        self._download_worker.start()

    def _on_download_progress(self, downloaded, total):
        sp = self.settings_panel
        mb = round(downloaded / 1_000_000, 1)
        sp.download_progress_bar.setVisible(True)
        if total:
            pct = int(downloaded * 100 / total)
            total_mb = round(total / 1_000_000, 1)
            sp.download_progress_bar.setRange(0, 100)
            sp.download_progress_bar.setValue(pct)
            sp.download_progress_bar.setFormat(str(mb) + "MB / " + str(total_mb) + "MB")
            sp.download_status_label.setText("Downloading...")
        else:
            # No content-length header available -- show an indeterminate-
            # style bar (range 0,0 makes Qt animate it as a busy indicator)
            # rather than a fake percentage we can't actually back up.
            sp.download_progress_bar.setRange(0, 0)
            sp.download_progress_bar.setFormat(str(mb) + "MB")
            sp.download_status_label.setText("Downloading...")

    def _on_download_done(self, success, message, commit_verified):
        sp = self.settings_panel
        sp.download_btn.setEnabled(True)
        sp.download_progress_bar.setVisible(False)
        if success:
            settings["bundled_binary_path"] = message
            save_settings()
            if commit_verified is True:
                verify_note = " (release commit: GitHub-verified ✓)"
            elif commit_verified is False:
                verify_note = " (⚠ release commit is NOT GitHub-verified)"
            else:
                verify_note = " (release verification status unknown)"
            sp.download_status_label.setStyleSheet("color:#5a9;font-size:10px;background:transparent;")
            sp.download_status_label.setText(
                "Backend ready: " + sp._short_path(message) + verify_note)
        else:
            sp.download_status_label.setStyleSheet("color:#c0392b;font-size:10px;background:transparent;")
            sp.download_status_label.setText("Failed: " + message)

    def handle_backend_toggle(self):
        if self.llama_manager is not None and self.llama_manager.status in (
                bm.STATUS_RUNNING, bm.STATUS_STARTING):
            self.stop_backend()
            return

        sp = self.settings_panel
        binary_path = self._resolve_backend_binary()
        model_path  = settings.get("model_gguf_path", "")
        if not binary_path:
            sp.backend_status_label.setStyleSheet("color:#c0392b;font-size:10px;background:transparent;")
            sp.backend_status_label.setText("Status: no binary set — download or browse first.")
            return
        if not model_path:
            sp.backend_status_label.setStyleSheet("color:#c0392b;font-size:10px;background:transparent;")
            sp.backend_status_label.setText("Status: choose a .gguf model first.")
            return
        self.start_backend(binary_path, model_path)

    def start_backend(self, binary_path, model_path):
        sp = self.settings_panel
        ngl = None if settings.get("gpu_layers_auto", True) else settings.get("n_gpu_layers", 99)
        extra_args = ["--log-verbose"] if settings.get("verbose_logs", False) else []
        self.llama_manager = bm.LlamaServerManager(
            binary_path, model_path,
            n_gpu_layers=ngl,
            ctx_size=settings.get("ctx_override") or 8192,
            extra_args=extra_args)
        sp.backend_toggle_btn.setEnabled(False)
        sp.backend_status_label.setStyleSheet("color:#e67e22;font-size:10px;background:transparent;")
        sp.backend_status_label.setText("Status: " + bm.STATUS_STARTING)
        self._backend_start_worker = BackendStartWorker(self.llama_manager, timeout=90)
        self._backend_start_worker.ready.connect(self._on_backend_started)
        self._backend_start_worker.start()

    def _on_backend_started(self, ok):
        global LLAMA_HOST, LLAMA
        sp = self.settings_panel
        sp.backend_toggle_btn.setEnabled(True)
        if ok and self.llama_manager:
            LLAMA_HOST = "http://127.0.0.1:" + str(self.llama_manager.port)
            LLAMA = LLAMA_HOST + "/v1/chat/completions"
            sp.backend_toggle_btn.setText("STOP BACKEND")
            sp.backend_status_label.setStyleSheet("color:#5a9;font-size:10px;background:transparent;")
            sp.backend_status_label.setText(
                "Status: Running (port " + str(self.llama_manager.port) + ")")
            self._backend_poll_timer.start(5000)
        else:
            err = self.llama_manager.last_error if self.llama_manager else "Unknown error"
            sp.backend_status_label.setStyleSheet("color:#c0392b;font-size:10px;background:transparent;")
            sp.backend_status_label.setText("Status: Error — " + str(err)[:160])
            self.llama_manager = None

        if getattr(self, "_pending_submit_after_start", False):
            self._pending_submit_after_start = False
            question = self._pending_question
            self._pending_question = None
            if ok:
                self._do_submit(question)
            else:
                self.status_label.setText("Backend failed to start — check Settings.")

    def stop_backend(self):
        sp = self.settings_panel
        if self.llama_manager:
            self.llama_manager.stop()
            self.llama_manager = None
        self._backend_poll_timer.stop()
        sp.backend_toggle_btn.setEnabled(True)
        sp.backend_toggle_btn.setText("START BACKEND")
        sp.backend_status_label.setStyleSheet("color:#666;font-size:10px;background:transparent;")
        sp.backend_status_label.setText("Status: " + bm.STATUS_STOPPED)

    def _poll_backend_health(self):
        if self.llama_manager and not self.llama_manager.poll_health():
            sp = self.settings_panel
            sp.backend_status_label.setStyleSheet("color:#c0392b;font-size:10px;background:transparent;")
            sp.backend_status_label.setText(
                "Status: Error — backend stopped unexpectedly.")
            sp.backend_toggle_btn.setText("START BACKEND")
            self._backend_poll_timer.stop()

    def handle_test_custom_binary(self):
        sp = self.settings_panel
        path = sp.custom_binary_input.text().strip()
        if not path or not os.path.isfile(path):
            sp.custom_test_result.setStyleSheet("color:#c0392b;font-size:10px;background:transparent;")
            sp.custom_test_result.setText("File not found.")
            return
        try:
            result = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=10)
            output = (result.stdout or result.stderr or "").strip()
            sp.custom_test_result.setStyleSheet("color:#5a9;font-size:10px;background:transparent;")
            sp.custom_test_result.setText("OK: " + output[:160])
        except Exception as e:
            sp.custom_test_result.setStyleSheet("color:#c0392b;font-size:10px;background:transparent;")
            sp.custom_test_result.setText("Failed: " + str(e))

    def handle_view_backend_log(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Backend Log")
        dialog.setMinimumSize(640, 420)
        dialog.setStyleSheet(
            "QDialog{background:#111;} QWidget{background:#111;color:#ccc;}"
            "QPushButton{background:#1a1a1a;border:1px solid #444;color:#ccc;"
            "padding:6px 16px;border-radius:2px;}"
            "QPushButton:hover{border-color:#C8A96E;color:#C8A96E;}")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        hint = QLabel(
            "Raw output from the backend process. Look for lines mentioning "
            "the GPU backend it initialized (e.g. \"ggml_vulkan\") and how "
            "many layers were offloaded, for real confirmation of GPU use.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#777;font-size:10px;background:transparent;")
        layout.addWidget(hint)

        log_view = QTextEdit()
        log_view.setReadOnly(True)
        log_view.setFont(QFont("Courier New", 10))
        log_view.setStyleSheet(
            "QTextEdit{background:#0a0a0a;border:1px solid #2a2a2a;color:#ccc;padding:8px;}")

        if self.llama_manager is None:
            log_view.setPlainText("No managed backend has been started yet.")
        else:
            lines = self.llama_manager.get_recent_log()
            log_view.setPlainText(
                "\n".join(lines) if lines else
                "(no output captured yet -- the backend may still be starting, "
                "or this binary doesn't log to stderr.)")

        layout.addWidget(log_view)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)

        dialog.exec()

    def submit(self):
        question = self.input.text().strip()
        if not question or self.worker is not None:
            return

        managed = (settings["backend"] == "llama.cpp"
                   and settings["llamacpp_mode"] in ("bundled", "custom"))

        if managed:
            if self.llama_manager is not None and self.llama_manager.status == bm.STATUS_RUNNING:
                self._do_submit(question)
                return
            binary_path = self._resolve_backend_binary()
            model_path  = settings.get("model_gguf_path", "")
            if not binary_path or not model_path:
                self.status_label.setText(
                    "Set a backend binary and a .gguf model in Settings first.")
                return
            self._pending_submit_after_start = True
            self._pending_question = question
            self.status_label.setText("Starting backend...")
            self.start_backend(binary_path, model_path)
            return

        try:
            requests.get(LLAMA_HOST + "/props", timeout=2)
        except Exception:
            try:
                requests.get(LLAMA_HOST + "/v1/models", timeout=2)
            except Exception:
                self.status_label.setText(
                    "Cannot reach server — check port and backend in settings.")
                return

        self._do_submit(question)

    def _do_submit(self, question):
        self.current_question   = question
        self.current_rounds     = []
        self.current_round_data = {}
        self.btn.setEnabled(False)
        self._btn_pulse_timer.start(285)
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
        self.worker.verdict_start.connect(lambda: self.moderator_card.start_thinking())
        self.worker.verdict_done.connect(self.on_verdict)
        self.worker.search_done.connect(lambda t: self.search_box.setPlainText(t))
        self.worker.finished.connect(self.on_finished)
        self.worker.start()


    def on_round_start(self, round_num):
        if self.current_round_data:
            self.current_rounds.append(dict(self.current_round_data))
        self.current_round_data = {}
        QTimer.singleShot(0, lambda: self.round_indicator.set_round(round_num))

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
        self.status_label.setStyleSheet("color:#444;font-size:11px;letter-spacing:1px;")


def main():
    import traceback
    try:
        print("APP STARTED")
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        icon_path = resource_path(os.path.join("icons", "ai-council.ico"))
        app.setWindowIcon(QIcon(icon_path))
        window = CouncilApp()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        with open("crash_log.txt", "w") as f:
            f.write(traceback.format_exc())
        raise

if __name__ == "__main__":
    main()
