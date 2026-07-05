"""
backend_manager.py

Manages the "bundled" llama.cpp backend mode for The Council:
GPU detection, binary download/verification, and subprocess lifecycle.

This file is being built incrementally and tested standalone before
being wired into main.py. Step 1: GPU detection only.
"""

import platform
import shutil
import os
import hashlib
import zipfile
import tarfile
import requests
import subprocess
import socket
import time
import threading

GITHUB_API  = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
GITHUB_REPO_PREFIX = "https://github.com/ggml-org/llama.cpp/"

PLATFORM   = platform.system()
IS_LINUX   = PLATFORM == "Linux"
IS_WINDOWS = PLATFORM == "Windows"
IS_MAC     = PLATFORM == "Darwin"

_MACHINE = platform.machine().lower()
IS_ARM64 = _MACHINE in ("arm64", "aarch64")
IS_X64   = _MACHINE in ("x86_64", "amd64")


def _vulkan_loader_present():
    """
    Presence check for the Vulkan loader — mirrors the style of the
    nvidia-smi/rocminfo checks (no functional GPU test, just confirms the
    runtime dependency a Vulkan-built llama-server would link against is
    actually installed). Checked directly via known library/DLL paths
    rather than requiring the vulkaninfo CLI tool, since vulkaninfo is a
    separate debugging utility that often isn't installed even when the
    Vulkan loader + driver themselves are fully functional — requiring it
    would under-detect systems that already have everything they need.
    """
    if IS_WINDOWS:
        windir = os.environ.get("WINDIR", r"C:\Windows")
        return os.path.isfile(os.path.join(windir, "System32", "vulkan-1.dll"))
    if IS_MAC:
        # llama.cpp doesn't ship a macOS Vulkan build (Metal covers this
        # platform instead), so this is irrelevant on Mac regardless of
        # whether MoltenVK happens to be present.
        return False
    candidates = [
        "/usr/lib/x86_64-linux-gnu/libvulkan.so.1",
        "/usr/lib/libvulkan.so.1",
        "/usr/lib64/libvulkan.so.1",
        "/usr/lib/aarch64-linux-gnu/libvulkan.so.1",
    ]
    if any(os.path.isfile(p) for p in candidates):
        return True
    try:
        import ctypes.util
        return ctypes.util.find_library("vulkan") is not None
    except Exception:
        return False


def detect_gpu():
    """
    Detect which llama.cpp release asset variant to use, based on
    what's available on the system. Deliberately simple — no scoring,
    no VRAM checks, just presence checks for known-good signals.

    Returns one of: "cuda", "rocm", "vulkan", "metal", "cpu"

    Notes on ordering:
    - llama.cpp's official releases only publish a CUDA build for
      Windows — there is no official Linux CUDA asset. So on Linux,
      Vulkan (which does have an official prebuilt Linux build, and
      runs on NVIDIA cards too) is used instead of guessing at "cuda"
      and finding nothing to download.
    - rocminfo presence is checked before Vulkan on Linux, since a
      working ROCm install is a positive signal the card is officially
      ROCm-supported. If rocminfo isn't present (e.g. an AMD card that
      ROCm has dropped support for, like Polaris/gfx803), Vulkan is a
      well-supported fallback — Mesa's RADV driver covers AMD GPUs that
      official ROCm no longer does.
    """
    if IS_MAC:
        # llama.cpp's macOS releases are Metal-enabled by default.
        return "metal"

    if IS_WINDOWS and shutil.which("nvidia-smi"):
        return "cuda"

    if IS_LINUX and shutil.which("rocminfo"):
        return "rocm"

    if _vulkan_loader_present():
        return "vulkan"

    return "cpu"


def gpu_detection_label(variant):
    """Human-readable label for displaying detection results in the UI."""
    return {
        "cuda":   "NVIDIA GPU detected (CUDA build)",
        "rocm":   "AMD GPU detected (ROCm build)",
        "vulkan": "GPU detected via Vulkan (cross-vendor build)",
        "metal":  "macOS detected (Metal build)",
        "cpu":    "No supported GPU detected (CPU-only build)",
    }.get(variant, "Unknown")


