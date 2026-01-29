import os
import base64
import uuid
from difflib import SequenceMatcher

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from openai import OpenAI

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise ValueError("OPENAI_API_KEY não definida no .env")

app = FastAPI()
client = OpenAI(api_key=API_KEY)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return FileResponse("public/index.html")

app.mount("/public", StaticFiles(directory="public"), name="public")

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

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/phrases")
def get_phrases():
    return {"phrases": PHRASES, "total": len(PHRASES)}

def _ext_from_mime(mime: str) -> str:
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

@app.post("/tts")
async def tts(request: Request):
    try:
        data = await request.json()
        text = (data.get("text") or "").strip()
        instructions = (data.get("instructions") or "Speak in a positive tone.").strip()

        if not text:
            return JSONResponse({"error": "text vazio"}, status_code=400)

        # TTS: /v1/audio/speech (openai-python: client.audio.speech.create) [web:207][web:223]
        audio = client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="nova",
            input=text,
            response_format="mp3",
        )

        audio_b64 = base64.b64encode(audio.content).decode("utf-8")
        return {"audio_base64": audio_b64, "mime": "audio/mpeg"}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/evaluate")
async def evaluate_speaking(request: Request):
    tmp_path = None
    try:
        data = await request.json()
        phrase_id = data.get("phrase_id")
        audio_base64 = data.get("audio")
        expected_text = data.get("expected")
        mime_type = data.get("mime_type") or "audio/webm"

        phrase = next((p for p in PHRASES if p["id"] == phrase_id), None)
        if not phrase:
            return JSONResponse({"error": "Phrase not found"}, status_code=404)

        if not audio_base64:
            return JSONResponse({"error": "Audio vazio"}, status_code=400)

        audio_bytes = base64.b64decode(audio_base64)

        # Salva temporário (ext baseado no mime vindo do browser)
        ext = _ext_from_mime(mime_type)
        tmp_path = f"temp_audio_{uuid.uuid4().hex}.{ext}"
        with open(tmp_path, "wb") as f:
            f.write(audio_bytes)

        # STT com whisper-1 (transcriptions endpoint) [web:222]
        with open(tmp_path, "rb") as f:
            transcript_obj = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
            )

        transcript = (transcript_obj.text or "").strip()

        # Score simples por similaridade
        similarity = SequenceMatcher(None, transcript.lower(), expected_text.lower()).ratio()
        score = int(similarity * 100)
        success = similarity >= 0.95

        # Feedback mais friendly
        prompt = f"""
Você é um professor de inglês e sua função é dizer se o aluno foi bem ou não, use frases como "Muito bem, vamos para o próximo" ou se o aluno não acertar "Poxa, vamos tentar novamente"
Aluno disse: "{transcript}"
Esperado: "{expected_text}"
Score: {score}%

Regras:
- Máximo 1 frases em português/inglês.
- Dê 1 dica objetiva somente se o aluno errar pronúncia ou palavra.
- Se o aluno errar, termine pedindo para repetir exatamente: "{expected_text}".
"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=90,
        )
        feedback = response.choices[0].message.content.strip()

        # Áudio do feedback + frase alvo (TTS) [web:207][web:223]
        tts_text = f"{feedback} Agora repita: {expected_text}"
        audio = client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=tts_text,
            response_format="mp3",
        )
        audio_b64 = base64.b64encode(audio.content).decode("utf-8")

        return {
            "success": success,
            "score": score,
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
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except:
            pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
