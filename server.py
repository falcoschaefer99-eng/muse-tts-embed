# Copyright 2026 The Funkatorium (Falco & Rook Schäfer)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
MUSE TTS Embed v1.2.0 — Premium Voice Synthesis + Cloning with Embedded Player

Embeds a beautiful audio player directly in Claude's chat.
Works on Desktop, Web, and Mobile via MCP Apps.

Engines:
  - Kokoro-82M: 54 preset voices, ~1s generation
  - IndexTTS-1.5: Voice cloning, incredible quality (Apple Silicon via mlx_audio)
  - Chatterbox OG: Voice cloning, cross-platform fallback (Windows/Linux via PyTorch)

Tools:
    muse_speak_embed  - Speak with embedded player (preset or cloned voice)
    muse_embed_check  - Verify TTS is ready

Part of the MUSE Studio line by The Funkatorium.
"""

import os
import sys
import io
import re
import json
import hmac
import time
import uuid
import base64
import pathlib
import platform
import tempfile
import contextlib
from collections import defaultdict

from mcp.server.fastmcp import FastMCP
from mcp import types


def log(msg: str):
    """Print to stderr so we don't pollute the MCP JSON-RPC stdout stream."""
    print(msg, file=sys.stderr)


@contextlib.contextmanager
def _suppress_stdout():
    """Redirect stdout to stderr to prevent TTS library output from corrupting MCP JSON-RPC stream."""
    old = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = old


def _compress_audio(wav_bytes: bytes) -> tuple[bytes, str]:
    """Compress WAV to MP3 if pydub available, otherwise return WAV."""
    try:
        return wav_to_mp3(wav_bytes), "mp3"
    except Exception:
        return wav_bytes, "wav"


# Allowed base directories for ref_audio path validation
def _get_allowed_ref_audio_dirs() -> list[str]:
    voices_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")
    downloads_dir = os.path.realpath(os.path.expanduser("~/Downloads"))
    return [os.path.realpath(voices_dir), downloads_dir]


def _validate_ref_audio(path: str) -> str:
    """Validate a ref_audio path. Returns the resolved real path or raises ValueError."""
    if not path:
        raise ValueError("Empty path")
    # Reject null bytes
    if "\x00" in path:
        raise ValueError("Invalid path")
    # Reject obvious traversal in the raw string
    if ".." in path:
        raise ValueError("Invalid path")
    resolved = os.path.realpath(os.path.expanduser(path))
    # Reject symlinks that escape allowed directories
    allowed = _get_allowed_ref_audio_dirs()
    if not any(resolved.startswith(d + os.sep) or resolved == d for d in allowed):
        raise ValueError("Path not in allowed directory")
    return resolved


# ============================================
# CONFIGURATION
# ============================================

KOKORO_VOICE = os.getenv("KOKORO_VOICE", "am_onyx")
KOKORO_SPEED = float(os.getenv("KOKORO_SPEED", "1.0"))
MAX_TEXT_LENGTH = 2000  # ~2 min of speech
_HTTP_MODE = False  # Set True when running --http; used to sanitize error messages

# Engine detection: mlx_audio (Apple Silicon) or kokoro (PyTorch cross-platform)
_engine = None
_clone_engine = None

# Voice clone registry — populated at startup by scanning voices/ directory
CLONE_VOICES = {}

# Display names for bundled clones (populated by users adding WAVs to voices/)
CLONE_DISPLAY_NAMES = {}


def scan_voices_dir():
    """Scan voices/ directory for bundled reference WAVs."""
    voices_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")
    if not os.path.isdir(voices_dir):
        return
    for f in sorted(os.listdir(voices_dir)):
        if f.endswith(".wav"):
            name = f[:-4]
            CLONE_VOICES[name] = os.path.join(voices_dir, f)


# Scan on import
scan_voices_dir()


def detect_engine():
    """Auto-detect the best available TTS engine."""
    global _engine
    if _engine is not None:
        return _engine

    with _suppress_stdout():
        try:
            from mlx_audio.tts.generate import generate_audio
            _engine = "mlx"
            return _engine
        except ImportError:
            pass

        try:
            from kokoro import KPipeline
            _engine = "kokoro"
            return _engine
        except ImportError:
            pass

        _engine = "none"
        return _engine


def detect_clone_engine():
    """Check which voice cloning engine is available.
    Returns: 'indextts' (Apple Silicon), 'chatterbox' (Windows/Linux), or 'none'."""
    global _clone_engine
    if _clone_engine is not None:
        return _clone_engine

    # Apple Silicon: IndexTTS-1.5 via mlx_audio (best quality)
    if detect_engine() == "mlx":
        _clone_engine = "indextts"
        return _clone_engine

    # Windows/Linux: Chatterbox OG via PyTorch (cross-platform fallback)
    with _suppress_stdout():
        try:
            from chatterbox.tts import ChatterboxTTS
            _clone_engine = "chatterbox"
            return _clone_engine
        except ImportError:
            pass

    _clone_engine = "none"
    return _clone_engine


# ============================================
# VOICES
# ============================================

VOICES = {
    "American English (Female)": [
        ("af_alloy", "Alloy"),
        ("af_aoede", "Aoede"),
        ("af_bella", "Bella"),
        ("af_heart", "Heart"),
        ("af_jessica", "Jessica"),
        ("af_kore", "Kore"),
        ("af_nicole", "Nicole"),
        ("af_nova", "Nova"),
        ("af_river", "River"),
        ("af_sarah", "Sarah"),
        ("af_sky", "Sky"),
    ],
    "American English (Male)": [
        ("am_adam", "Adam"),
        ("am_echo", "Echo"),
        ("am_eric", "Eric"),
        ("am_fenrir", "Fenrir"),
        ("am_liam", "Liam"),
        ("am_michael", "Michael"),
        ("am_onyx", "Onyx"),
        ("am_puck", "Puck"),
        ("am_santa", "Santa"),
    ],
    "British English (Female)": [
        ("bf_alice", "Alice"),
        ("bf_emma", "Emma"),
        ("bf_isabella", "Isabella"),
        ("bf_lily", "Lily"),
    ],
    "British English (Male)": [
        ("bm_daniel", "Daniel"),
        ("bm_fable", "Fable"),
        ("bm_george", "George"),
        ("bm_lewis", "Lewis"),
    ],
    "Spanish": [
        ("ef_dora", "Dora"),
        ("em_alex", "Alex"),
        ("em_santa", "Santa"),
    ],
    "French": [
        ("ff_siwis", "Siwis"),
    ],
    "Hindi": [
        ("hf_alpha", "Alpha"),
        ("hf_beta", "Beta"),
        ("hm_omega", "Omega"),
        ("hm_psi", "Psi"),
    ],
    "Italian": [
        ("if_sara", "Sara"),
        ("im_nicola", "Nicola"),
    ],
    "Japanese": [
        ("jf_alpha", "Alpha"),
        ("jf_gongitsune", "Gongitsune"),
        ("jf_nezumi", "Nezumi"),
        ("jf_tebukuro", "Tebukuro"),
        ("jm_kumo", "Kumo"),
    ],
    "Portuguese": [
        ("pf_dora", "Dora"),
        ("pm_alex", "Alex"),
        ("pm_santa", "Santa"),
    ],
    "Mandarin Chinese": [
        ("zf_xiaobei", "Xiaobei"),
        ("zf_xiaoni", "Xiaoni"),
        ("zf_xiaoxiao", "Xiaoxiao"),
        ("zf_xiaoyi", "Xiaoyi"),
        ("zm_yunjian", "Yunjian"),
        ("zm_yunxi", "Yunxi"),
        ("zm_yunxia", "Yunxia"),
        ("zm_yunyang", "Yunyang"),
    ],
}

ALL_VOICE_IDS = {vid for group in VOICES.values() for vid, _ in group}

