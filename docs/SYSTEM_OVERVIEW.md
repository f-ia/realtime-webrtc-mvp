# System Overview — Speaking Practice (English Evaluation)

This document describes the full operation of the Speaking Practice system: backend, evaluation flow, APIs, and frontend.

---

## 1. Architecture

- **Backend:** FastAPI (Python), single process.
- **Frontend:** Static HTML/CSS/JS served from `/public`. No build step.
- **APIs:** OpenAI (Chat Completions with audio, Transcriptions, Speech/TTS). All server-side.

The user records audio in the browser; the server receives it, transcribes and evaluates it (pronunciation, intonation, grammar), and returns feedback plus optional TTS audio.

---

## 2. Main Components

| Component | Role |
|-----------|------|
| `main.py` | FastAPI app: routes, evaluation logic, audio conversion, OpenAI calls. |
| `public/index.html` | Single-page UI: phrase display, record/stop, result card, optional log box. |
| `public/app.js` | State machine, recording (MediaRecorder), API calls, TTS playback. |
| `public/styles.css` | Layout and styling. |
| `.env` | `OPENAI_API_KEY` and optional config (see below). |

---

## 3. Evaluation Flow

Evaluation has two paths: **unified (primary)** and **fallback**.

### 3.1 Unified flow (primary)

1. **Input:** Base64 audio (e.g. webm from browser), phrase id, expected text.
2. **Conversion:** If not WAV, audio is converted to WAV (ffmpeg via imageio-ffmpeg or pydub). Conversion uses direct ffmpeg subprocess when possible (no ffprobe required on Windows).
3. **Single API call:** `gpt-audio` (Chat Completions) receives:
   - Text prompt: instructions to act as an English teacher, transcribe literally, evaluate pronunciation/intonation/grammar, return JSON.
   - `input_audio`: WAV (base64).
4. **Output:** One JSON with `transcript`, `feedback_ui`, `feedback_tts`, `tips`, `scoreAi`.
5. **Transcript:** Must be **literal** (exactly what was heard; no correction). Evaluation and tips are based on this transcript and on the audio (pronunciation/intonation).
6. **Retry:** If the first unified call returns None (e.g. empty content or non-JSON), the server retries the unified flow once before falling back.

**Why unified:** The model hears the real audio, so it can evaluate pronunciation and intonation directly, and produce a literal transcript. No separate STT step that could “correct” the user’s words.

### 3.2 Fallback flow

Used only when the unified flow fails twice (e.g. WAV conversion failure or gpt-audio API error).

1. **Transcription:** Whisper (OpenAI Transcriptions API) with a literal-style prompt. Output may still be normalized by the model.
2. **Feedback:** Chat Completions (e.g. gpt-4o-mini) receives expected text + transcript (no audio), returns `feedback_ui`, `feedback_tts`, `tips`, `scoreAi`.
3. **Limitation:** Fallback does not hear the audio, so it cannot evaluate pronunciation/intonation; only transcript vs expected text.

The response includes `used_fallback: true` so the frontend can show a notice (e.g. “Transcrição pode ter sido ajustada”).

---

## 4. APIs (Backend)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serves `public/index.html`. |
| GET | `/api/config` | Returns `{ "environment": "dev" \| "prod" }`. Frontend uses this to show/hide the log box (dev only). |
| GET | `/phrases` | Returns list of phrases (id, text, translation, difficulty, topic). |
| GET | `/health` | Returns `{ "status": "ok" }`. |
| POST | `/tts` | Body: `{ "text": "..." }`. Returns `{ "audio_base64": "...", "mime": "audio/mpeg" }` (OpenAI TTS). Used to play the correct pronunciation and the feedback/d tips. |
| POST | `/evaluate` | Body: `{ "phrase_id", "audio" (base64), "expected", "mime_type" }`. Runs unified or fallback, then TTS for feedback. Returns `success`, `score`, `transcript`, `feedback`, `feedback_tts`, `tips`, `audio_base64` (feedback TTS), `used_fallback`. |

---

## 5. Evaluation Logic (Unified Prompt)

The unified prompt instructs the model to:

