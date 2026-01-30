import json
import os
import re
import base64
import subprocess
import tempfile
import uuid
from io import BytesIO
from typing import Literal, cast

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI

# Prefer ffmpeg from imageio-ffmpeg (bundled in venv) so pydub works without system PATH
_FFMPEG_EXE = None
try:
    import imageio_ffmpeg
    _FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
    if _FFMPEG_EXE:
        _ffmpeg_dir = os.path.dirname(_FFMPEG_EXE)
        os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

load_dotenv()

def _log_ffmpeg_status():
    if _FFMPEG_EXE:
        print("[STARTUP] ffmpeg from imageio-ffmpeg (unified eval available if conversion succeeds).")
    else:
        print("[STARTUP] ffmpeg not from imageio-ffmpeg. Install ffmpeg (and ffprobe) on PATH for unified eval; otherwise fallback (STT) will be used.")

_log_ffmpeg_status()

API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise RuntimeError("OPENAI_API_KEY não definida no .env")

PASS_SCORE = int(os.getenv("PASS_SCORE", "90"))
STT_MODEL = os.getenv("STT_MODEL", "gpt-4o-mini-transcribe")
# tts-1-hd: better quality; gpt-4o-mini-tts: alternative
TTS_MODEL = os.getenv("TTS_MODEL", "tts-1-hd")

# OpenAI TTS: alloy, echo, fable, onyx, nova, shimmer. alloy/echo tend to stay in Portuguese.
Voice = Literal["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
_ALLOWED_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}
voice_env = (os.getenv("TTS_VOICE", "alloy") or "alloy").lower()
TTS_VOICE: Voice = cast(Voice, voice_env if voice_env in _ALLOWED_VOICES else "alloy")

# Unified evaluation: one Chat Completions call with audio (model acts as English teacher)
EVAL_AUDIO_MODEL = os.getenv("EVAL_AUDIO_MODEL", "gpt-audio")
USE_UNIFIED_EVAL = os.getenv("USE_UNIFIED_EVAL", "true").lower() in ("1", "true", "yes")

ENVIRONMENT = (os.getenv("ENVIRONMENT", "prod") or "prod").lower()

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

@app.get("/api/config")
async def get_config():
    """Frontend uses this to show/hide dev-only UI (e.g. log box)."""
    return {"environment": ENVIRONMENT}

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