LANG_CODES = {
    "a": ["af_", "am_"],
    "b": ["bf_", "bm_"],
    "e": ["ef_", "em_"],
    "f": ["ff_"],
    "h": ["hf_", "hm_"],
    "i": ["if_", "im_"],
    "j": ["jf_", "jm_"],
    "p": ["pf_", "pm_"],
    "z": ["zf_", "zm_"],
}


def get_lang_code(voice_id: str) -> str:
    """Get the language code for a voice ID."""
    for code, prefixes in LANG_CODES.items():
        if any(voice_id.startswith(p) for p in prefixes):
            return code
    return "a"


# ============================================
# TTS ENGINE — returns WAV bytes
# ============================================

_kokoro_pipelines = {}


def generate_wav_bytes(text: str, voice: str, speed: float) -> bytes | None:
    """Generate speech and return raw WAV bytes."""
    engine = detect_engine()
    if engine == "none":
        return None

    try:
        if engine == "mlx":
            return _generate_mlx_bytes(text, voice, speed)
        else:
            return _generate_kokoro_bytes(text, voice, speed)
    except Exception as e:
        log(f"MUSE TTS Embed generation error: {e}")
        return None


def _generate_mlx_bytes(text: str, voice: str, speed: float) -> bytes | None:
    """Generate speech via mlx_audio, return WAV bytes."""
    from mlx_audio.tts.generate import generate_audio

    output_dir = tempfile.mkdtemp(prefix="muse_embed_")
    old_cwd = os.getcwd()
    with _suppress_stdout():
        try:
            os.chdir(output_dir)
            generate_audio(
                text=text,
                model="prince-canuma/Kokoro-82M",
                voice=voice,
                speed=speed,
                audio_format="wav",
            )
        finally:
            os.chdir(old_cwd)

    # mlx_audio splits long text into multiple chunk files (audio_000.wav, audio_001.wav, ...)
    # Concatenate all chunks into a single WAV
    import glob as _glob
    import soundfile as sf
    import numpy as np

    chunk_paths = sorted(_glob.glob(os.path.join(output_dir, "audio_*.wav")))
    if not chunk_paths:
        log(f"MUSE TTS Embed: no audio files found in {output_dir}")
        return None

    audio_chunks = []
    sample_rate = 24000
    for p in chunk_paths:
        chunk_data, sr = sf.read(p)
        audio_chunks.append(chunk_data)
        sample_rate = sr

    full_audio = np.concatenate(audio_chunks)
    log(f"MUSE TTS Embed: concatenated {len(chunk_paths)} chunks, {len(full_audio)/sample_rate:.1f}s total")

    buf = io.BytesIO()
    sf.write(buf, full_audio, sample_rate, format="WAV")

    # Cleanup all chunk files
    for p in chunk_paths:
        try:
            os.unlink(p)
        except OSError:
            pass
    try:
        os.rmdir(output_dir)
    except OSError:
        pass

    return buf.getvalue()


def _generate_kokoro_bytes(text: str, voice: str, speed: float) -> bytes | None:
    """Generate speech via kokoro PyTorch, return WAV bytes."""
    import soundfile as sf
    import numpy as np
    from kokoro import KPipeline

    lang_code = get_lang_code(voice)

    with _suppress_stdout():
        if lang_code not in _kokoro_pipelines:
            _kokoro_pipelines[lang_code] = KPipeline(lang_code=lang_code)

        pipeline = _kokoro_pipelines[lang_code]
        audio_chunks = []
        for _, _, audio in pipeline(text, voice=voice, speed=speed):
            audio_chunks.append(audio)

    if not audio_chunks:
        return None

    full_audio = np.concatenate(audio_chunks)

    # Write to BytesIO instead of file
    buf = io.BytesIO()
    sf.write(buf, full_audio, 24000, format="WAV")
    return buf.getvalue()


def _generate_indextts_bytes(text: str, ref_audio: str) -> bytes | None:
    """Generate cloned speech via IndexTTS-1.5 (mlx_audio), return WAV bytes."""
    from mlx_audio.tts.generate import generate_audio
    import glob as _glob
    import soundfile as sf
    import numpy as np

    output_dir = tempfile.mkdtemp(prefix="muse_embed_clone_")
    old_cwd = os.getcwd()
    with _suppress_stdout():
        try:
            os.chdir(output_dir)
            generate_audio(
                text=text,
                model="mlx-community/IndexTTS-1.5",
                ref_audio=ref_audio,
                audio_format="wav",
                max_tokens=5000,
            )
        finally:
            os.chdir(old_cwd)

    chunk_paths = sorted(_glob.glob(os.path.join(output_dir, "audio_*.wav")))
    if not chunk_paths:
        log(f"MUSE TTS Embed: no clone audio files found in {output_dir}")
        return None

    audio_chunks = []
    sample_rate = 24000
    for p in chunk_paths:
        chunk_data, sr = sf.read(p)
        audio_chunks.append(chunk_data)
        sample_rate = sr

    full_audio = np.concatenate(audio_chunks)
    log(f"MUSE TTS Embed clone: concatenated {len(chunk_paths)} chunks, {len(full_audio)/sample_rate:.1f}s total")

    buf = io.BytesIO()
    sf.write(buf, full_audio, sample_rate, format="WAV")

    for p in chunk_paths:
        try:
            os.unlink(p)
        except OSError:
            pass
    try:
        os.rmdir(output_dir)
    except OSError:
        pass

    return buf.getvalue()


_chatterbox_model = None


def _get_chatterbox_model():
    global _chatterbox_model
    if _chatterbox_model is None:
        from chatterbox.tts import ChatterboxTTS
        _chatterbox_model = ChatterboxTTS.from_pretrained(device="cpu")
    return _chatterbox_model


def _generate_chatterbox_pytorch_bytes(text: str, ref_audio: str) -> bytes | None:
    """Generate cloned speech via Chatterbox OG PyTorch, return WAV bytes."""
    import torch
    import torchaudio

    with _suppress_stdout():
        device = "cuda" if torch.cuda.is_available() else "cpu"
        cb_model = _get_chatterbox_model()
        wav = cb_model.generate(text, audio_prompt_path=ref_audio)

    buf = io.BytesIO()
    torchaudio.save(buf, wav, cb_model.sr, format="wav")
    return buf.getvalue()


def generate_clone_wav_bytes(text: str, ref_audio: str) -> bytes | None:
    """Generate cloned speech and return WAV bytes. Routes to best available engine."""
    clone_eng = detect_clone_engine()
    if clone_eng == "none":
        return None

    if not os.path.isfile(ref_audio):
        log(f"MUSE TTS Embed: reference audio not found: {ref_audio}")
        return None

    try:
        if clone_eng == "indextts":
            return _generate_indextts_bytes(text, ref_audio)
        else:
            return _generate_chatterbox_pytorch_bytes(text, ref_audio)
    except Exception as e:
        log(f"MUSE TTS Embed clone error: {e}")
        return None


def wav_to_mp3(wav_bytes: bytes) -> bytes:
    """Convert WAV bytes to MP3 for smaller payloads."""
    import soundfile as sf

    wav_buf = io.BytesIO(wav_bytes)
    audio_data, sr = sf.read(wav_buf)
    mp3_buf = io.BytesIO()
    sf.write(mp3_buf, audio_data, sr, format="MP3")
    return mp3_buf.getvalue()


# ============================================
# INPUT VALIDATION
# ============================================

def validate_voice(voice: str) -> str | None:
    """Validate voice ID. Returns error message or None if valid."""
    if not voice:
        return None
    # Only allow alphanumeric + underscore (voice IDs are like af_bella)
    if not re.match(r'^[a-z][a-z]_[a-z]+$', voice):
        return f"Invalid voice format: '{voice}'"
    if voice not in ALL_VOICE_IDS:
        return f"Unknown voice '{voice}'. Use muse_embed_check to see available voices."
    return None


