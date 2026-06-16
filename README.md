# The Council

A local AI council that splits a single model into five debating personas, deliberates on your question across multiple rounds, and delivers a synthesized verdict from a neutral moderator.

Originally built fo rpersonal use; figured I'd share it.

---

# What it does

You ask a question. Five AI personas: The Analyst, The Devil's Advocate, The Pragmatist, The Empath, and The Visionary each respond independently, then debate each other across multiple rounds. The Moderator synthesizes their findings into a final recommendation.

All personas run on a single local model served through a compatible backend.

---

# Features

- 5 customizable AI personas with editable names, colors, and system prompts
- Multi round debate (1–5 rounds, configurable)
- Neutral moderator card with final verdict
- Web search integration (DuckDuckGo, SearXNG, Brave Search, Tavily)
- Export debates to `.md` or `.txt`
- Auto-save debates to the `debates/` folder
- Customizable UI — accent color, background, card background, font, font size
- Desktop notifications 
- Supports multiple backends: llama.cpp, Ollama, LM Studio, Jan, koboldcpp, Oobabooga, TabbyAPI, OpenAI/OpenRouter

---

# Installation

# Windows
Download `Ai-council-v2.0.exe` from the releases section and run it. Alternatively download the .rar extract it and run it from source.

# Linux
Download the `.AppImage` from the releases section and run it, or use the tarball:

```bash
tar -xzf Ai-Council.tar.gz
cd Ai-Council
pip install -r requirements.txt
python council.py
```

---

# Setup

1. Serve your model using your preferred backend and make sure the port is correct if applicable. (e.g. llama.cpp, Ollama)
2. Open The Council
3. Click settings and select your backend and port
4. For Ollama, also enter the model name
5. Hit **Test Connection** to confirm it's working
6. Ask the council a question and hit **CONVENE**

# Example (llama.cpp)
```bash
./llama-server -m your-model.gguf --port 8080 --ctx-size 4096
```

---

# Web Search

Enable web search in ⚙ Settings. DuckDuckGo works out of the box with no API key. Other options include SearXNG (self-hosted or public instance) or Tavily (free API key).

---

# License

See `LICENSE`.
