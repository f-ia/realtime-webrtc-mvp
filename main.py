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

PASS_SCORE = int(os.getenv("PASS_SCORE", "90"))
STT_MODEL = os.getenv("STT_MODEL", "gpt-4o-mini-transcribe")
TTS_MODEL = os.getenv("TTS_MODEL", "gpt-4o-mini-tts")

Voice = Literal["nova",]
_ALLOWED_VOICES = {"nova"}
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

def is_last_phrase(phrase_id: int) -> bool:
    """Verifica se é a última frase"""
    return phrase_id == PHRASES[-1]["id"]

def generate_personalized_feedback(expected_text: str, transcript: str, pass_score: int, phrase_id: int) -> tuple[str, str, list[str], int, bool]:
    """
    Chama o modelo de chat pra gerar feedback IA personalizado.
    Retorna: (feedback_ui, feedback_tts, tips, scoreAi, targetResult)
    """
    exp_normalized = normalize_text(expected_text).split()
    hyp_normalized = normalize_text(transcript).split()

    missing_words = [w for w in exp_normalized if w not in hyp_normalized]
    extra_words = [w for w in hyp_normalized if w not in exp_normalized]

    error_context = ""
    if missing_words:
        error_context += f"Palavras que faltaram: {', '.join(missing_words[:3])}. "
    if extra_words:
        error_context += f"Palavras extras: {', '.join(extra_words[:3])}. "

    is_last = is_last_phrase(phrase_id)

    prompt = f"""Você é um professor de inglês para alunos.
Sua função é avaliar o speaking do aluno, caso o aluno fale errado ou pronuncie errado, você deve corrigir com um feedback curto, em português BR, crítico em JSON.

Contexto:
- Frase esperada: "{expected_text}"
- O aluno disse: "{transcript}"
- Meta: {pass_score}%
- É a última frase: {is_last}
{error_context if error_context else ""}

REGRAS:
1. NUNCA repita a frase inteira no feedback_tts
2. feedback_ui pode ter emoji e é para exibir na tela
3. feedback_tts é para o TTS falar - sem emoji, sem "repita a frase"
4. tips é um array de orientações para o aluno
5. scoreAi é a pontuação estimada (0-100) do aluno baseada APENAS na pronúncia e clareza
6. Mesmo que o aluno atinja a meta, erros de pronúncia, palavras ou fluidez DEVEM ser apontados
7. Corrija TODOS os erros identificados

CRITÉRIOS DE PONTUAÇÃO (OBRIGATÓRIO):
- Avalie a pronuncia de 0 a 100, considere que o aluno seja iniciante.
- Não avalie de forma critica a entonação, lembre-se que é um aluno iniciante.

ORGANIZAÇÃO DO FEEDBACK (OBRIGATÓRIO):
- feedback_tts deve conter a orientação PRINCIPAL (1 frase curta)
- tips deve conter TODAS as orientações, incluindo o conteúdo do feedback_tts
- tips deve ter NO MÁXIMO 2 itens no total
- Cada item deve corrigir UM ponto específico
- Priorize erros que afetam o significado da frase
- Evite frases genéricas ou motivacionais

9. NUNCA mencione "próximo exercício" ou "curso concluído" - o app já cuida disso
10. Responda SOMENTE em JSON válido, sem markdown
11. Exemplo:
{{"feedback_ui":"🔴 A frase mudou de sentido.","feedback_tts":"A palavra next não foi dita e isso muda o significado.","tips":["A palavra next não foi dita e isso muda o significado.","Almost não funciona nesse contexto.","Use next para falar de tempo futuro."],"scoreAi":40 }}

Agora gere o feedback JSON (sem markdown, apenas o objeto):"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1000,
        )

        print("response: ",response)
        
        content = response.choices[0].message.content

        if not content:
            raise ValueError("Empty response from AI")

        response_text = content.strip()

        # Tenta parsear JSON
        import json
        feedback_json = json.loads(response_text)
        
        feedback_ui = feedback_json.get("feedback_ui", "🟡 Tente novamente.")
        feedback_tts = feedback_json.get("feedback_tts", "Tente novamente.")
        tips = feedback_json.get("tips", [])
        scoreAi = feedback_json.get("scoreAi", 0)
        userPassed = scoreAi >= pass_score

        return feedback_ui, feedback_tts, tips, scoreAi, userPassed
    
    except Exception as e:
        print(f"Erro ao gerar feedback IA: {e}")
        # Fallback genérico
        return "🟡 Tente novamente.", "Tente novamente.", [], 0, False

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

        # ===== STT COM PROMPT =====
        with open(tmp_path, "rb") as f:
            transcript_obj = client.audio.transcriptions.create(
                model=STT_MODEL,
                file=f,
                language="en",
                prompt=expected_text,
            )

        transcript = (getattr(transcript_obj, "text", "") or "").strip()

        # ===== FEEDBACK =====
        # Tutor IA gera feedback personalizado
        feedback_ui, feedback_tts, tips, scoreAi, userPassed = generate_personalized_feedback(
            expected_text, transcript, PASS_SCORE, phrase_id,
        )

        # ===== TTS DO FEEDBACK (não da frase) =====
        audio_b64 = None
        try:
            audio = client.audio.speech.create(
                model=TTS_MODEL,
                voice=TTS_VOICE,
                input=feedback_tts,
                response_format="mp3",
            )
            audio_b64 = base64.b64encode(audio.content).decode("utf-8")
        except Exception as e:
            print(f"Erro ao gerar TTS: {e}")
            audio_b64 = None

        return {
            "success": userPassed,
            "score": scoreAi,
            "pass_score": PASS_SCORE,
            "transcript": transcript,
            "expected": expected_text,
            "feedback": feedback_ui,
            "feedback_tts": feedback_tts,
            "tips": tips,
            "phrase_id": phrase_id,
            "audio_base64": audio_b64,
            "mime": "audio/mpeg",
        }

    except Exception as e:
        print(f"Erro no /evaluate: {e}")
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