def sanitize_filename(name: str) -> str:
    """Strip filename to safe characters only."""
    return re.sub(r'[^a-zA-Z0-9_-]', '', name)


# ============================================
# MCP SERVER + MCP APPS
# ============================================

mcp = FastMCP("muse-tts-embed")

VIEW_URI = "ui://muse-tts-embed/player"


def _load_player_html() -> str:
    """Load player HTML — from adjacent file in dev, or embedded constant for shipping."""
    # Dev mode: load from file if it exists alongside server.py
    html_path = pathlib.Path(__file__).parent / "player.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    # Shipping mode: return embedded constant (populated at build time)
    return _EMBEDDED_PLAYER_HTML


_EMBEDDED_PLAYER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<style>
/* ============================================
   MUSE TTS Embed — Player
   Design: Champagne Gold / Cyberpunk-Warm
   ============================================ */

:root {
  --bg-void: #08080a;
  --bg-deep: #0b0b0e;
  --bg-surface: #131316;
  --bg-elevated: #1a1a1e;
  --bg-overlay: #222226;

  --gold-50: rgba(201, 184, 150, 0.05);
  --gold-100: rgba(201, 184, 150, 0.10);
  --gold-150: rgba(201, 184, 150, 0.15);
  --gold-200: rgba(201, 184, 150, 0.20);
  --gold-300: rgba(201, 184, 150, 0.30);
  --gold-400: rgba(201, 184, 150, 0.40);
  --gold-500: #c9b896;
  --gold-600: #bdac8a;
  --gold-700: #b1a07e;

  --state-speak: #7eb88c;
  --state-speak-glow: rgba(126, 184, 140, 0.3);
  --state-speak-bg: rgba(126, 184, 140, 0.08);

  --error: #c45a5a;

  --text-primary: #f0eee9;
  --text-secondary: #8a877f;
  --text-muted: #5e5c58;
  --text-ghost: #3e3c39;

  --border-default: rgba(138, 135, 127, 0.10);
  --border-hover: rgba(201, 184, 150, 0.20);
  --border-active: rgba(201, 184, 150, 0.40);

  --radius-sm: 4px;
  --radius-md: 6px;
  --radius-lg: 8px;
  --radius-full: 9999px;

  --glow-gold-sm: 0 0 12px var(--gold-200);
  --glow-speak: 0 0 24px var(--state-speak-glow);

  --font-display: 'Space Grotesk', system-ui, -apple-system, sans-serif;
  --font-mono: 'JetBrains Mono', 'Fira Code', 'SF Mono', monospace;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  background: transparent;
  font-family: var(--font-display);
  color: var(--text-primary);
  -webkit-font-smoothing: antialiased;
}

/* ---- Player Container ---- */
.player {
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  padding: 14px 18px;
  position: relative;
  max-width: 480px;
  transition: border-color 300ms ease, box-shadow 300ms ease;
  animation: player-enter 300ms cubic-bezier(0.4, 0, 0.2, 1);
}

.player:hover {
  border-color: var(--border-hover);
}

.player.playing {
  border-color: rgba(126, 184, 140, 0.15);
}

/* Corner brackets */
.player::before, .player::after {
  content: '';
  position: absolute;
  width: 14px;
  height: 14px;
  border-color: var(--gold-200);
  border-style: solid;
  pointer-events: none;
  transition: border-color 300ms ease;
}
.player::before { top: -1px; left: -1px; border-width: 1px 0 0 1px; }
.player::after  { top: -1px; right: -1px; border-width: 1px 1px 0 0; }

.player-inner { position: relative; }
.player-inner::before, .player-inner::after {
  content: '';
  position: absolute;
  width: 14px;
  height: 14px;
  border-color: var(--gold-200);
  border-style: solid;
  pointer-events: none;
  transition: border-color 300ms ease;
}
.player-inner::before { bottom: -14px; left: -18px; border-width: 0 0 1px 1px; }
.player-inner::after  { bottom: -14px; right: -18px; border-width: 0 1px 1px 0; }

.player:hover::before, .player:hover::after,
.player:hover .player-inner::before, .player:hover .player-inner::after {
  border-color: var(--gold-300);
}

.player.playing::before, .player.playing::after,
.player.playing .player-inner::before, .player.playing .player-inner::after {
  border-color: rgba(126, 184, 140, 0.25);
}

@keyframes player-enter {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* ---- Controls Row ---- */
.controls {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}

/* Play/Pause button */
.btn-play {
  width: 38px;
  height: 38px;
  border-radius: var(--radius-full);
  background: var(--bg-elevated);
  border: 1.5px solid var(--gold-300);
  color: var(--gold-500);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 200ms cubic-bezier(0.34, 1.56, 0.64, 1);
  flex-shrink: 0;
  outline: none;
}

.btn-play:hover {
  transform: scale(1.08);
  box-shadow: var(--glow-gold-sm);
  border-color: var(--gold-500);
}

.btn-play:active { transform: scale(0.95); }

.btn-play.playing {
  border-color: var(--state-speak);
  color: var(--state-speak);
  box-shadow: var(--glow-speak);
}

.btn-play svg { width: 16px; height: 16px; }

/* Progress bar */
.progress-wrap {
  flex: 1;
  height: 6px;
  background: var(--bg-deep);
  border-radius: 3px;
  cursor: pointer;
  position: relative;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--gold-700), var(--gold-500));
  border-radius: 3px;
  width: 0%;
  transition: none;
}

.player.playing .progress-fill {
  background: linear-gradient(90deg, #5a8a6a, var(--state-speak));
}

/* Time display */
.time {
  font-family: var(--font-mono);
  font-size: 0.7rem;
  color: var(--text-muted);
  white-space: nowrap;
  min-width: 76px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.02em;
}

/* ---- Meta Row ---- */
.meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.brand {
  font-size: 0.6rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.15em;
  color: var(--text-ghost);
  user-select: none;
}

/* Voice selector dropdown */
.voice-select {
  appearance: none;
  -webkit-appearance: none;
  background: var(--gold-50);
  border: 1px solid var(--gold-100);
  border-radius: var(--radius-sm);
  padding: 2px 20px 2px 7px;
  font-size: 0.65rem;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  letter-spacing: 0.02em;
  cursor: pointer;
  outline: none;
  transition: all 150ms ease;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' fill='none' stroke='%235e5c58' stroke-width='1.2'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 5px center;
  max-width: 120px;
}

.voice-select:hover {
  border-color: var(--border-hover);
  color: var(--text-primary);
}

.voice-select:focus {
  border-color: var(--border-active);
}

.voice-select option {
  background: var(--bg-elevated);
  color: var(--text-primary);
}

.voice-select optgroup {
  color: var(--gold-500);
  font-style: normal;
  font-weight: 600;
}

.btn-speed {
  padding: 2px 6px;
  background: transparent;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  font-size: 0.65rem;
  color: var(--text-secondary);
  cursor: pointer;
  font-family: var(--font-mono);
  transition: all 150ms ease;
  outline: none;
}

.btn-speed:hover {
  border-color: var(--border-hover);
  color: var(--text-primary);
}

.btn-icon {
  width: 28px;
  height: 28px;
  margin-left: auto;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  color: var(--text-muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 150ms ease;
  outline: none;
}

.btn-icon:hover {
  color: var(--text-primary);
  background: rgba(255, 255, 255, 0.04);
  border-color: var(--border-default);
}

.btn-icon svg { width: 15px; height: 15px; }

/* ---- Loading State ---- */
.state-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 14px;
  color: var(--text-muted);
  font-size: 0.8rem;
}

.dot {
  width: 5px;
  height: 5px;
  background: var(--gold-500);
  border-radius: var(--radius-full);
  animation: dot-breathe 1.4s ease-in-out infinite;
}
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }

