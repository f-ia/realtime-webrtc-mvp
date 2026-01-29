import os
import re
import base64
import uuid
from typing import Literal, cast

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise RuntimeError("OPENAI_API_KEY não definida no .env")

PASS_SCORE = int(os.getenv("PASS_SCORE", "80"))
STT_MODEL = os.getenv("STT_MODEL", "whisper-1")
TTS_MODEL = os.getenv("TTS_MODEL", "gpt-4o-mini-tts")

Voice = Literal["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
_ALLOWED_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}
voice_env = (os.getenv("TTS_VOICE", "nova") or "nova").lower()
TTS_VOICE: Voice = cast(Voice, voice_env if voice_env in _ALLOWED_VOICES else "nova")

app = FastAPI()
client = OpenAI(api_key=API_KEY)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# STATIC
app.mount("/public", StaticFiles(directory="public"), name="public")

@app.get("/")
async def root():
    return FileResponse("public/index.html")

PHRASES = [
    {
        "id": 1,
        "text": "My birthday is next week.",
        "translation": "Meu aniversário é semana que vem.",
        "difficulty": "Easy",
        "topic": "Personal",
    },
    {
        "id": 2,
        "text": "I like to drink water in the morning.",
        "translation": "Eu gosto de beber água pela manhã.",
        "difficulty": "Easy",
        "topic": "Daily Routine",
    },
    {
        "id": 3,
        "text": "I loved meeting my new friend.",
        "translation": "Eu adorei conhecer meu novo amigo.",
        "difficulty": "Easy",
        "topic": "Social",
    },
]

def normalize_text(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def levenshtein_distance_words(ref_words, hyp_words) -> int:
    n, m = len(ref_words), len(hyp_words)
    dp = [[0] * (m + 1) for _ in range(n + 1)]

    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref_words[i - 1] == hyp_words[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )
    return dp[n][m]

def word_accuracy_percent(expected: str, transcript: str) -> int:
    ref = normalize_text(expected).split()
    hyp = normalize_text(transcript).split()
    if not ref:
        return 0

    dist = levenshtein_distance_words(ref, hyp)
    acc = max(0.0, 1.0 - (dist / len(ref)))
    return int(round(acc * 100))

def ext_from_mime(mime: str) -> str:
    m = (mime or "").lower()
    if "webm" in m:
        return "webm"
    if "ogg" in m:
        return "ogg"
    if "wav" in m:
        return "wav"
    if "mpeg" in m or "mp3" in m:
        return "mp3"
    if "mp4" in m or "m4a" in m:
        return "m4a"
    return "webm"

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/phrases")
def get_phrases():
    return {"phrases": PHRASES, "total": len(PHRASES)}

@app.post("/tts")
async def tts(request: Request):
    try:
        data = await request.json()
        text = (data.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "Texto vazio"}, status_code=400)

        audio = client.audio.speech.create(
            model=TTS_MODEL,
            voice=TTS_VOICE,
            input=text,
            response_format="mp3",
        )

        audio_b64 = base64.b64encode(audio.content).decode("utf-8")
        return {"audio_base64": audio_b64, "mime": "audio/mpeg"}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/evaluate")
async def evaluate(request: Request):
    tmp_path = None

    try:
        data = await request.json()

        phrase_id = data.get("phrase_id")
        audio_base64 = data.get("audio")
        mime_type = data.get("mime_type") or "audio/webm"

        phrase = next((p for p in PHRASES if p["id"] == phrase_id), None)
        if not phrase:
            return JSONResponse({"error": "Frase não encontrada"}, status_code=404)

        expected_text = (data.get("expected") or phrase["text"]).strip()
        if not audio_base64:
            return JSONResponse({"error": "Áudio vazio"}, status_code=400)

        audio_bytes = base64.b64decode(audio_base64)

        ext = ext_from_mime(mime_type)
        tmp_path = f"temp_{uuid.uuid4().hex}.{ext}"
        with open(tmp_path, "wb") as f:
            f.write(audio_bytes)

        with open(tmp_path, "rb") as f:
            transcript_obj = client.audio.transcriptions.create(
                model=STT_MODEL,
                file=f,
            )

        transcript = (getattr(transcript_obj, "text", "") or "").strip()
        score = word_accuracy_percent(expected_text, transcript)
        success = score >= PASS_SCORE

        feedback = "✅ Muito bem! Vamos ao próximo." if success else "🟡 Tente novamente. Repita a frase."

        # >>> MUDANÇA PRINCIPAL (demo): áudio retornado sempre 1:1 com a frase da tela
        # (seu front pode tocar isso em playbackRate > 1.0)
        audio_b64 = None
        try:
            audio = client.audio.speech.create(
                model=TTS_MODEL,
                voice=TTS_VOICE,
                input=expected_text,  # 1:1 com o texto em tela
                response_format="mp3",
            )
            audio_b64 = base64.b64encode(audio.content).decode("utf-8")
        except Exception:
            audio_b64 = None

        return {
            "success": success,
            "score": score,
            "pass_score": PASS_SCORE,
            "transcript": transcript,
            "expected": expected_text,
            "feedback": feedback,
            "phrase_id": phrase_id,
            "audio_base64": audio_b64,
            "mime": "audio/mpeg",
        }

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