- **Role:** Act as an English teacher for beginners (native or non-native).
- **Transcript:** Transcribe the attached audio literally (no correction). Preserve mispronunciations (e.g. birtidey, wik) as heard.
- **Comparison:** The expected phrase is grammatically correct; evaluation is whether the user **deviated** from it (pronunciation or grammar).
- **Rules:** Use English language rules: (a) phonetics (th, r, vowels, stress, linking, intonation, schwa), (b) grammar (agreement, tense, articles, word order, plurals).
- **Tips:** One tip per clear deviation; vary wording (not every tip starting with “Você disse”). Approximate but understandable pronunciations (e.g. birtidey→birthday) can be treated as acceptable for beginners (score 70–84, at most one encouraging tip).
- **Output:** A single JSON object: `transcript`, `feedback_ui`, `feedback_tts`, `tips`, `scoreAi`.

Modalities for gpt-audio are set to `["text"]` so the API returns text (the JSON) in `message.content`, not audio.

---

## 6. Audio Pipeline

1. **Browser:** User records with MediaRecorder (typically webm). App sends base64 + `mime_type` to `/evaluate`.
2. **Server:** Saves bytes to a temp file (for fallback) and, for unified, converts to WAV in memory (or temp file for ffmpeg). WAV is base64-encoded and sent in `input_audio` to gpt-audio.
3. **Conversion:** Prefer direct ffmpeg (imageio-ffmpeg path) so only the ffmpeg binary is needed. If that fails, pydub is used (requires ffmpeg and ffprobe on PATH).

---

## 7. Frontend Flow

1. **Load:** Fetches `/phrases`, optionally `/api/config`. If `environment === "dev"`, the log box is shown.
2. **Phrase:** Displays current phrase, translation, difficulty. User can play target pronunciation via `/tts` with the phrase text.
3. **Record:** START → MediaRecorder start; STOP → blob built, base64 sent to `/evaluate`.
4. **Result:** Response is parsed; result card shows success/failure, score, meta. Tips are shown; `feedback_tts` is played as TTS (from server’s `/evaluate` response or could be requested via `/tts`). RETRY or PRÓXIMO advances state.
5. **Log:** In dev, a log box shows messages (e.g. phrase loaded, recording, evaluation result).

---

## 8. Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | **Required.** OpenAI API key. |
| `PASS_SCORE` | `90` | Minimum score (0–100) to pass a phrase. |
| `STT_MODEL` | `gpt-4o-mini-transcribe` | Model for fallback transcription. |
| `TTS_MODEL` | `tts-1-hd` | Model for TTS (phrase and feedback). |
| `TTS_VOICE` | `alloy` | Voice: alloy, echo, fable, onyx, nova, shimmer. |
| `EVAL_AUDIO_MODEL` | `gpt-audio` | Model for unified evaluation (must support audio input). |
| `USE_UNIFIED_EVAL` | `true` | If true, unified flow is tried first; fallback only on failure. |
| `ENVIRONMENT` | `prod` | `dev` shows log box in UI; `prod` hides it. |

---

## 9. Dependencies

- **Python:** FastAPI, uvicorn, openai (>=1.54 for gpt-audio modalities), python-dotenv, pydub, imageio-ffmpeg. See `requirements.txt`.
- **ffmpeg:** Provided by imageio-ffmpeg in venv when possible; otherwise must be on PATH for conversion (and for pydub fallback, ffprobe too).

---

## 10. Files Reference

- `main.py` — All backend logic (routes, `evaluate_speech_unified`, `generate_personalized_feedback`, `_audio_to_wav`, config).
- `public/index.html` — Page structure; log box has `id="logBox"` and class `hidden` (shown only when `ENVIRONMENT=dev`).
- `public/app.js` — State, `loadPhrases`, `showPhrase`, recording, `/evaluate` and `/tts` calls, result and TTS playback, log.
- `public/styles.css` — Styles.
- `docs/SYSTEM_OVERVIEW.md` — This document.
- `docs/API_CHOICE_ANALYSIS.md` — API choice rationale.
- `docs/INTONATION_ANALYSIS_PLAN.md` — Intonation/analysis notes.