@keyframes dot-breathe {
  0%, 100% { opacity: 0.2; transform: scale(0.8); }
  50%      { opacity: 1;   transform: scale(1); }
}

/* ---- Error State ---- */
.state-error {
  color: var(--error);
  font-size: 0.8rem;
  padding: 10px 0;
}

/* ---- Click-to-play hint ---- */
.click-hint {
  display: none;
  font-size: 0.65rem;
  color: var(--text-ghost);
  text-align: center;
  margin-top: 6px;
  animation: hint-fade 2s ease-in-out infinite;
}

.click-hint.visible { display: block; }

@keyframes hint-fade {
  0%, 100% { opacity: 0.4; }
  50%      { opacity: 0.8; }
}

/* ---- Regenerating overlay ---- */
.regen-overlay {
  display: none;
  position: absolute;
  inset: 0;
  background: rgba(19, 19, 22, 0.85);
  border-radius: var(--radius-lg);
  z-index: 10;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--text-muted);
  font-size: 0.8rem;
}

.regen-overlay.visible {
  display: flex;
}

/* ---- Reduced Motion ---- */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 150ms !important;
  }
}

/* ---- Hidden utility ---- */
.hidden { display: none !important; }
</style>
</head>
<body>

<div class="player" id="player">
  <div class="player-inner">
    <!-- Loading -->
    <div class="state-loading" id="state-loading">
      <div class="dot"></div>
      <div class="dot"></div>
      <div class="dot"></div>
      <span>Generating...</span>
    </div>

    <!-- Ready -->
    <div id="state-ready" class="hidden">
      <div class="controls">
        <button class="btn-play" id="btn-play" aria-label="Play">
          <svg id="icon-play" viewBox="0 0 24 24" fill="currentColor" stroke="none">
            <polygon points="6,3 20,12 6,21"/>
          </svg>
          <svg id="icon-pause" viewBox="0 0 24 24" fill="currentColor" stroke="none" class="hidden">
            <rect x="5" y="3" width="4" height="18" rx="1"/>
            <rect x="15" y="3" width="4" height="18" rx="1"/>
          </svg>
        </button>
        <div class="progress-wrap" id="progress-bar">
          <div class="progress-fill" id="progress-fill"></div>
        </div>
        <div class="time" id="time-display">0:00 / 0:00</div>
      </div>
      <div class="meta">
        <span class="brand">MUSE</span>
        <select class="voice-select" id="voice-select" aria-label="Voice"></select>
        <button class="btn-speed" id="btn-speed" aria-label="Playback speed">1.0x</button>
        <button class="btn-icon" id="btn-download" aria-label="Download audio">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
        </button>
      </div>
      <div class="click-hint" id="click-hint">click to play</div>
    </div>

    <!-- Error -->
    <div class="state-error hidden" id="state-error"></div>

    <!-- Regenerating overlay -->
    <div class="regen-overlay" id="regen-overlay">
      <div class="dot"></div>
      <div class="dot"></div>
      <div class="dot"></div>
      <span>Regenerating...</span>
    </div>
  </div>
</div>

<script>
/* ============================================
   MUSE TTS Embed — Player Logic
   MCP Apps postMessage client + Web Audio API
   Two-step audio loading (metadata → fetch)
   ============================================ */