class DownloadError(Exception):
    """Raised when fetching/verifying/extracting a release asset fails."""
    pass


def fetch_latest_release():
    """
    Fetch metadata for the latest llama.cpp release from the GitHub API.
    Only ever talks to api.github.com/repos/ggml-org/llama.cpp — never an
    arbitrary mirror — and relies on HTTPS for transport security.

    Returns the parsed JSON dict (includes "tag_name" and "assets" list,
    where each asset has "name", "browser_download_url", and "digest").
    """
    headers = {
        "User-Agent": "TheCouncil-App",
        "Accept": "application/vnd.github+json",
    }
    try:
        r = requests.get(GITHUB_API, headers=headers, timeout=10)
        if r.status_code == 403 and "rate limit" in r.text.lower():
            raise DownloadError(
                "GitHub API rate limit exceeded for this network. "
                "This is unauthenticated and shared per-IP (60 requests/hour) — "
                "wait a while and try again, or use Custom Binary Path instead.")
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise DownloadError("Could not reach GitHub releases API: " + str(e))


def _has_arch(n):
    """Check the filename contains an explicit architecture tag matching
    this machine. Returns False (no guessing) if architecture can't be
    confirmed — caller should fall back to Custom Binary Path."""
    if IS_ARM64:
        return "arm64" in n or "aarch64" in n
    if IS_X64:
        return "x64" in n or "x86_64" in n or "amd64" in n
    return False


# Accelerator keywords we explicitly do NOT support in v1 (bundled mode).
# Anything containing these is skipped rather than guessed at — users with
# this hardware should use Custom Binary Path instead.
_UNSUPPORTED_ACCELERATORS = ("sycl", "openvino", "opencl")


def _asset_name_matches(name, variant):
    """
    Match a release asset filename to the detected GPU variant + platform
    + CPU architecture, using an explicit include-list per variant rather
    than excluding known keywords — because llama.cpp adds new accelerator
    variants (sycl, openvino, opencl-adreno, etc.) often enough that an
    exclude-list silently goes stale and risks a false match.

    Real asset examples seen as of llama.cpp b9699:
        llama-b9699-bin-win-cuda-12.4-x64.zip
        llama-b9699-bin-win-cuda-13.3-x64.zip
        llama-b9699-bin-win-hip-radeon-x64.zip      (AMD on Windows)
        llama-b9699-bin-ubuntu-rocm-7.2-x64.tar.gz  (AMD on Linux)
        llama-b9699-bin-ubuntu-x64.tar.gz           (CPU)
        llama-b9699-bin-macos-arm64.tar.gz
        llama-b9699-bin-macos-x64.tar.gz
        llama-b9699-bin-ubuntu-s390x.tar.gz         (must NOT match x64 hosts)
        llama-b9699-bin-win-cpu-arm64.zip           (Windows on ARM)
        + various sycl/openvino/opencl variants — explicitly unsupported.

    Returns True/False. Unmatched/unsupported hardware returns False for
    every variant rather than falling through to a guessed "cpu" match.
    """
    n = name.lower()
    if not (n.endswith(".zip") or n.endswith(".tar.gz") or n.endswith(".tgz")):
        return False

    if n.startswith("cudart-"):
        # CUDA runtime support package (DLLs), not the llama-server binary
        # itself — must never be picked as the thing we try to launch.
        return False

    if not _has_arch(n):
        return False

    if any(kw in n for kw in _UNSUPPORTED_ACCELERATORS):
        # Never match an unsupported-accelerator asset, even for "cpu" —
        # e.g. "ubuntu-sycl-fp16-x64" must not be picked as a CPU build.
        return False

    if variant == "cuda":
        return IS_WINDOWS and "win" in n and "cuda" in n
    if variant == "rocm":
        if IS_LINUX:
            return ("ubuntu" in n or "linux" in n) and "rocm" in n
        if IS_WINDOWS:
            return "win" in n and "hip" in n
        return False
    if variant == "vulkan":
        if IS_LINUX:
            return ("ubuntu" in n or "linux" in n) and "vulkan" in n
        if IS_WINDOWS:
            return "win" in n and "vulkan" in n
        return False
    if variant == "metal":
        return IS_MAC and ("macos" in n or "osx" in n)
    if variant == "cpu":
        excluded = ("cuda", "rocm", "hip", "vulkan")
        if IS_WINDOWS:
            return "win" in n and not any(kw in n for kw in excluded)
        if IS_LINUX:
            return (("ubuntu" in n or "linux" in n)
                    and not any(kw in n for kw in excluded))
        if IS_MAC:
            return "macos" in n or "osx" in n
    return False


