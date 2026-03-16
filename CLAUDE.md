# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Seinfeld-style comedy skit generator with AI-driven script writing and synthesized voice acting. It uses Ollama (local LLM) to generate scripts and F5-TTS for voice cloning, then assembles audio with laugh tracks and music beds into MP3 files.

## Running the Applications

```bash
# Main skit creator (PyQt6 GUI)
python skit_creator9i.py

# Generic segment producer (custom speakers)
python skit_creator_x.py

# Audio stem separator (tkinter GUI)
python stempsplitter_pro.py
```

**No build step required.** Dependencies include: `PyQt6`, `pydub`, `ollama`, `f5_tts`, `torch`, `soundfile`, `librosa`, `demucs`.

## External Requirements

- **Ollama** must be running at `http://127.0.0.1:11434` for AI script generation
- **F5-TTS model** is auto-downloaded from Hugging Face on first run
- **Voice files** must exist in `Voices/` — `jerry.wav`, `george.wav`, `elaine.wav`, `kramer.wav`

## Architecture

### Data Flow

```
User topic → Ollama LLM → Script (dialogue + [LAUGH] cues)
    → Parse into (character, line) events
    → F5-TTS inference → per-character WAV files
    → Audio assembly (intro bed + dialogue + laugh tracks + ender)
    → Normalize → Export MP3 → /skits/
```

### Key Files

- **`api.py`** — `F5TTS` class wrapping the F5-TTS model; handles voice inference, reference audio transcription, and WAV export
- **`skit_creator9i.py`** — Main app; 3 tabs: AI Generator (Ollama → script), Manual Script (paste dialogue), Batch From File (.txt/.json input)
- **`skit_creator_x.py`** — Variant supporting 1–4 custom speakers instead of fixed Seinfeld characters
- **`prompt_config.json`** — System prompt defining dialogue format rules (character names, `[LAUGH]` placement, line structure)
- **`skit_creator_config.json`** — Persisted UI state (character speeds, voice seeds, file paths, model selection)
- **`transcript_cache.json`** — Caches voice file transcriptions keyed by file modification time to avoid redundant processing

### Script Format

The LLM must produce dialogue in this exact format (defined in `prompt_config.json`):
```
JERRY: Line of dialogue here.
GEORGE: Another line.
[LAUGH]
ELAINE: Response line.
```

Characters: `JERRY`, `GEORGE`, `ELAINE`, `KRAMER`. Laugh track lines use `[LAUGH]`.

### Audio Assembly

- Laugh tracks in `laughs/` are cycled in order (not random) at each `[LAUGH]` marker
- Intro (`intro bed.wav`) and ender (`ender2.wav`) wrap the dialogue
- Output named `skit_{timestamp}_{batch_num}.mp3` saved to `skits/`

### Archive

`archive/` contains 16+ historical versions of `skit_creator` — useful for understanding feature evolution but not active code.