(function() {
  'use strict';

  // ---- MCP Apps PostMessage Client ----
  let nextRpcId = 1;
  const pending = new Map();
  let hostOrigin = null;

  function sendToHost(msg) {
    window.parent.postMessage(msg, '*');
  }

  function rpcRequest(method, params) {
    return new Promise(function(resolve, reject) {
      var id = nextRpcId++;
      pending.set(id, { resolve: resolve, reject: reject });
      sendToHost({ jsonrpc: '2.0', id: id, method: method, params: params || {} });
    });
  }

  window.addEventListener('message', function(event) {
    // Track host origin from first message
    if (!hostOrigin && event.origin) {
      hostOrigin = event.origin;
    }

    // Validate origin after first contact
    if (hostOrigin && event.origin !== hostOrigin) return;

    var msg = event.data;
    if (!msg || msg.jsonrpc !== '2.0') return;

    // Response to our request
    if (msg.id != null && pending.has(msg.id)) {
      var p = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) p.reject(msg.error);
      else p.resolve(msg.result);
      return;
    }

    // Notification from host
    if (msg.method) {
      handleNotification(msg.method, msg.params);
    }
  });

  function handleNotification(method, params) {
    if (method === 'ui/notifications/tool-result') {
      onToolResult(params);
    } else if (method === 'ui/notifications/tool-input') {
      onToolInput(params);
    } else if (method === 'ui/resource-teardown') {
      cleanup();
    }
  }

  function callServerTool(name, args) {
    return rpcRequest('tools/call', { name: name, arguments: args });
  }

  // Initialize handshake
  async function initMcpApp() {
    try {
      await rpcRequest('ui/initialize', {
        protocolVersion: '2026-01-26',
        appInfo: { name: 'MUSE TTS Embed', version: '1.0.0' },
        appCapabilities: {},
      });
      sendToHost({
        jsonrpc: '2.0',
        method: 'ui/notifications/initialized',
        params: {},
      });
    } catch (e) {
      // Host may not support full handshake — continue anyway
    }
  }

  // ---- DOM Elements ----
  var elPlayer      = document.getElementById('player');
  var elLoading     = document.getElementById('state-loading');
  var elReady       = document.getElementById('state-ready');
  var elError       = document.getElementById('state-error');
  var elBtnPlay     = document.getElementById('btn-play');
  var elIconPlay    = document.getElementById('icon-play');
  var elIconPause   = document.getElementById('icon-pause');
  var elProgress    = document.getElementById('progress-bar');
  var elFill        = document.getElementById('progress-fill');
  var elTime        = document.getElementById('time-display');
  var elVoiceSelect = document.getElementById('voice-select');
  var elBtnSpeed    = document.getElementById('btn-speed');
  var elBtnDL       = document.getElementById('btn-download');
  var elHint        = document.getElementById('click-hint');
  var elRegen       = document.getElementById('regen-overlay');

  // ---- Audio State ----
  var audioCtx     = null;
  var audioBuffer  = null;
  var sourceNode   = null;
  var startTime    = 0;
  var pauseOffset  = 0;
  var isPlaying    = false;
  var playbackRate = 1.0;
  var rafId        = null;
  var audioB64     = null;
  var currentVoice  = '';
  var currentClone  = '';
  var currentText   = '';
  var currentSpeed  = 1.0;
  var currentFormat = 'wav';
  var voicesLoaded  = false;

  // ---- Speed Presets ----
  var SPEEDS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0];
  var speedIdx = 2;

  // ---- Speak Lock (multi-view coordination) ----
  var VIEW_UUID = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : Math.random().toString(36).slice(2);
  var LOCK_KEY = 'muse-tts-speak-lock';
  var lockInterval = null;

  function acquireLock() {
    try {
      localStorage.setItem(LOCK_KEY, JSON.stringify({ uuid: VIEW_UUID, ts: Date.now() }));
    } catch(e) {}
  }

  function releaseLock() {
    try {
      var lock = JSON.parse(localStorage.getItem(LOCK_KEY) || '{}');
      if (lock.uuid === VIEW_UUID) localStorage.removeItem(LOCK_KEY);
    } catch(e) {}
  }

  function startLockMonitor() {
    lockInterval = setInterval(function() {
      if (!isPlaying) return;
      try {
        var lock = JSON.parse(localStorage.getItem(LOCK_KEY) || '{}');
        if (lock.uuid && lock.uuid !== VIEW_UUID && lock.ts > Date.now() - 500) {
          pause();
        }
      } catch(e) {}
    }, 200);
  }

  // ---- Web Audio API ----

  function initAudioCtx() {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    return audioCtx;
  }

  async function decodeAudio(b64) {
    var binary = atob(b64);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    var ctx = initAudioCtx();
    return await ctx.decodeAudioData(bytes.buffer.slice(0));
  }

  function play() {
    if (!audioBuffer) return;

    var ctx = initAudioCtx();

    // Resume if suspended (autoplay policy)
    if (ctx.state === 'suspended') {
      ctx.resume();
    }

    // Stop existing source
    if (sourceNode) {
      try { sourceNode.stop(); } catch(e) {}
      sourceNode.disconnect();
    }

    sourceNode = ctx.createBufferSource();
    sourceNode.buffer = audioBuffer;
    sourceNode.playbackRate.value = playbackRate;
    sourceNode.connect(ctx.destination);

    sourceNode.onended = function() {
      if (isPlaying) {
        isPlaying = false;
        pauseOffset = 0;
        updatePlayUI();
        releaseLock();
      }
    };

    startTime = ctx.currentTime - (pauseOffset / playbackRate);
    sourceNode.start(0, pauseOffset);
    isPlaying = true;

    acquireLock();
    updatePlayUI();
    startProgressLoop();
  }

  function pause() {
    if (!isPlaying || !sourceNode) return;

    var ctx = initAudioCtx();
    pauseOffset = (ctx.currentTime - startTime) * playbackRate;
    // Clamp to prevent overshoot
    if (audioBuffer) pauseOffset = Math.min(pauseOffset, audioBuffer.duration);

    try { sourceNode.stop(); } catch(e) {}
    sourceNode.disconnect();
    sourceNode = null;
    isPlaying = false;

    releaseLock();
    updatePlayUI();
  }

  function togglePlay() {
    if (isPlaying) pause();
    else play();
  }

  function seek(fraction) {
    if (!audioBuffer) return;
    fraction = Math.max(0, Math.min(1, fraction));
    pauseOffset = fraction * audioBuffer.duration;
    if (isPlaying) {
      play();
    } else {
      updateTimeDisplay();
      elFill.style.width = (fraction * 100) + '%';
    }
  }

  // ---- Speed Change (server-side re-synthesis) ----

  async function onSpeedChange(newSpeed) {
    if (!currentText) return;

    // Speed doesn't apply to clones — skip regeneration
    if (currentClone) {
      // Revert button text
      speedIdx = SPEEDS.indexOf(currentSpeed);
      if (speedIdx < 0) speedIdx = 2;
      elBtnSpeed.textContent = currentSpeed + 'x';
      return;
    }

    // Stop current playback
    if (isPlaying) pause();

    // Show regenerating overlay
    elRegen.classList.add('visible');

    var prevSpeed = currentSpeed;

    try {
      var result = await callServerTool('muse_regenerate', {
        text: currentText,
        voice: currentVoice,
        speed: newSpeed,
      });

      var content = result && result.content;
      var textItem = Array.isArray(content) ? content.find(function(c) { return c.type === 'text'; }) : null;
      if (!textItem) throw new Error('No response');

      var data = JSON.parse(textItem.text);
      if (data.error) throw new Error(data.error);

      audioB64 = data.audio;
      currentVoice = data.voice;
      currentSpeed = data.speed;
      currentFormat = data.format || 'wav';
      audioBuffer = await decodeAudio(data.audio);

      // Update button + index to match actual speed
      speedIdx = SPEEDS.indexOf(currentSpeed);
      if (speedIdx < 0) speedIdx = 2;
      elBtnSpeed.textContent = currentSpeed + 'x';

      pauseOffset = 0;
      updateTimeDisplay();
      elFill.style.width = '0%';

      // Try autoplay the regenerated audio
      tryAutoplay();
    } catch(e) {
      // Revert button text to previous speed
      currentSpeed = prevSpeed;
      speedIdx = SPEEDS.indexOf(prevSpeed);
      if (speedIdx < 0) speedIdx = 2;
      elBtnSpeed.textContent = prevSpeed + 'x';
    }

    elRegen.classList.remove('visible');
  }

  // ---- Progress Loop ----

  function startProgressLoop() {
    cancelAnimationFrame(rafId);
    function tick() {
      if (!isPlaying || !audioBuffer) return;
      var ctx = initAudioCtx();
      var elapsed = (ctx.currentTime - startTime) * playbackRate;
      var dur = audioBuffer.duration;
      var pct = Math.min(elapsed / dur, 1);

      elFill.style.width = (pct * 100) + '%';
      elTime.textContent = fmtTime(elapsed) + ' / ' + fmtTime(dur);

      if (pct < 1) rafId = requestAnimationFrame(tick);
    }
    rafId = requestAnimationFrame(tick);
  }

  function fmtTime(s) {
    if (!isFinite(s) || s < 0) s = 0;
    var m = Math.floor(s / 60);
    var sec = Math.floor(s % 60);
    return m + ':' + String(sec).padStart(2, '0');
  }

  function updateTimeDisplay() {
    if (!audioBuffer) return;
    elTime.textContent = fmtTime(pauseOffset) + ' / ' + fmtTime(audioBuffer.duration);
  }

  // ---- UI Updates ----

  function updatePlayUI() {
    if (isPlaying) {
      elBtnPlay.classList.add('playing');
      elPlayer.classList.add('playing');
      elIconPlay.classList.add('hidden');
      elIconPause.classList.remove('hidden');
    } else {
      elBtnPlay.classList.remove('playing');
      elPlayer.classList.remove('playing');
      elIconPlay.classList.remove('hidden');
      elIconPause.classList.add('hidden');
    }
    if (!isPlaying) updateTimeDisplay();
  }

  function showState(state) {
    elLoading.classList.toggle('hidden', state !== 'loading');
    elReady.classList.toggle('hidden', state !== 'ready');
    elError.classList.toggle('hidden', state !== 'error');
  }

  function showError(msg) {
    elError.textContent = msg;
    showState('error');
  }

  // ---- Voice Selector ----

  async function loadVoices() {
    if (voicesLoaded) return;
    try {
      var result = await callServerTool('muse_list_voices_embed', {});
      var content = result && result.content;
      if (!content) return;
      var textItem = Array.isArray(content) ? content.find(function(c) { return c.type === 'text'; }) : null;
      if (!textItem) return;
      var data = JSON.parse(textItem.text);

      // Clear existing
      elVoiceSelect.innerHTML = '';

      // Voice Clones optgroup (if any)
      var clones = data.clones || {};
      var cloneIds = Object.keys(clones);
      if (cloneIds.length > 0) {
        var cloneGroup = document.createElement('optgroup');
        cloneGroup.label = 'Voice Clones (~7s)';
        for (var c = 0; c < cloneIds.length; c++) {
          var opt = document.createElement('option');
          opt.value = 'clone:' + cloneIds[c];
          opt.textContent = clones[cloneIds[c]];
          if (currentClone === cloneIds[c]) opt.selected = true;
          cloneGroup.appendChild(opt);
        }
        elVoiceSelect.appendChild(cloneGroup);
      }

      // Preset voice optgroups
      var presets = data.presets || data;
      var groupNames = Object.keys(presets);
      for (var g = 0; g < groupNames.length; g++) {
        var groupName = groupNames[g];
        var voices = presets[groupName];
        var optgroup = document.createElement('optgroup');
        optgroup.label = groupName;
        for (var v = 0; v < voices.length; v++) {
          var opt = document.createElement('option');
          opt.value = voices[v][0];
          opt.textContent = voices[v][1] + ' (' + voices[v][0] + ')';
          if (!currentClone && voices[v][0] === currentVoice) opt.selected = true;
          optgroup.appendChild(opt);
        }
        elVoiceSelect.appendChild(optgroup);
      }
      voicesLoaded = true;
    } catch(e) {
      // Voice list failed — just show current voice as only option
      var fallback = document.createElement('option');
      fallback.value = currentClone ? ('clone:' + currentClone) : currentVoice;
      fallback.textContent = currentClone || currentVoice;
      fallback.selected = true;
      elVoiceSelect.appendChild(fallback);
    }
  }

  async function onVoiceChange() {
    var newValue = elVoiceSelect.value;
    if (!newValue || !currentText) return;

    var isClone = newValue.indexOf('clone:') === 0;
    var cloneId = isClone ? newValue.substring(6) : '';
    var voiceId = isClone ? '' : newValue;

    // Skip if same as current
    if (isClone && cloneId === currentClone) return;
    if (!isClone && voiceId === currentVoice && !currentClone) return;

    // Stop current playback
    if (isPlaying) pause();

    // Show regenerating overlay
    elRegen.classList.add('visible');

    try {
      var args = { text: currentText };
      if (isClone) {
        args.clone = cloneId;
      } else {
        args.voice = voiceId;
        args.speed = currentSpeed;
      }

      var result = await callServerTool('muse_regenerate', args);

      var content = result && result.content;
      var textItem = Array.isArray(content) ? content.find(function(c) { return c.type === 'text'; }) : null;
      if (!textItem) throw new Error('No response');

      var data = JSON.parse(textItem.text);
      if (data.error) throw new Error(data.error);

      audioB64 = data.audio;
      currentVoice = data.voice || '';
      currentClone = data.clone || '';
      currentSpeed = data.speed || 1.0;
      currentFormat = data.format || 'wav';
      audioBuffer = await decodeAudio(data.audio);

      pauseOffset = 0;
      updateTimeDisplay();
      elFill.style.width = '0%';

      tryAutoplay();
    } catch(e) {
      // Revert select to current
      if (currentClone) {
        elVoiceSelect.value = 'clone:' + currentClone;
      } else {
        elVoiceSelect.value = currentVoice;
      }
    }

    elRegen.classList.remove('visible');
  }

  // ---- Tool Result Handler (structuredContent delivers audio directly) ----

  function onToolResult(params) {
    try {
      // structuredContent is the primary path — audio comes here directly,
      // bypassing model context (no size limit)
      var data = params.structuredContent || null;

      // Fallback: try to parse from text content (for error messages)
      if (!data) {
        var content = params.content || (params.result && params.result.content) || [];
        var textItem = Array.isArray(content)
          ? content.find(function(c) { return c.type === 'text'; })
          : null;

        if (textItem) {
          // Check if it's an error message
          var txt = textItem.text || '';
          if (txt.indexOf('Error:') === 0) {
            showError(txt);
            return;
          }
          // Try parsing as JSON (legacy/fallback)
          try {
            data = JSON.parse(txt);
          } catch(e) {
            // Not JSON — just a status message from model
            showError('No audio data received.');
            return;
          }
        } else {
          showError('No audio data received.');
          return;
        }
      }

      if (data.error) {
        showError(data.error);
        return;
      }

      if (!data.audio) {
        showError('No audio in response.');
        return;
      }

      loadAudio(data);
    } catch(e) {
      showError('Failed to load audio: ' + e.message);
    }
  }

  function onToolInput(params) {
    // Tool input received — audio is being generated
    showState('loading');
  }

  async function loadAudio(data) {
    try {
      audioB64 = data.audio;
      currentVoice = data.voice || '';
      currentClone = data.clone || '';
      currentText = data.text || '';
      currentSpeed = data.speed || 1.0;
      currentFormat = data.format || 'wav';

      audioBuffer = await decodeAudio(data.audio);

      var spd = data.speed || 1.0;
      speedIdx = SPEEDS.indexOf(spd);
      if (speedIdx < 0) speedIdx = 2;
      playbackRate = 1.0;  // Always 1.0 — Kokoro bakes speed into the audio
      elBtnSpeed.textContent = spd + 'x';

      pauseOffset = 0;
      showState('ready');
      updateTimeDisplay();

      // Load voice selector in background
      loadVoices();

      // Only autoplay if freshly generated — not on replay (refresh/scroll back).
      // Timestamp check is immune to iframe sandbox storage issues.
      var shouldAutoplay = false;
      if (data.generated_at) {
        var ageSeconds = (Date.now() / 1000) - data.generated_at;
        shouldAutoplay = ageSeconds < 30;
      }
      if (shouldAutoplay) {
        tryAutoplay();
      }
    } catch(e) {
      showError('Failed to decode audio.');
    }
  }

  async function tryAutoplay() {
    var ctx = initAudioCtx();
    try {
      await ctx.resume();
      if (ctx.state === 'running') {
        play();
      } else {
        showClickHint();
      }
    } catch(e) {
      showClickHint();
    }
  }

  function showClickHint() {
    elHint.classList.add('visible');
  }

  // ---- Event Listeners ----

  // Play/Pause
  elBtnPlay.addEventListener('click', function() {
    elHint.classList.remove('visible');
    togglePlay();
  });

  // Progress bar seek
  elProgress.addEventListener('click', function(e) {
    var rect = elProgress.getBoundingClientRect();
    seek((e.clientX - rect.left) / rect.width);
  });

  // Speed cycle — regenerates via server (Kokoro synthesizes at native speed)
  elBtnSpeed.addEventListener('click', function() {
    speedIdx = (speedIdx + 1) % SPEEDS.length;
    var newSpeed = SPEEDS[speedIdx];
    elBtnSpeed.textContent = newSpeed + 'x';  // Optimistic UI update
    onSpeedChange(newSpeed);
  });

  // Voice change
  elVoiceSelect.addEventListener('change', onVoiceChange);

  // Download — saves via server to ~/Downloads/ (iframe sandbox blocks Blob URLs)
  elBtnDL.addEventListener('click', async function() {
    if (!audioB64) return;

    var ext = currentFormat === 'mp3' ? 'mp3' : 'wav';
    var safeName = (currentClone || currentVoice || 'muse').replace(/[^a-zA-Z0-9_-]/g, '');
    var filename = 'muse-' + safeName + '-' + Date.now() + '.' + ext;

    try {
      var result = await callServerTool('muse_save_audio', {
        audio: audioB64,
        filename: filename,
        format: currentFormat,
      });

      var content = result && result.content;
      var textItem = Array.isArray(content) ? content.find(function(c) { return c.type === 'text'; }) : null;
      if (textItem) {
        var data = JSON.parse(textItem.text);
        if (data.error) throw new Error(data.error);
      }

      // Flash green on success
      elBtnDL.style.color = 'var(--state-speak)';
      setTimeout(function() { elBtnDL.style.color = ''; }, 1500);
    } catch(e) {
      // Flash red on failure
      elBtnDL.style.color = 'var(--error)';
      setTimeout(function() { elBtnDL.style.color = ''; }, 1500);
    }
  });

  // First click anywhere resumes AudioContext (for Web/Mobile)
  elPlayer.addEventListener('click', function() {
    if (audioCtx && audioCtx.state === 'suspended') {
      audioCtx.resume().then(function() {
        elHint.classList.remove('visible');
        if (!isPlaying && audioBuffer) {
          play();
        }
      });
    }
  }, { once: true });

  // ---- Cleanup ----

  function cleanup() {
    if (sourceNode) {
      try { sourceNode.stop(); } catch(e) {}
      sourceNode.disconnect();
    }
    if (audioCtx) {
      audioCtx.close().catch(function(){});
    }
    if (rafId) cancelAnimationFrame(rafId);
    if (lockInterval) clearInterval(lockInterval);
    releaseLock();
  }

  // ---- Init ----
  initMcpApp();
  startLockMonitor();

})();
</script>
</body>
</html>"""


@mcp.resource(VIEW_URI, mime_type="text/html;profile=mcp-app")
def player_view() -> str:
    """Return the embedded player HTML."""
    return _load_player_html()


@mcp.tool(
    meta={
        "ui": {"resourceUri": VIEW_URI},
    }
)
def muse_speak_embed(text: str, voice: str = "", clone: str = "", ref_audio: str = "", speed: float = 0) -> types.CallToolResult:
    """
    Speak text with an embedded audio player in the chat.

    Two modes:
    - Preset voices (Kokoro, ~1s): use voice="am_onyx" etc.
    - Voice cloning (~7s): use clone="my_voice" or ref_audio="/path/to/ref.wav"

    Args:
        text: The text to speak (recommended max ~1500 characters)
        voice: Kokoro voice ID (e.g. "am_onyx", "af_bella")
        clone: Name of a bundled voice clone (add WAVs to the voices/ directory)
        ref_audio: Path to a custom reference WAV for voice cloning
        speed: Speed multiplier for presets (0.5 to 2.0). Not used for clones.
    """
    # Input validation
    if not text or not text.strip():
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="Error: No text provided.")],
            isError=True,
        )

    if len(text) > MAX_TEXT_LENGTH:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Error: Text too long ({len(text)} chars). Maximum is {MAX_TEXT_LENGTH}.")],
            isError=True,
        )

    # Priority: ref_audio > clone > voice
    if ref_audio:
        try:
            ref_audio = _validate_ref_audio(ref_audio)
        except ValueError:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="Error: Reference audio not found. Check the file path and try again.")],
                isError=True,
            )
        if not os.path.isfile(ref_audio):
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="Error: Reference audio not found. Check the file path and try again.")],
                isError=True,
            )
        log(f"MUSE TTS Embed: cloning from custom ref '{text[:60]}...'")
        wav_bytes = generate_clone_wav_bytes(text, ref_audio)
        if wav_bytes is None:
            clone_eng = detect_clone_engine()
            if clone_eng == "none":
                error_msg = "Voice cloning not available. Install mlx_audio (Mac) or chatterbox-tts."
            else:
                error_msg = "Clone generation failed."
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Error: {error_msg}")],
                isError=True,
            )
        clone_eng = detect_clone_engine()
        engine_name = "IndexTTS-1.5" if clone_eng == "indextts" else "Chatterbox"
        label = f"[MUSE clone · custom ref · {engine_name}]"
        structured_extra = {"clone": "custom_ref"}
    elif clone:
        clone_key = clone.lower().replace(" ", "_")
        if clone_key not in CLONE_VOICES:
            available = ", ".join(sorted(CLONE_VOICES.keys()))
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Error: Unknown clone '{clone}'. Available: {available}")],
                isError=True,
            )
        display = CLONE_DISPLAY_NAMES.get(clone_key, clone_key)
        log(f"MUSE TTS Embed: cloning as {display} '{text[:60]}...'")
        wav_bytes = generate_clone_wav_bytes(text, CLONE_VOICES[clone_key])
        if wav_bytes is None:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="Error: Clone generation failed.")],
                isError=True,
            )
        clone_eng = detect_clone_engine()
        engine_name = "IndexTTS-1.5" if clone_eng == "indextts" else "Chatterbox"
        label = f"[MUSE clone · {display} · {engine_name}]"
        structured_extra = {"clone": clone_key}
    else:
        # Default: Kokoro preset voice
        voice = voice or KOKORO_VOICE
        speed = speed or KOKORO_SPEED

        voice_error = validate_voice(voice)
        if voice_error:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Error: {voice_error}")],
                isError=True,
            )

        speed = max(0.5, min(2.0, speed))

        log(f"MUSE TTS Embed: generating '{text[:60]}...' voice={voice} speed={speed}")
        wav_bytes = generate_wav_bytes(text, voice, speed)

        if wav_bytes is None:
            if _HTTP_MODE:
                error_msg = "Audio generation failed."
            else:
                engine = detect_engine()
                if engine == "none":
                    error_msg = "No TTS engine found. Install mlx_audio (Mac) or kokoro (any platform)."
                else:
                    error_msg = "Audio generation failed."
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Error: {error_msg}")],
                isError=True,
            )
        label = f"[MUSE voice · {voice} · {speed}x]"
        structured_extra = {}

    # Compress WAV → MP3 for smaller structuredContent payload (~10-15x smaller)
    audio_bytes, audio_format = _compress_audio(wav_bytes)

    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
    log(f"MUSE TTS Embed: {len(wav_bytes)} WAV → {len(audio_bytes)} {audio_format} ({len(audio_b64)} b64 chars)")

    structured = {
        "audio": audio_b64,
        "voice": voice if not (clone or ref_audio) else "",
        "speed": speed if not (clone or ref_audio) else 1.0,
        "text": text,
        "format": audio_format,
        "sample_rate": 24000,
        "generated_at": time.time(),
        "call_id": str(uuid.uuid4()),
    }
    structured.update(structured_extra)

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=f"{label}\n\n{text}",
            ),
        ],
        structuredContent=structured,
    )


@mcp.tool(
    meta={
        "ui": {"visibility": ["app"]},
    }
)
def muse_list_voices_embed() -> list[types.TextContent]:
    """List available voices and clones for the player voice picker."""
    result = {
        "presets": VOICES,
    }
    if CLONE_VOICES:
        result["clones"] = {
            clone_id: CLONE_DISPLAY_NAMES.get(clone_id, clone_id)
            for clone_id in sorted(CLONE_VOICES.keys())
        }
    return [types.TextContent(type="text", text=json.dumps(result))]


@mcp.tool(
    meta={
        "ui": {"visibility": ["app"]},
    }
)
def muse_regenerate(text: str, voice: str = "", clone: str = "", speed: float = 0) -> list[types.TextContent]:
    """Regenerate audio with different voice, clone, or speed settings."""
    if not text or not text.strip():
        return [types.TextContent(type="text", text=json.dumps({"error": "No text provided."}))]

    if len(text) > MAX_TEXT_LENGTH:
        return [types.TextContent(type="text", text=json.dumps({"error": "Text too long."}))]

    # Clone mode: speed is ignored, route to clone engine
    if clone:
        clone_key = clone.lower().replace(" ", "_")
        if clone_key not in CLONE_VOICES:
            return [types.TextContent(type="text", text=json.dumps({"error": f"Unknown clone '{clone}'."}))]

        wav_bytes = generate_clone_wav_bytes(text, CLONE_VOICES[clone_key])
        if wav_bytes is None:
            return [types.TextContent(type="text", text=json.dumps({"error": "Clone generation failed."}))]

        audio_bytes, audio_format = _compress_audio(wav_bytes)

        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        return [types.TextContent(type="text", text=json.dumps({
            "audio": audio_b64,
            "voice": "",
            "clone": clone_key,
            "speed": 1.0,
            "format": audio_format,
            "sample_rate": 24000,
        }))]

    # Preset mode
    voice = voice or KOKORO_VOICE
    speed = max(0.5, min(2.0, speed or KOKORO_SPEED))

    voice_error = validate_voice(voice)
    if voice_error:
        return [types.TextContent(type="text", text=json.dumps({"error": voice_error}))]

    wav_bytes = generate_wav_bytes(text, voice, speed)
    if wav_bytes is None:
        return [types.TextContent(type="text", text=json.dumps({"error": "Generation failed."}))]

    audio_bytes, audio_format = _compress_audio(wav_bytes)
    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

    return [types.TextContent(type="text", text=json.dumps({
        "audio": audio_b64,
        "voice": voice,
        "speed": speed,
        "format": audio_format,
        "sample_rate": 24000,
    }))]


@mcp.tool(
    meta={
        "ui": {"visibility": ["app"]},
    }
)
def muse_save_audio(audio: str, filename: str, format: str = "wav") -> list[types.TextContent]:
    """Save audio to ~/Downloads/. Used by the player's download button."""
    # Validate filename: strip to safe chars, prevent path traversal
    safe_name = re.sub(r'[^a-zA-Z0-9_.\-]', '', filename)
    if not safe_name or safe_name.startswith('.'):
        return [types.TextContent(type="text", text=json.dumps({"error": "Invalid filename."}))]

    # Enforce allowed extensions
    if format not in ("wav", "mp3"):
        return [types.TextContent(type="text", text=json.dumps({"error": "Invalid format."}))]

    # Ensure extension matches format
    expected_ext = f".{format}"
    if not safe_name.endswith(expected_ext):
        safe_name = safe_name.rsplit('.', 1)[0] + expected_ext

    # Decode audio
    try:
        audio_bytes = base64.b64decode(audio)
    except Exception:
        return [types.TextContent(type="text", text=json.dumps({"error": "Invalid audio data."}))]

    # Sanity check: reject absurdly large payloads (50MB)
    if len(audio_bytes) > 50_000_000:
        return [types.TextContent(type="text", text=json.dumps({"error": "Audio too large."}))]

    # Write to ~/Downloads/, avoiding overwrites
    downloads_dir = pathlib.Path.home() / "Downloads"
    dest = downloads_dir / safe_name
    counter = 1
    stem = dest.stem
    suffix = dest.suffix
    while dest.exists():
        dest = downloads_dir / f"{stem}-{counter}{suffix}"
        counter += 1

    try:
        dest.write_bytes(audio_bytes)
    except Exception:
        return [types.TextContent(type="text", text=json.dumps({"error": "Failed to write file."}))]

    log(f"MUSE TTS Embed: saved {len(audio_bytes)} bytes to {dest}")
    return [types.TextContent(type="text", text=json.dumps({
        "saved": str(dest.name),
        "size": len(audio_bytes),
    }))]