def _cuda_sort_key(asset_name):
    """
    When multiple CUDA assets match (e.g. cuda-12.4 vs cuda-13.3), prefer
    the lower/older CUDA toolkit version — it's the safer default since
    older GPU drivers can usually run binaries built against an older
    CUDA toolkit, but not a newer one. Returns a sortable key; lower
    version sorts first.
    """
    import re
    match = re.search(r"cuda-(\d+(?:\.\d+)?)", asset_name.lower())
    if match:
        return float(match.group(1))
    return 0.0


def find_asset(release_json, variant):
    """
    Pick the right asset dict from a release's asset list for the given
    GPU variant + current platform. Returns None if nothing matches
    (e.g. ROCm requested but llama.cpp didn't publish a ROCm build that
    release — caller should fall back to telling the user to use
    Custom Binary Path).

    If multiple assets match (e.g. cuda-12.4 and cuda-13.3 both qualify
    as "cuda" on this platform/arch), picks the one with the lower CUDA
    version for broader driver compatibility, rather than relying on
    whatever order the API happened to return them in.
    """
    assets = release_json.get("assets", [])
    candidates = [a for a in assets if _asset_name_matches(a.get("name", ""), variant)]

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    candidates.sort(key=lambda a: _cuda_sort_key(a.get("name", "")))
    return candidates[0]


def download_and_verify(asset, dest_dir, progress_callback=None):
    """
    Download a release asset to dest_dir, verifying its SHA-256 against
    the "digest" field GitHub reports for that asset (fetched fresh in
    the same API call that gave us the download URL — not a separately
    hosted checksums file, and not something we maintain by hand).

    progress_callback(bytes_downloaded, total_bytes) is called periodically
    if provided, for UI progress feedback.

    Returns the local path to the downloaded (verified) file.
    Raises DownloadError if the download fails or the hash doesn't match.
    """
    url = asset.get("browser_download_url")
    name = asset.get("name")
    expected_digest = asset.get("digest")  # e.g. "sha256:abc123..."

    if not url or not name:
        raise DownloadError("Asset metadata missing url or name.")

    if not url.startswith(GITHUB_REPO_PREFIX):
        # Refuse to download from anything other than the pinned repo,
        # even if asset metadata is somehow malformed/redirected.
        raise DownloadError("Refusing to download from unexpected host: " + url)

    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, name)
    tmp_path = dest_path + ".part"

    sha256 = hashlib.sha256()
    total = 0

    try:
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            total_size = int(r.headers.get("content-length", 0))
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    f.write(chunk)
                    sha256.update(chunk)
                    total += len(chunk)
                    if progress_callback:
                        progress_callback(total, total_size)
    except requests.RequestException as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise DownloadError("Download failed: " + str(e))

    actual_digest = "sha256:" + sha256.hexdigest()

    if expected_digest:
        if actual_digest != expected_digest:
            os.remove(tmp_path)
            raise DownloadError(
                "Checksum mismatch — downloaded file does not match "
                "the hash reported by GitHub for this asset. "
                "Expected " + expected_digest + " but got " + actual_digest + ". "
                "The download may be corrupted or tampered with; not installing it.")
    # If GitHub didn't report a digest for this asset (older releases may
    # not have one), we still proceed — transport was HTTPS and the host
    # was verified above. There's nothing to compare against in that case.

    os.replace(tmp_path, dest_path)
    return dest_path