def _audio_to_wav(audio_bytes: bytes, ext: str) -> bytes | None:
    """Convert to WAV. Prefer direct ffmpeg (no ffprobe). Fallback: pydub (needs ffprobe on PATH)."""
    # 1) Direct ffmpeg: only needs ffmpeg binary (e.g. from imageio-ffmpeg on Windows, which does not ship ffprobe)
    if _FFMPEG_EXE and os.path.isfile(_FFMPEG_EXE):
        tmp_in = None
        try:
            tmp_in = os.path.join(tempfile.gettempdir(), f"temp_in_{uuid.uuid4().hex}.{ext}")
            with open(tmp_in, "wb") as f:
                f.write(audio_bytes)
            result = subprocess.run(
                [_FFMPEG_EXE, "-y", "-i", tmp_in, "-f", "wav", "-acodec", "pcm_s16le", "-"],
                capture_output=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            pass
        except Exception:
            pass
        finally:
            if tmp_in and os.path.exists(tmp_in):
                try:
                    os.remove(tmp_in)
                except Exception:
                    pass
    # 2) pydub (needs ffmpeg and ffprobe on PATH)
    try:
        from pydub import AudioSegment
    except ImportError:
        print("[WARN] pydub not installed. pip install pydub. Unified eval will fall back to STT.")
        return None
    if _FFMPEG_EXE:
        AudioSegment.converter = _FFMPEG_EXE
    try:
        seg = AudioSegment.from_file(BytesIO(audio_bytes), format=ext)
        buf = BytesIO()
        seg.export(buf, format="wav")
        return buf.getvalue()
    except Exception as e:
        print(f"[WARN] Audio conversion to WAV failed: {e}. Install ffmpeg (and ffprobe) on PATH. Unified eval will use fallback.")
        return None


def evaluate_speech_unified(audio_base64: str, audio_format: str, expected_text: str, pass_score: int, phrase_id: int) -> tuple[str, str, str, list[str], int, bool] | None:
    """
    Single Chat Completions call: model listens to audio and returns transcript + evaluation as English teacher.
    Returns (transcript, feedback_ui, feedback_tts, tips, scoreAi, userPassed) or None to fall back to two-call flow.

    Where pronunciation/intonation are evaluated: ONLY here. We send the raw audio (input_audio) to gpt-audio.
    The model receives the actual audio and "hears" it; we do NOT run separate acoustic analysis (e.g. librosa).
    How it knows English: gpt-audio is trained on speech data and can judge pronunciation/intonation from the
    audio; we only instruct it via prompt. Fallback flow (Transcriptions + generate_personalized_feedback)
    has NO audio—only transcript—so it cannot evaluate pronunciation or intonation.
    """
    audio_bytes = base64.b64decode(audio_base64)
    ext = audio_format.lower() if audio_format else "webm"
    if ext not in ("wav",):
        wav_bytes = _audio_to_wav(audio_bytes, ext)
        if wav_bytes is None:
            print("[EVAL] Unified skipped: audio conversion to WAV failed. Using fallback (STT).")
            return None
        audio_bytes = wav_bytes
        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
        ext = "wav"

    print("[EVAL] Unified: WAV ok, calling gpt-audio...")
    is_last = is_last_phrase(phrase_id)
    prompt = f"""O áudio do aluno está ANEXADO a esta mensagem (bloco input_audio abaixo). Ouça-o agora. NÃO responda com frases como "envie o áudio" ou "please send the audio". Sua resposta deve ser EXATAMENTE um objeto JSON (começando com {{ e terminando com }}), sem texto antes ou depois.

Você é um professor de inglês avaliando a fala de um aluno INICIANTE. O aluno pode ser NÃO NATIVO (ex.: brasileiro aprendendo inglês) ou NATIVO em inglês (ex.: criança ou praticante); considere os dois cenários.
- Transcreva literalmente o que você ouviu (ex.: burst dey, birtidey, wik, weeki — como soou).
- Ao avaliar e dar dicas: se soar como sotaque de não nativo (tentativas próximas como burst dey→birthday, wik→week), reconheça a intenção e inclua dica de pronúncia quando útil (som th, sílaba tônica, vogais, r). Se soar nativo ou fluente, avalie pronúncia/entonação normalmente.

PRINCÍPIO ABSOLUTO (OBRIGATÓRIO):
- O áudio deve ser representado EXATAMENTE como foi falado. Nenhuma correção por parte da IA no que o aluno disse.
- O campo "transcript" é a transcrição LITERAL do áudio: o que você ouviu, tal qual soou. Zero normalização, zero correção ortográfica ou fonética.
- A avaliação (nota, feedback, tips) ocorre EM CIMA desse transcript e do áudio exatamente como foram — não em cima de uma versão "corrigida". Se o aluno falou errado, o transcript deve mostrar errado e a avaliação deve apontar o erro.

REGRA CRÍTICA PARA O "transcript":
- Transcreva EXATAMENTE o que você OUVIU. Zero correção, zero interpretação. Preserve erros: birtidey, nexti, wikiii, weekii, liki, drinkii, watere, morningui — como soaram.
- Exemplo: soou "My birtidey is nexti wikiii" → transcript = "My birtidey is nexti wikiii". NUNCA "My birthday is next week" nem "My big today is next cheweeky" (não invente "che", não separe "birtidey" em "big today", não troque "wikiii" por "cheweeky").
- NUNCA invente sílabas ou palavras que o aluno não disse. NUNCA "corrija" no transcript — a avaliação vem depois, em cima do transcript literal.
- Se você alterar o transcript, a avaliação deixa de ser sobre o que o aluno falou de fato.

FRASE QUE O ALUNO DEVERIA TER DITO (já está gramaticalmente correta; use só para comparar com o que o aluno disse): "{expected_text}"
- A avaliação é se o usuário FUGIR desse padrão (pronúncia errada ou dizer algo gramaticalmente diferente). Se fugir, aponte o erro e ensine (dica de pronúncia ou de gramática conforme o desvio).

Nota mínima para passar: {pass_score}%
Última frase do exercício: {is_last}

Tarefas:
1. Transcreva no campo "transcript" EXATAMENTE o que o aluno disse no áudio (mesmo que esteja errado).
2. Compare o transcript com a frase esperada. Só inclua dica nas tips quando o desvio for CLARO ou prejudicar o entendimento. Pronúncias APROXIMADAS e compreensíveis (ex.: birtidey→birthday, wik→week) contam como aceitáveis para iniciante: NÃO exija uma dica por palavra nesses casos; pode dar nota 70–84 e no máximo 1 dica encorajadora (ex.: "Quase! Para soar mais nativo: th em birth, vogal longa em week.") em vez de várias tips. Dicas obrigatórias: quando a palavra estiver realmente errada (som muito diferente, palavra trocada, gramática errada).
3. Ao avaliar e orientar, use as REGRAS DA LÍNGUA INGLESA: (a) FONÉTICA/pronúncia: sons th, r, vogais longas/curtas, consoantes, sílaba tônica, linking, entonação, schwa. (b) GRAMÁTICA: concordância (sujeito-verbo), tempos verbais, artigos (a/an/the), ordem das palavras, plurais, etc. Se o aluno desviar do esperado (pronúncia ou gramática), aponte o erro e oriente com base nessas regras. Ex.: "liki"→like (pronúncia); "he go"→"he goes" (gramática).
4. Avalie e dê a nota conforme os critérios abaixo.
5. Retorne um único objeto JSON (sem markdown): transcript, feedback_ui, feedback_tts, tips, scoreAi.

CRITÉRIOS DE NOTA (OBRIGATÓRIO – seja coerente):
- Transcript = fala REAL (como ouviu). Pronúncias APROXIMADAS (birtidey, wik, etc.) = aceitáveis para iniciante: nota 70–84, feedback pode ser positivo ("Quase!", "Boa tentativa!") e no máximo 1–2 dicas encorajadoras, não uma dica por palavra.
- 85–100: transcript = frase esperada palavra por palavra E pronúncia/entonação aceitáveis. "Frase correta."
- 70–84: transcript com pronúncias aproximadas mas compreensíveis (birtidey→birthday, wik→week). Aceitável para iniciante. Uma dica geral ou nenhuma; feedback encorajador.
- 50–69: desvios claros (palavra trocada, som muito diferente, gramática errada). Inclua dicas por erro relevante.
- 30–49: muitas palavras erradas/faltando.
- 0–29: quase nada correto.
- Só seja negativo ("Ajuste a pronúncia.", nota baixa, várias tips) quando os desvios forem CLAROS e prejudicarem o entendimento.

REGRAS:
- As tips: só inclua dica quando o desvio for CLARO. Pronúncias aproximadas: no máximo 1 dica encorajadora. VARIE o texto das tips — não repita "Você disse" em todas. Exemplos: "Em birthday: o correto é birthday (som th em birth)." / "week: vogal longa, como iː." / "Correção: birtidey → birthday." Use "Você disse X; o correto é Y" só quando fizer sentido. Palavra a mais: "Remova a palavra [X]." (NUNCA use "Tire".) Use até 8 itens só quando houver vários erros claros.
- AUDIO (feedback_tts e tips): Palavras em inglês (birthday, week, etc.) podem aparecer em inglês para o TTS. O resto em português.
- feedback_ui: mensagem curta para a tela, em português do Brasil, pode ter emoji. Pronúncias aproximadas (nota 70–84): feedback pode ser positivo ("Quase!", "Boa tentativa!"). Só use feedback negativo ("Ajuste a pronúncia.", "A frase não está correta.") quando houver desvios claros (nota 50–69 ou menos).
- feedback_tts e tips: varie a redação; não comece todas as dicas com "Você disse". Use formas como "Em [palavra]: [dica].", "[palavra]: [correção].", "Correção: X → Y." quando cabível. "Remova a palavra [X]." para palavra a mais (nunca "Tire"). Palavras em inglês quando necessário; o resto em português.
- "Frase correta" / "Muito bem" / nota 85+ SOMENTE quando o transcript for IGUAL à frase esperada palavra por palavra. Se houver qualquer desvio (transcript diferente da frase esperada OU tips com correções), NUNCA diga "a frase está correta" nem "está correta mas...", "correta, porém...", "correta mas cuidado com...". Nesse caso feedback_ui DEVE ser claramente negativo: ex. "Ajuste a pronúncia.", "A frase não está correta.", "Corrija as palavras nas dicas." — e nota 50–70.
- NÃO repita a frase inteira no feedback_tts; NÃO mencione "próximo exercício" ou "curso concluído".
- A nota deve refletir transcript + pronúncia + entonação (você ouve o áudio): palavras erradas, pronúncia ruim ou entonação completamente errada devem baixar a nota.
- Responda SOMENTE com o objeto JSON, sem outro texto. Exemplo de resposta válida: {{"transcript":"...","feedback_ui":"...","feedback_tts":"...","tips":[],"scoreAi":70}}"""

    content = [
        {"type": "text", "text": prompt},
        {"type": "input_audio", "input_audio": {"data": audio_base64, "format": "wav"}},
    ]

    try:
        kwargs = {
            "model": EVAL_AUDIO_MODEL,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.2,
            "max_tokens": 1000,
        }
        if "gpt-audio" in EVAL_AUDIO_MODEL.lower():
            kwargs["modalities"] = ["text"]
        try:
            response = client.chat.completions.create(**kwargs)
        except TypeError as te:
            if "modalities" in str(te) or "unexpected keyword" in str(te).lower():
                kwargs.pop("modalities", None)
                response = client.chat.completions.create(**kwargs)
            else:
                raise
    except Exception as e:
        hint = " (pip install -U 'openai>=1.54.0' para gpt-audio)" if "modalities" in str(e) or "unexpected keyword" in str(e).lower() else ""
        print(f"[EVAL] Unified (gpt-audio) failed: {type(e).__name__}: {e}.{hint}")
        return None

    text = (response.choices[0].message.content or "").strip()
    if not text:
        print("[EVAL] Unified: API respondeu mas message.content vazio.")
        return None

    # Retry once if model replied with "send the audio" instead of JSON (audio may not have been processed)
    def parse_unified_response(raw: str) -> dict | None:
        t = raw.strip()
        if "```" in t:
            t = re.sub(r"^.*?```(?:json)?\s*", "", t).strip()
            t = re.sub(r"\s*```.*$", "", t).strip()
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            return None

    data = parse_unified_response(text)
    if data is None and ("envie" in text.lower() or "send the audio" in text.lower() or "send the" in text.lower()):
        print("[EVAL] Unified: modelo pediu áudio em vez de JSON; refazendo chamada...")
        try:
            response = client.chat.completions.create(**kwargs)
            text = (response.choices[0].message.content or "").strip()
            if text:
                data = parse_unified_response(text)
        except Exception:
            pass
    if data is None:
        print(f"[EVAL] Unified: resposta não é JSON válido. Primeiros 200 chars: {text[:200]!r}")
        return None

    transcript = (data.get("transcript") or "").strip()
    feedback_ui = data.get("feedback_ui", "🟡 Tente novamente.")
    feedback_tts = data.get("feedback_tts", "Tente novamente.")
    tips = data.get("tips", [])
    scoreAi = int(data.get("scoreAi", 0))
    userPassed = scoreAi >= pass_score
    # Log: o que a IA retornou como "o que ouviu" (para checar se está corrigindo antes de analisar)
    print(f"[EVAL] expected={expected_text!r} | transcript (o que a IA ouviu)={transcript!r} | score={scoreAi}")
    return transcript, feedback_ui, feedback_tts, tips, scoreAi, userPassed

# Fallback flow: Transcriptions + Completions when unified is disabled or failed
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
3. tips: UMA dica por palavra incorreta. VARIE o texto — não repita "Você disse" em todas. Use formatos como "Em birthday: o correto é birthday (som th).", "Correção: birtidey → birthday.", "week: vogal longa (iː)." Use "Você disse X; o correto é Y" só quando fizer sentido. Palavra a mais: "Remova a palavra [X]." NUNCA use "Tire". Até 8 itens se houver vários erros.
4. Palavras da frase esperada e a forma errada em inglês no texto; o resto em português.
5. scoreAi: pontuação 0-100 baseada em pronúncia e clareza
6. Mesmo que o aluno atinja a meta, erros de pronúncia DEVEM ser apontados
7. Corrija TODOS os erros identificados (cada palavra errada = uma dica)

CRITÉRIOS DE NOTA (OBRIGATÓRIO – seja coerente com o que o aluno disse):
- 85–100: frase correta com pequenos desvios de pronúncia aceitáveis para iniciante.
- 70–84: maioria das palavras certas, 1–2 erros leves.
- 50–69: várias palavras erradas ou faltando.
- 30–49: muitas palavras erradas/faltando, frase bem diferente da esperada.
- 0–29: quase nada correto ou incompreensível.
Compare palavra por palavra (transcript vs frase esperada). Aluno é iniciante: entonação pouco relevante; palavras erradas/faltando devem baixar a nota.

ORGANIZAÇÃO DO FEEDBACK (OBRIGATÓRIO):
- feedback_tts: orientação PRINCIPAL (1 frase curta)
- tips: UMA dica por palavra errada. Varie a redação (não comece todas com "Você disse"). Ex.: "Em [palavra]: [dica].", "Correção: X → Y.", "[palavra]: [orientação]." Até 8 itens. QUANDO CORRETA (nota 85+): tips = [] ou incentivo. Cite a palavra errada e a correta sem repetir a mesma fórmula.

9. NUNCA mencione "próximo exercício" ou "curso concluído" - o app já cuida disso
10. Responda SOMENTE em JSON válido, sem markdown
11. Exemplo (palavras da frase em inglês citadas em inglês para o TTS pronunciar):
{{"feedback_ui":"🔴 A frase mudou de sentido.","feedback_tts":"A palavra next não foi dita e isso muda o significado.","tips":["A palavra next não foi dita e isso muda o significado.","A palavra que você usou não funciona nesse contexto. Use a palavra next para tempo futuro."],"scoreAi":40 }}

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

        transcript = ""
        feedback_ui = "🟡 Tente novamente."
        feedback_tts = "Tente novamente."
        tips: list[str] = []
        scoreAi = 0
        userPassed = False

        # 1) Unified (gpt-audio) is the main flow: audio → literal transcript + evaluation. Retry once before fallback.
        result = evaluate_speech_unified(
            audio_base64, ext, expected_text, PASS_SCORE, phrase_id,
        )
        if result is None:
            print("[EVAL] Unified falhou; tentando novamente antes do fallback...")
            result = evaluate_speech_unified(
                audio_base64, ext, expected_text, PASS_SCORE, phrase_id,
            )
        if result is not None:
            used_fallback = False
            transcript, feedback_ui, feedback_tts, tips, scoreAi, userPassed = result
        else:
            # 2) Fallback: transcribe literal (como no fluxo principal) + evaluate. STT pode normalizar; pedimos transcrição exata.
            used_fallback = True
            print("[EVAL] Usando fallback (unificado falhou após retry). Transcrição deve ser exatamente o que foi dito no áudio.")
            stt_prompt = (
                "Transcribe exactly what the speaker said. Do not correct. Literal only. "
            )
            with open(tmp_path, "rb") as f:
                transcript_obj = client.audio.transcriptions.create(
                    model=STT_MODEL,
                    file=f,
                    language="en",
                    prompt=stt_prompt[:500],
                    temperature=0.2,
                )
            transcript = (getattr(transcript_obj, "text", "") or "").strip()
            print(f"[EVAL fallback] expected={expected_text!r} | transcript={transcript!r}")
            feedback_ui, feedback_tts, tips, scoreAi, userPassed = generate_personalized_feedback(
                expected_text, transcript, PASS_SCORE, phrase_id,
            )

        # ===== TTS DO FEEDBACK (só o texto das dicas, sem prefixo) =====
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
            "used_fallback": used_fallback,
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