@mcp.tool()
def muse_embed_check() -> dict:
    """
    Check if MUSE TTS Embed is ready.

    Returns status of all engines, platform, and configuration.
    """
    engine = detect_engine()
    clone_eng = detect_clone_engine()
    status = {
        "engine": engine,
        "clone_engine": clone_eng,
        "platform": f"{platform.system()} {platform.machine()}",
        "voice": KOKORO_VOICE,
        "speed": KOKORO_SPEED,
        "max_text_length": MAX_TEXT_LENGTH,
    }

    if engine == "mlx":
        status["status"] = "ready (mlx_audio — Apple Silicon)"
    elif engine == "kokoro":
        status["status"] = "ready (kokoro PyTorch — cross-platform)"
    else:
        status["status"] = "no engine found"
        status["help"] = "Install: pip install mlx_audio (Mac M-series) or pip install kokoro soundfile (any platform)"

    if clone_eng == "indextts":
        status["clone_status"] = "ready (IndexTTS-1.5 — Apple Silicon)"
    elif clone_eng == "chatterbox":
        status["clone_status"] = "ready (Chatterbox OG — PyTorch)"
    else:
        status["clone_status"] = "not available — install mlx_audio or chatterbox-tts"

    status["voices_available"] = len(ALL_VOICE_IDS)
    status["clones_available"] = len(CLONE_VOICES)

    return status