def extract_archive(archive_path, dest_dir):
    """
    Extract a downloaded .zip or .tar.gz into dest_dir, after confirming
    it's actually a well-formed archive of the expected type (catches
    truncated/corrupted downloads or unexpected content before we ever
    try to run anything from it).
    """
    os.makedirs(dest_dir, exist_ok=True)

    if archive_path.endswith(".zip"):
        if not zipfile.is_zipfile(archive_path):
            raise DownloadError("Downloaded file is not a valid zip archive.")
        with zipfile.ZipFile(archive_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise DownloadError("Corrupted file inside archive: " + bad)
            zf.extractall(dest_dir)
    elif archive_path.endswith(".tar.gz") or archive_path.endswith(".tgz"):
        if not tarfile.is_tarfile(archive_path):
            raise DownloadError("Downloaded file is not a valid tar.gz archive.")
        with tarfile.open(archive_path) as tf:
            tf.extractall(dest_dir)
    else:
        raise DownloadError("Unrecognized archive format: " + archive_path)

    return dest_dir


def get_release_commit_verified(release_json):
    """
    Best-effort check of whether the release's target commit shows as
    GitHub-verified. This is a secondary signal, not a substitute for
    the checksum check above — it confirms the commit was pushed through
    GitHub's normal signing/verification flow, not that this specific
    binary asset is hash-correct (that's what download_and_verify does).
    Returns True/False/None (None = couldn't determine).
    """
    try:
        commit_sha = release_json.get("target_commitish")
        if not commit_sha:
            return None
        url = "https://api.github.com/repos/ggml-org/llama.cpp/commits/" + commit_sha
        r = requests.get(url, headers={"User-Agent": "TheCouncil-App"}, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data.get("commit", {}).get("verification", {}).get("verified")
    except Exception:
        return None


def find_free_port():
    """
    Ask the OS for a free port by binding to port 0 and reading back what
    it assigned. Avoids the race condition of manually checking a port
    and then binding to it separately (something else could grab it in
    between). This is the standard approach used by tools like Jupyter.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Status string constants used by LlamaServerManager.status
STATUS_STOPPED  = "Stopped"
STATUS_STARTING = "Starting"
STATUS_RUNNING  = "Running"
STATUS_ERROR    = "Error"


class LlamaServerManager:
    """
    Manages a single llama-server subprocess: launch, health-poll until
    ready, crash detection, and clean shutdown. One instance per running
    server; create a new one to start again after stopping.

    ctx_size defaults to 8192 rather than leaving it unset — llama-server's
    own default/auto-detected context size can be very large for some
    models (sometimes close to the model's full training context length),
    which inflates the KV cache memory requirement enough to trigger an
    OOM kill on machines without much spare RAM/VRAM. 8192 is a reasonable
    default for most debate-style usage; expose it as a setting if users
    need more.

    Usage:
        mgr = LlamaServerManager(binary_path, model_path)
        mgr.start()                  # launches subprocess, returns immediately
        mgr.wait_until_ready(30)     # blocks (with polling) until /health is up
        mgr.status                   # "Stopped" | "Starting" | "Running" | "Error"
        mgr.port                     # resolved port once started
        mgr.last_error                # human-readable error info, if status == Error
        mgr.stop()                   # terminate subprocess cleanly
    """

    def __init__(self, binary_path, model_path, n_gpu_layers=99, ctx_size=8192, extra_args=None):
        """
        n_gpu_layers: pass None to omit -ngl entirely and let llama-server's
        own built-in --fit logic (on by default in recent builds) pick the
        optimal value for actual free VRAM. Passing any explicit integer
        (including 99) disables that auto-fit behavior for layer count --
        llama.cpp does not adjust memory-allocation arguments the user has
        explicitly set, even if they don't actually fit. None is the safer
        default on memory-constrained cards; an explicit value is for
        people who want manual control and know what they're doing.
        """
        self.binary_path = binary_path
        self.model_path = model_path
        self.n_gpu_layers = n_gpu_layers
        self.ctx_size = ctx_size
        self.extra_args = extra_args or []

        self.process = None
        self.port = None
        self.status = STATUS_STOPPED
        self.last_error = None
        self._stderr_lines = []
        self._stderr_thread = None

    def start(self):
        """
        Launch the subprocess. Resolves a free port automatically and
        does not block waiting for the server to become ready — call
        wait_until_ready() separately for that (so callers can show a
        "Starting..." UI state in between).
        """
        if self.process is not None:
            raise RuntimeError("Already started — create a new manager instance to restart.")

        if not os.path.isfile(self.binary_path):
            self.status = STATUS_ERROR
            self.last_error = "Backend binary not found: " + self.binary_path
            raise FileNotFoundError(self.last_error)

        if not os.path.isfile(self.model_path):
            self.status = STATUS_ERROR
            self.last_error = "Model file not found: " + self.model_path
            raise FileNotFoundError(self.last_error)

        self.port = find_free_port()
        self.status = STATUS_STARTING
        self.last_error = None
        self._stderr_lines = []

        cmd = [
            self.binary_path,
            "-m", self.model_path,
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "-c", str(self.ctx_size),
        ]
        if self.n_gpu_layers is not None:
            cmd += ["-ngl", str(self.n_gpu_layers)]
        cmd += self.extra_args

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception as e:
            self.status = STATUS_ERROR
            self.last_error = "Failed to launch backend: " + str(e)
            raise

        # Drain stderr in a background thread so the pipe never fills up
        # and blocks the subprocess; keep only the last N lines for
        # diagnostics if it crashes.
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self):
        try:
            for line in self.process.stderr:
                self._stderr_lines.append(line.rstrip())
                if len(self._stderr_lines) > 50:
                    self._stderr_lines.pop(0)
        except Exception:
            pass

    def wait_until_ready(self, timeout=60):
        """
        Poll /health until the server responds OK, the process dies, or
        timeout elapses. Returns True if ready, False otherwise (check
        self.status / self.last_error for why).
        """
        if self.process is None:
            return False

        start = time.time()
        url = "http://127.0.0.1:" + str(self.port) + "/health"

        while time.time() - start < timeout:
            exit_code = self.process.poll()
            if exit_code is not None:
                self.status = STATUS_ERROR
                self.last_error = (
                    "Backend exited during startup (code " + str(exit_code) + "). "
                    "Last output:\n" + "\n".join(self._stderr_lines[-10:]))
                return False

            try:
                r = requests.get(url, timeout=2)
                if r.status_code == 200:
                    self.status = STATUS_RUNNING
                    return True
            except requests.RequestException:
                pass

            time.sleep(0.5)

        self.status = STATUS_ERROR
        self.last_error = "Backend did not become ready within " + str(timeout) + "s."
        self.stop()
        return False

    def get_recent_log(self, n=80):
        """
        Return the most recent n lines of the backend's stderr output.
        Useful for showing real confirmation of what the backend actually
        did on startup (e.g. which GPU backend it initialized, how many
        layers it offloaded) rather than just trusting the status dot.
        """
        return list(self._stderr_lines[-n:])

    def poll_health(self):
        """
        Lightweight check for use while the app is otherwise idle (e.g.
        before starting a debate round) to catch a backend that crashed
        sometime after startup. Updates self.status if a crash is found.
        Returns True if still running, False if not.
        """
        if self.process is None:
            return False

        exit_code = self.process.poll()
        if exit_code is not None and self.status == STATUS_RUNNING:
            self.status = STATUS_ERROR
            self.last_error = (
                "Backend process exited unexpectedly (code " + str(exit_code) + "). "
                "Last output:\n" + "\n".join(self._stderr_lines[-10:]))
            return False

        return self.status == STATUS_RUNNING

    def stop(self):
        """Terminate the subprocess cleanly. Safe to call multiple times."""
        if self.process is None:
            self.status = STATUS_STOPPED
            return

        if self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            except Exception:
                pass

        self.process = None
        self.status = STATUS_STOPPED


if __name__ == "__main__":
    # Standalone sanity check — run directly with `python3 backend_manager.py`
    variant = detect_gpu()
    print("Platform:", PLATFORM)
    print("Detected variant:", variant)
    print("Label:", gpu_detection_label(variant))
