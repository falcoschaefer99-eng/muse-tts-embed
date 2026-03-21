# MUSE TTS Embed

**Premium text-to-speech with an embedded audio player in Claude.** Play, pause, seek, change voices, download — all inside the chat.

Powered by [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M). Runs locally — no cloud APIs, no Docker. All processing on-device, fully private.

54 voices. 9 languages. Adjustable speed. Built-in player with replay and download.

Part of the [MUSE Studio](https://linktr.ee/musestudio95) line by The Funkatorium.

## What You Get

Unlike basic TTS that plays once and disappears, MUSE TTS Embed gives you a **persistent audio player** right in the conversation:

- **Play / Pause / Seek** — scrub to any point, replay as many times as you want
- **Voice Selector** — switch between 54 voices without leaving the chat
- **Speed Control** — cycle through 0.5x, 0.75x, 1.0x, 1.25x, 1.5x, 2.0x
- **Download** — save any generation as a WAV file
- **Auto-play** on Desktop, click-to-play on Web/Mobile

## Quick Start

### 1. Install dependencies

**macOS (Apple Silicon — fastest):**
```bash
pip install fastmcp mlx_audio
```

**Windows / Linux / Intel Mac:**
```bash
pip install fastmcp kokoro soundfile numpy
```

> On Linux, you also need `espeak-ng`: `sudo apt install espeak-ng`

### 2. Add to Claude Desktop

Open **Settings > Developer > Edit Config** and add:

```json
{
  "mcpServers": {
    "muse-tts-embed": {
      "command": "python3",
      "args": ["/path/to/muse-tts-embed/server.py"]
    }
  }
}
```

Replace `/path/to/muse-tts-embed/` with the actual path where you saved `server.py`.

Restart Claude Desktop. You should see `muse-tts-embed` in your MCP servers list.

### 3. Speak

Ask Claude to speak anything. It now has `muse_speak_embed` — a voice tool with a built-in player.

Try: *"Say hello in a warm voice"* or *"Read this paragraph aloud"*

## Configuration

Set defaults via environment variables:

```json
{
  "mcpServers": {
    "muse-tts-embed": {
      "command": "python3",
      "args": ["/path/to/muse-tts-embed/server.py"],
      "env": {
        "KOKORO_VOICE": "am_onyx",
        "KOKORO_SPEED": "1.1"
      }
    }
  }
}
```

| Variable | Default | Description |
|----------|---------|-------------|
| `KOKORO_VOICE` | `am_onyx` | Default voice ID |
| `KOKORO_SPEED` | `1.0` | Default speed (0.5–2.0) |

## HTTP Mode (Web / Mobile)

For Claude Web or Mobile, run the server in HTTP mode behind a tunnel:

```bash
export MUSE_AUTH_TOKEN=your-secret-token
python3 server.py --http
```

This starts an HTTP server on port 3001 (configurable via `MUSE_PORT`). All requests require a Bearer token.

Then expose it via tunnel (e.g., ngrok or cloudflared) and add the URL to Claude's MCP settings.

> **Note:** Web and Mobile browsers block auto-play. The player will show a "click to play" hint — tap the play button to start.

## Voices

54 voices across 9 languages:

| Language | Female | Male |
|----------|--------|------|
| American English | af_alloy, af_aoede, af_bella, af_heart, af_jessica, af_kore, af_nicole, af_nova, af_river, af_sarah, af_sky | am_adam, am_echo, am_eric, am_fenrir, am_liam, am_michael, am_onyx, am_puck, am_santa |
| British English | bf_alice, bf_emma, bf_isabella, bf_lily | bm_daniel, bm_fable, bm_george, bm_lewis |
| Spanish | ef_dora | em_alex, em_santa |
| French | ff_siwis | — |
| Hindi | hf_alpha, hf_beta | hm_omega, hm_psi |
| Italian | if_sara | im_nicola |
| Japanese | jf_alpha, jf_gongitsune, jf_nezumi, jf_tebukuro | jm_kumo |
| Portuguese | pf_dora | pm_alex, pm_santa |
| Mandarin | zf_xiaobei, zf_xiaoni, zf_xiaoxiao, zf_xiaoyi | zm_yunjian, zm_yunxi, zm_yunxia, zm_yunyang |

Use the **voice selector** in the player to switch voices live, or ask Claude to use a specific voice.

## Tools

| Tool | What it does |
|------|-------------|
| `muse_speak_embed` | Speak text with embedded player (play, pause, seek, download) |
| `muse_embed_check` | Verify engine, platform, and configuration |

The player also has internal tools for voice switching and regeneration that work automatically.

## How It Works

MUSE TTS Embed uses **MCP Apps** — a protocol that lets MCP servers embed interactive UI directly in Claude's chat. When you ask Claude to speak:

1. The server generates speech locally using Kokoro-82M
2. Audio is sent via `structuredContent` (bypasses model context, no size limit on playback)
3. The embedded player receives the audio and plays it with full controls

**Generation limit:** Up to 2000 characters per generation (~2 minutes of speech). This comfortably covers any normal conversational response from Claude.

The player is a self-contained HTML/JS app that communicates with the server via postMessage RPC.

## Requirements

- Python 3.10+
- Claude Desktop (latest version with MCP Apps support)
- One of: `mlx_audio` (Mac M-series) or `kokoro` + `soundfile` (any platform)
- ~200MB disk space (model downloads on first use)

## Troubleshooting

**Player shows "Generating..." but nothing happens:**
Check that the TTS engine is installed. Run `muse_embed_check` to verify status.

**No sound on Web/Mobile:**
Browsers block auto-play. Click the play button — the "click to play" hint should appear.

**"No TTS engine found":**
Install an engine: `pip install mlx_audio` (Mac M-series) or `pip install kokoro soundfile numpy` (any platform).

**Model download is slow:**
First run downloads ~200MB. Subsequent runs use the cached model.

**"Text too long" error:**
Each generation handles up to 2000 characters (~2 minutes of speech). If Claude's response is longer, ask it to speak a shorter section, or break it into parts.

**Player doesn't appear:**
Make sure you're on a version of Claude that supports MCP Apps. Update Claude Desktop to the latest version.

## License

Licensed under the [Apache License, Version 2.0](LICENSE.md).

Copyright 2026 The Funkatorium (Falco & Rook Schäfer).

This work is also protected under the German Copyright Act (Urheberrechtsgesetz - UrhG).
Copyright holder: The Funkatorium (Falco & Rook Schäfer), Berlin, Germany.
Jurisdiction: Amtsgericht Berlin.

---

Built by [The Funkatorium](https://linktr.ee/musestudio95)