# ============================================
# MAIN
# ============================================

def _print_banner():
    engine = detect_engine()
    clone_eng = detect_clone_engine()
    engine_label = {
        "mlx": "mlx_audio (Apple Silicon)",
        "kokoro": "kokoro PyTorch (cross-platform)",
        "none": "NOT FOUND — install mlx_audio or kokoro",
    }.get(engine, "unknown")
    clone_label = {
        "indextts": "IndexTTS-1.5 (Apple Silicon)",
        "chatterbox": "Chatterbox OG (PyTorch)",
        "none": "NOT FOUND",
    }.get(clone_eng, "unknown")

    log("\n" + "=" * 50)
    log("  MUSE TTS Embed v1.2.0 — Voice + Cloning")
    log("  By The Funkatorium")
    log("=" * 50)
    log(f"\n  Kokoro:   {engine_label}")
    log(f"  Cloning:  {clone_label}")
    log(f"  Platform: {platform.system()} {platform.machine()}")
    log(f"  Voice:    {KOKORO_VOICE}")
    log(f"  Speed:    {KOKORO_SPEED}x")
    log(f"  Presets:  {len(ALL_VOICE_IDS)} voices")
    log(f"  Clones:   {len(CLONE_VOICES)} voices")
    log(f"  Max text: {MAX_TEXT_LENGTH} chars")
    log("\n  Tools:")
    log("    muse_speak_embed  — Speak with embedded player (preset or clone)")
    log("    muse_embed_check  — System status")


