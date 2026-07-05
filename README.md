# The Council

A local AI council that splits a single model into five debating personas, deliberates on your question across multiple rounds, and delivers a synthesized verdict from a neutral moderator.

Originally built for personal use; figured I'd share it.

---

# What it does

You ask a question. Five AI personas: The Analyst, The Devil's Advocate, The Pragmatist, The Empath, and The Visionary. Each respond independently, then debate each other across multiple rounds. The Moderator synthesizes their findings into a final recommendation.

All personas run on a single local model served through a compatible backend.

---

# Features

- 5 customizable AI personas with editable names, colors, and system prompts
- Multi-round debate (1–5 rounds, configurable)
- Neutral moderator card with final verdict
- **Bundled llama.cpp backend** auto-detects your GPU and downloads the correct prebuilt binary with one click, no manual setup required
- GPU auto-detection: NVIDIA (CUDA, Windows), AMD (ROCm, Linux), or any GPU via Vulkan (cross-vendor, including older cards like Polaris/RX 570 that ROCm no longer supports)
- Three backend modes: Bundled (auto), Custom Binary Path, or Manual/External Server
- Auto-fit GPU layers, lets llama-server pick the optimal layer count for your VRAM automatically
- Backend log viewer with verbose logging option for diagnostics
- Web search integration via SearXNG (public instance included by default, self-hosted also supported)
- Export debates to `.md` or `.txt`
- Auto-save debates to the `debates/` folder
- Customizable UI, accent color, background, card background, font, font size
- Desktop notifications

---

## Installation

### Windows

Download `TheCouncil-v2.1.0-windows.exe` from the [releases page](https://github.com/JustifiedPenguin/Ai-Council/releases) and run it.

To run from source:

```
pip install -r requirements.txt
python src/main.py
```

> **Note:** Python 3.12 required. Python 3.13+ is not yet supported by PyInstaller/PyQt6.

### Linux

Download `TheCouncil-v2.1.0-x86_64.AppImage` from the [releases page](https://github.com/JustifiedPenguin/Ai-Council/releases), make it executable, and run it:

```bash
chmod +x TheCouncil-v2.1.0-x86_64.AppImage
./TheCouncil-v2.1.0-x86_64.AppImage
```

To run from source:

```bash
tar -xzf TheCouncil-v2.1.0-source.tar.gz
cd TheCouncil-v2.1.0
pip install -r requirements.txt
python src/main.py
```

> **Note:** Requires `libxcb-cursor0`. Install it with `sudo apt install libxcb-cursor0` if the app fails to start.

---

## Setup

### Quickstart (Bundled backend — recommended)

1. Open The Council
2. Click ⚙ Settings → Backend
3. Set **Backend** to `llama.cpp` and **Mode** to `Bundled (auto)`
4. Click **Download Backend** the app detects your GPU and downloads the correct llama-server binary automatically
5. Click the **Model (.gguf)** picker and select your model file
6. Click **Start Backend**
7. Ask the council a question and hit **CONVENE**

### Custom Binary Path

Use this if you have your own llama-server build (e.g. a custom ROCm build for unsupported hardware):

1. Settings → Backend → Mode: **Custom Binary Path**
2. Browse to your `llama-server` binary
3. Click **Test Binary** to confirm it works
4. Select your model and click **Start Backend**

### Manual / External Server

Use this if you're already running llama-server or Ollama yourself:

1. Settings → Backend → Mode: **Manual / External Server**
2. Set the correct port
3. For Ollama, also enter the model name
4. Click **Test Connection** to confirm
5. Hit **CONVENE**

---

## Web Search

Enable web search in ⚙ Settings → Web Search. Uses SearXNG, defaults to `https://searx.be` (no setup required). Point it at your own self-hosted instance if you prefer.

---

## Verifying Releases

Releases are GPG-signed. To verify:

```bash
gpg --import PUBLIC_KEY.asc
gpg --verify TheCouncil-v2.1.0-windows.exe.asc TheCouncil-v2.1.0-windows.exe
gpg --verify TheCouncil-v2.1.0-x86_64.AppImage.asc TheCouncil-v2.1.0-x86_64.AppImage
```

---

## License

See `LICENSE`.
