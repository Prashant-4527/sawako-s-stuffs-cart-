<div align="center">

```
╔══════════════════════════════════════════════════════════╗
║           KAORUKO — AI Desktop Voice Assistant           ║
║                  Elite Edition v1.0.0                    ║
╚══════════════════════════════════════════════════════════╝
```

**Kaoruko** is an offline-first, privacy-focused AI voice assistant for Windows.<br>
Wake word → Whisper STT → 3-layer NLU → Action Execution → Edge TTS.

![Python](https://img.shields.io/badge/Python-3.11+-blue) ![PyQt6](https://img.shields.io/badge/UI-PyQt6-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

</div>

---

## Quick Start

```bash
# 1. Clone and setup (Windows)
python scripts/setup_windows.py

# 2. Configure your Anthropic key (optional — works offline too)
python -c "
from kaoruko.security.secrets_manager import SecretsManager
from pathlib import Path
s = SecretsManager(Path('.'))
s.store('anthropic_api_key', 'sk-ant-...')
"

# 3. Run
.venv\Scripts\python main.py
```

**Wake word:** `"Hey Kaoruko"` or `"Kaoruko"`

---

## Architecture

```
[Mic] → [NoiseSuppressor] → [WakeWordDetector]
                                    ↓
                            [Silero VAD records]
                                    ↓
                         [Whisper STT transcribes]
                                    ↓
              ┌─────────────────────────────────────┐
              │          NLU Pipeline               │
              │  Layer 0: Plugin check  (<1ms)      │
              │  Layer 1: Rule engine   (<5ms)      │
              │  Layer 2: Local NLU     (<50ms)     │
              │  Layer 3: Claude/Ollama (<800ms)    │
              └─────────────────────────────────────┘
                                    ↓
                         [Executor dispatches]
                                    ↓
                       [Edge TTS speaks response]
```

All components communicate through the **EventBus** — zero direct coupling.

---

## Build EXE (Windows)

```bat
:: Step 1 — Run setup
python scripts\setup_windows.py

:: Step 2 — Build EXE bundle
BUILD.bat
:: Output: dist\Kaoruko\Kaoruko.exe

:: Step 3 (optional) — Create installer .exe
:: Requires Inno Setup 6: https://jrsoftware.org/isdl.php
BUILD_INSTALLER.bat
:: Output: dist\Kaoruko_Setup_1.0.0.exe
```

The EXE is a **onedir bundle** — a `dist\Kaoruko\` folder containing `Kaoruko.exe`
plus all DLLs and data files. Distribute the entire folder (or use the installer).

---

## Changelog — Elite Upgrade

All bugs from the code review have been fixed:

### 🔴 Critical Fixes

| File | Issue | Fix |
|------|-------|-----|
| `main.py` | `asyncio.get_event_loop()` deprecated in Python 3.10+ | `asyncio.new_event_loop()` + `asyncio.set_event_loop()` |
| `voice/pipeline.py` | `_recording_active` / `_tts_busy` were plain `bool` — not thread-safe across the sounddevice hardware callback thread | Replaced with `threading.Event` — has internal `Lock` + memory barrier |
| `voice/wake_word/detector.py` | `asyncio.get_event_loop()` called from background detection thread | Uses `self._loop` (injected at construction) — no thread-local loop lookup |
| `execution/executor.py` | `asyncio.get_event_loop().run_in_executor()` deprecated in 3.10+ | `asyncio.get_running_loop().run_in_executor()` |

### 🟠 Design Fixes

| File | Issue | Fix |
|------|-------|-----|
| `core/assistant.py` | `_check_plugins()` keyword-map bypassed the NLU pipeline entirely — invisible to metrics/logging | Removed; plugin intents now resolved via **NLU Layer 0** in `IntentClassifier` |
| `intelligence/ai_router.py` | Internet check cached forever — if network dropped mid-session, Claude stayed offline for entire run | Added **30-second TTL cache** + `_invalidate_internet_cache()` on API errors |
| `intelligence/ai_router.py` | `SecretsManager(Path.cwd())` — breaks when launched from shortcut/different CWD | `project_root` injected via constructor; no more `Path.cwd()` |
| `intelligence/ai_router.py` | No retry on Claude API calls — a single 429 fell through to Ollama | **Exponential backoff** retry (3 attempts, `0.5s / 1s / 2s` delays) for 429/5xx |
| `core/assistant.py` | Event subscriptions never unsubscribed at shutdown — ghost callbacks if shutdown called mid-run | `_subscribed_handlers` list tracks all subs; `shutdown()` unsubscribes all |
| `plugins/plugin_base.py` | `handle_intent()` was synchronous — I/O in plugins would block the event loop | Now `async`; sync overrides wrapped in `run_in_executor` automatically |
| `core/event_bus.py` | `publish_sync()` used `get_event_loop()` unconditionally | Uses `get_running_loop()` when a loop is running; fallback for sync contexts |

### 🟡 Minor Fixes

- `scripts/setup_windows.py` — Desktop shortcut now quotes paths with spaces correctly
- `requirements.txt` — Consolidated; now just `pip install -e .` (pyproject.toml is the source of truth)
- Added `reset_event_bus()` for test isolation
- Added `total_subscriber_count` and `clear_subscriptions()` to EventBus
- Added `get_all_intents()` and `call_handle_intent()` to PluginManager

### ✅ New Test Coverage

| Test File | Covers |
|-----------|--------|
| `test_event_bus.py` | subscribe, publish, unsubscribe, wildcards, once, clear, wait_for, publish_sync |
| `test_executor.py` | sequential/parallel dispatch, confirmation gate, missing handler, response building |
| `test_secrets_manager.py` | encrypt/decrypt round-trip, missing key, delete, shared store, unicode |
| `test_ai_router.py` | TTL cache, retry logic, fallback to Ollama, project_root injection |
| `test_plugin_manager.py` | load, intent dispatch, enable/disable, async handle_intent |
| `test_voice_pipeline_thread_safety.py` | threading.Event cross-thread visibility, concurrent access |

---

## Configuration

Edit `config/kaoruko.yaml`:

```yaml
assistant:
  name: "Kaoruko"
  language: "en"

ai:
  primary: "claude"          # claude | ollama
  model: "claude-sonnet-4-20250514"
  offline_model: "llama3.1"

voice:
  wake_word:
    phrases: ["hey kaoruko", "kaoruko"]
    sensitivity: 0.65
  stt:
    model_size: "base"       # tiny | base | small | medium | large
```

---

## Privacy

- All voice processing happens **locally** (Whisper STT, Silero VAD, OpenWakeWord)
- API calls to Claude only happen for complex NLU — easily disabled
- API keys stored **encrypted** (Fernet + PBKDF2), never in plaintext
- No telemetry, no ads, no cloud dependency for core functionality

---

*MIT License · Made with 💙*
# sawako-stuffs