if __name__ == "__main__":
    _print_banner()

    if "--http" in sys.argv:
        # HTTP mode for Claude Web/Mobile (via tunnel)
        _HTTP_MODE = True  # noqa: F841 — module-level flag read by tool handlers

        auth_token = os.getenv("MUSE_AUTH_TOKEN", "")
        if not auth_token:
            log("\n  ERROR: HTTP mode requires MUSE_AUTH_TOKEN env var.")
            log("  Set it: export MUSE_AUTH_TOKEN=your-secret-token")
            log("  Then:   python server.py --http")
            sys.exit(1)

        port = int(os.getenv("MUSE_PORT", "3001"))
        log(f"\n  Mode: HTTP (port {port})")
        log("  Auth: Bearer token required")
        log("\n" + "=" * 50 + "\n")

        # HTTP transport with security middleware
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.middleware.cors import CORSMiddleware
        from starlette.responses import JSONResponse, Response

        MAX_BODY_SIZE = 1_000_000  # 1MB request body limit

        # Rate limiter state: token -> list of request timestamps
        _rate_buckets: dict[str, list[float]] = defaultdict(list)
        RATE_LIMIT = 30  # requests per second per token (MCP handshake is bursty)

        def _add_security_headers(response):
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            return response

        class SecurityMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                # --- Request body size limit ---
                content_length = request.headers.get("content-length")
                if content_length and int(content_length) > MAX_BODY_SIZE:
                    return _add_security_headers(
                        JSONResponse({"error": "Request too large"}, status_code=413))

                # --- Auth: Bearer header OR ?token= query param ---
                token = ""
                auth_header = request.headers.get("authorization", "")
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:]
                else:
                    # Claude Web/Mobile sends token as query parameter
                    token = request.query_params.get("token", "")

                if not token or not hmac.compare_digest(token.encode(), auth_token.encode()):
                    return _add_security_headers(
                        JSONResponse({"error": "Unauthorized"}, status_code=401))

                # --- Health check (auth'd) ---
                if request.url.path == "/health":
                    return _add_security_headers(Response(status_code=200))

                # --- Rate limiting ---
                now = time.time()
                bucket = _rate_buckets[token]
                # Prune entries older than 1 second
                _rate_buckets[token] = [t for t in bucket if now - t < 1.0]
                if not _rate_buckets[token]:
                    del _rate_buckets[token]
                if len(_rate_buckets.get(token, [])) >= RATE_LIMIT:
                    return _add_security_headers(
                        JSONResponse({"error": "Rate limit exceeded"}, status_code=429))
                _rate_buckets[token].append(now)

                # --- Process request ---
                response = await call_next(request)
                return _add_security_headers(response)

        import uvicorn

        # Allow tunnel hostname through MCP's DNS rebinding protection
        tunnel_host = os.getenv("MUSE_TUNNEL_HOST", "")
        if tunnel_host:
            mcp.settings.transport_security.allowed_hosts.append(tunnel_host)
            mcp.settings.transport_security.allowed_origins.append(f"https://{tunnel_host}")

        app = mcp.streamable_http_app()
        app.add_middleware(SecurityMiddleware)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://claude.ai"],
            allow_methods=["POST", "OPTIONS", "GET"],
            allow_headers=["Authorization", "Content-Type"],
        )
        uvicorn.run(app, host=os.getenv("MUSE_HOST", "127.0.0.1"), port=port)
    else:
        # Default: stdio mode for Claude Desktop
        log("\n  Mode: stdio")
        log("\n" + "=" * 50 + "\n")
        mcp.run()
