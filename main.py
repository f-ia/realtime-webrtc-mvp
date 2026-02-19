import json
import os
import re
import base64
import subprocess
import tempfile
import uuid
import warnings
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

# Suppress pydub's "Couldn't find ffmpeg" warning before any pydub import (we set converter in _configure_pydub_ffmpeg)
warnings.filterwarnings(
    "ignore",
    message=".*Couldn't find ffmpeg or avconv.*",
    category=RuntimeWarning,
)

load_dotenv()

def _log_ffmpeg_status():
    if _FFMPEG_EXE:
        print("[STARTUP] ffmpeg from imageio-ffmpeg (unified eval available if conversion succeeds).")
    else:
        print("[STARTUP] ffmpeg not from imageio-ffmpeg. Install ffmpeg (and ffprobe) on PATH for unified eval; otherwise fallback (STT) will be used.")


def _configure_pydub_ffmpeg():
    """Point pydub at imageio-ffmpeg's ffmpeg so it uses our binary."""
    if not _FFMPEG_EXE or not os.path.isfile(_FFMPEG_EXE):
        return
    try:
        import pydub
        pydub.AudioSegment.converter = _FFMPEG_EXE
        pydub.AudioSegment.ffprobe = _FFMPEG_EXE
    except Exception:
        pass


_log_ffmpeg_status()
_configure_pydub_ffmpeg()

API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise RuntimeError("OPENAI_API_KEY não definida no .env")

PASS_SCORE = int(os.getenv("PASS_SCORE", "70"))
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

# Optional: Assistant ID to fetch evaluation prompt template from OpenAI (instructions = template with {expected_text}, {pass_score}, {is_last})
OPENAI_EVAL_ASSISTANT_ID = (os.getenv("OPENAI_EVAL_ASSISTANT_ID") or "").strip() or None

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

# Default prompt template for unified eval. Placeholders: {expected_text}, {pass_score}, {is_last}. Used when OPENAI_EVAL_ASSISTANT_ID is not set.
DEFAULT_EVAL_PROMPT_TEMPLATE = """O áudio do aluno está ANEXADO a esta mensagem (bloco input_audio abaixo). Ouça-o agora. NÃO responda com frases como "envie o áudio" ou "please send the audio". Sua resposta deve ser EXATAMENTE um objeto JSON (começando com {{ e terminando com }}), sem texto antes ou depois.

Você é um professor de inglês avaliando a fala de um aluno INICIANTE. O aluno pode ser NÃO NATIVO (ex.: brasileiro aprendendo inglês) ou NATIVO em inglês (ex.: criança ou praticante); considere os dois cenários.

TRANSCRIÇÃO DO "transcript" — REGRA PRINCIPAL:
- A frase que o aluno DEVERIA ter dito é: "{expected_text}". Use-a como referência.
- PREFIRA SEMPRE a ortografia padrão das palavras dessa frase (birthday, week, meeting, friend, water, morning, etc.). Só use transcrição fonética (birtidey, wik, miting) quando a pronúncia foi CLARAMENTE ERRADA — por exemplo: faltou o som "th", vogal muito diferente, sílaba trocada. Quando o que você ouviu for reconhecível como a palavra esperada (mesmo com sotaque), transcreva com a palavra correta.
- Exemplo para "My birthday is next week.": se o aluno disse a frase de forma compreensível/correta → transcript = "My birthday is next week." NÃO use "My birtidey is next wik" a menos que você tenha ouvido de fato um erro claro (ex.: sem o "th" em birthday, "wik" em vez de "week" com vogal errada).
- Em dúvida, use a palavra da frase esperada. Só transcreva foneticamente (birtidey, wik, etc.) quando houver desvio óbvio na pronúncia que você queira apontar nas dicas.

FRASE QUE O ALUNO DEVERIA TER DITO (use para comparar e para decidir a ortografia do transcript): "{expected_text}"
- A avaliação é se o usuário FUGIR desse padrão (pronúncia errada ou dizer algo gramaticalmente diferente). Se fugir, aponte o erro e ensine (dica de pronúncia ou de gramática conforme o desvio).

Nota mínima para passar: {pass_score}%
Última frase do exercício: {is_last}

Tarefas:
1. Transcreva no campo "transcript": use as palavras da frase esperada "{expected_text}" em ortografia padrão (birthday, week, etc.) quando o que você ouviu for reconhecível como essa palavra. Só use forma fonética (birtidey, wik) quando a pronúncia for claramente errada e você for dar dica de correção.
2. Compare o transcript com a frase esperada. Se incluir QUALQUER tip que corrige uma palavra (ex.: miting→meeting, wik→week), a nota DEVE ser < {pass_score}% (reprovado). Para aprovar (nota >= {pass_score}%), tips = [] ou no máximo 1 encorajamento geral (sem corrigir palavra). Quando a palavra estiver errada (ex.: miting, warer), inclua a dica e dê nota < {pass_score}%.
3. Ao avaliar e orientar, use as REGRAS DA LÍNGUA INGLESA: (a) FONÉTICA/pronúncia: sons th, r, vogais longas/curtas, consoantes, sílaba tônica, linking, entonação, schwa. (b) GRAMÁTICA: concordância (sujeito-verbo), tempos verbais, artigos (a/an/the), ordem das palavras, plurais, etc. Se o aluno desviar do esperado (pronúncia ou gramática), aponte o erro e oriente com base nessas regras. Ex.: "liki"→like (pronúncia); "he go"→"he goes" (gramática).
4. Avalie e dê a nota conforme os critérios abaixo.
5. Retorne um único objeto JSON (sem markdown): transcript, feedback_ui, feedback_tts, tips, scoreAi.

CRITÉRIOS DE NOTA (OBRIGATÓRIO – seja coerente com a meta {pass_score}%):
- APROVADO = nota >= {pass_score}%. REPROVADO = nota < {pass_score}%.
- REGRA DE COERÊNCIA (OBRIGATÓRIA): Se você incluir QUALQUER tip que corrige uma palavra (ex.: "miting" → "meeting", "wik" → "week", "Em [[week]]: o correto é [[week]]"), a nota DEVE ser < {pass_score}% (REPROVADO). O aluno NÃO passou quando há erro a corrigir. NUNCA dê nota >= {pass_score}% quando houver pelo menos uma dica de correção por palavra — tip de correção = sempre reprovado.
- Nota >= {pass_score}% (aprovado) SOMENTE quando tips = [] ou no máximo 1 encorajamento geral ("Continue assim!") SEM corrigir nenhuma palavra.
- 100: SOMENTE quando acertar TUDO — frase (transcript = frase esperada), pronúncia E entonação corretas. Tips = []. "Frase correta."
- 90–99: frase correta, pronúncia e entonação muito boas. Tips = [] ou no máximo 1 encorajamento geral (sem corrigir palavra). (aprovado.)
- 85–89: frase correta, pronúncia/entonação aceitáveis. Tips = [] ou no máximo 1 encorajamento geral. (aprovado.)
- 70–84: transcript com pronúncias aproximadas mas compreensíveis, SEM necessidade de corrigir palavra nas tips. Tips = [] ou 1 encorajamento geral. (aprovado.) Se precisar incluir tip que corrige palavra (miting→meeting, wik→week, etc.), nota < {pass_score}% (reprovado).
- 50–69: desvios claros (palavra trocada, som muito diferente, gramática errada). REPROVADO. Inclua dicas por erro relevante.
- 30–49: muitas palavras erradas/faltando. REPROVADO.
- 0–29: quase nada correto. REPROVADO.

REGRAS:
- As tips: só inclua dica quando o desvio for CLARO. Se incluir QUALQUER tip que corrige uma palavra (ex.: miting→meeting, wik→week), a nota DEVE ser < {pass_score}% (reprovado). Aprovado (nota >= {pass_score}%) = tips = [] ou só encorajamento geral, sem correção de palavra. VARIE o texto das tips. Exemplos (use [[palavra]]): "Em [[meeting]]: o correto é [[meeting]] (som de g no final)." Palavra a mais: "Remova a palavra [[X]]." (NUNCA use "Tire".)
- IDIOMA DO ÁUDIO (feedback_tts e tips): O áudio será reproduzido com pronúncia em PORTUGUÊS no texto explicativo e pronúncia em INGLÊS apenas nas palavras citadas. Para isso, envolva CADA palavra em inglês (a que está sendo corrigida/citada) em colchetes duplos: [[palavra]]. O resto do texto NÃO deve ter colchetes e deve ser em português. Exemplo OBRIGATÓRIO: "Em [[birthday]]: o correto é [[birthday]], com som th em [[birth]]." Assim "Em", "o correto é", "com som th em" serão falados em português; "birthday" e "birth" em inglês. Use SEMPRE [[palavra]] para qualquer palavra da frase em inglês que aparecer no texto.
- feedback_ui: mensagem curta para a tela, em português do Brasil, pode ter emoji. Nota >= {pass_score}% (aprovado): feedback pode ser positivo ("Quase!", "Boa tentativa!"). Nota < {pass_score}% (reprovado): use feedback negativo ("Ajuste a pronúncia.", "A frase não está correta.").
- feedback_tts e tips: texto em português; cada palavra em inglês citada deve estar entre [[ e ]]. Ex.: "Em [[birthday]]: o correto é [[birthday]], com som th em [[birth]]." / "Correção: [[birtidey]] → [[birthday]]." / "Remova a palavra [[X]]." (nunca "Tire").
- "Frase correta" / "Muito bem" / 100 SOMENTE quando frase + pronúncia + entonação estiverem corretas (tips = []). Nota 90–99 = muito bom; 85–89 = bom. Se houver desvio, NUNCA diga "a frase está correta" nem dê 100. Se nota < {pass_score}%, feedback_ui DEVE ser claramente negativo.
- NÃO repita a frase inteira no feedback_tts; NÃO mencione "próximo exercício" ou "curso concluído".
- A nota deve refletir transcript + pronúncia + entonação (você ouve o áudio): palavras erradas, pronúncia ruim ou entonação completamente errada devem baixar a nota.
- Responda SOMENTE com o objeto JSON, sem outro texto. Exemplo de resposta válida: {{"transcript":"...","feedback_ui":"...","feedback_tts":"...","tips":[],"scoreAi":70}}"""


def get_eval_prompt_template() -> str:
    """
    Returns the evaluation prompt template. If OPENAI_EVAL_ASSISTANT_ID is set,
    fetches the template from the Assistant's instructions (editable in OpenAI dashboard).
    Otherwise returns DEFAULT_EVAL_PROMPT_TEMPLATE. Template must use placeholders:
    {expected_text}, {pass_score}, {is_last}.
    """
    if OPENAI_EVAL_ASSISTANT_ID:
        try:
            assistant = client.beta.assistants.retrieve(OPENAI_EVAL_ASSISTANT_ID)
            instructions = (assistant.instructions or "").strip()
            if instructions:
                print("[EVAL] Using prompt template from OpenAI Assistant.")
                return instructions
        except Exception as e:
            print(f"[EVAL] Failed to fetch prompt from Assistant ({OPENAI_EVAL_ASSISTANT_ID}): {e}. Using default template.")
    return DEFAULT_EVAL_PROMPT_TEMPLATE


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
    template = get_eval_prompt_template()
    try:
        prompt = template.format(expected_text=expected_text, pass_score=pass_score, is_last=is_last)
    except KeyError as e:
        print(f"[EVAL] Prompt template missing placeholder: {e}. Using default.")
        prompt = DEFAULT_EVAL_PROMPT_TEMPLATE.format(
            expected_text=expected_text, pass_score=pass_score, is_last=is_last
        )

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
4. ÁUDIO (feedback_tts e tips): Para o TTS falar em português o texto e em inglês só as palavras citadas, envolva CADA palavra em inglês em colchetes duplos: [[palavra]]. Exemplo: "Em [[birthday]]: o correto é [[birthday]], com som th em [[birth]]." O resto em português, sem colchetes.
5. scoreAi: pontuação 0-100 baseada em pronúncia e clareza
6. Mesmo que o aluno atinja a meta, erros de pronúncia DEVEM ser apontados
7. Corrija TODOS os erros identificados (cada palavra errada = uma dica)

CRITÉRIOS DE NOTA (OBRIGATÓRIO – seja coerente com a meta {pass_score}%):
- APROVADO = nota >= {pass_score}%. REPROVADO = nota < {pass_score}%.
- REGRA DE COERÊNCIA (OBRIGATÓRIA): Se incluir QUALQUER tip que corrige uma palavra (ex.: "miting" → "meeting", "wik" → "week") → nota DEVE ser < {pass_score}% (REPROVADO). Aprovado (nota >= {pass_score}%) = tips = [] ou só encorajamento geral, sem correção de palavra.
- 100: SOMENTE quando acertar TUDO — frase correta (transcript = esperada), pronúncia e clareza boas. Tips = []. "Frase correta."
- 90–99: frase correta, pequenos desvios aceitáveis. Tips = [] ou no máximo 1 encorajadora (sem corrigir palavra). (aprovado.)
- 85–89: frase correta, pronúncia aceitável. Tips = [] ou no máximo 1 encorajamento geral. (aprovado.)
- 70–84: maioria das palavras certas, sem necessidade de corrigir palavra nas tips. Tips = [] ou 1 encorajamento geral. (aprovado.) Se incluir tip de correção de palavra (miting→meeting, etc.), nota < {pass_score}% (reprovado).
- 50–69: várias palavras erradas ou faltando; 2+ tips de correção. (reprovado.)
- 30–49: muitas palavras erradas/faltando, frase bem diferente da esperada. (reprovado.)
- 0–29: quase nada correto ou incompreensível. (reprovado.)
Compare palavra por palavra (transcript vs frase esperada). Aluno é iniciante: entonação pouco relevante; palavras erradas/faltando devem baixar a nota.

ORGANIZAÇÃO DO FEEDBACK (OBRIGATÓRIO):
- feedback_ui: se nota >= {pass_score}% (aprovado), feedback pode ser positivo; se nota < {pass_score}% (reprovado), feedback deve ser negativo ("Ajuste a pronúncia.", "A frase não está correta.", etc.).
- feedback_tts: orientação PRINCIPAL (1 frase curta); use [[palavra]] para cada palavra em inglês citada.
- tips: UMA dica por palavra errada; use [[palavra]]. Se incluir QUALQUER tip que corrige palavra (ex.: miting→meeting), nota DEVE ser < {pass_score}% (reprovado). Aprovado = tips = [] ou só 1 incentivo geral (sem correção). Até 8 itens.

9. NUNCA mencione "próximo exercício" ou "curso concluído" - o app já cuida disso
10. Responda SOMENTE em JSON válido, sem markdown
11. Exemplo (use [[next]] para a palavra em inglês):
{{"feedback_ui":"🔴 A frase mudou de sentido.","feedback_tts":"A palavra [[next]] não foi dita e isso muda o significado.","tips":["A palavra [[next]] não foi dita e isso muda o significado.","A palavra que você usou não funciona nesse contexto. Use a palavra [[next]] para tempo futuro."],"scoreAi":40 }}

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


def _parse_tts_segments(text: str) -> list[tuple[str, Literal["pt", "en"]]]:
    """Splits text by [[word]] markers; returns list of (segment_text, "pt"|"en")."""
    parts = re.split(r"(\[\[.*?\]\])", text)
    segments: list[tuple[str, Literal["pt", "en"]]] = []
    for part in parts:
        if not part:
            continue
        if part.startswith("[[") and part.endswith("]]"):
            segments.append((part[2:-2].strip(), "en"))
        else:
            segments.append((part, "pt"))
    return segments


def _synthesize_mixed_tts(segments: list[tuple[str, Literal["pt", "en"]]]) -> bytes | None:
    """Synthesizes PT segments with gTTS and EN segments with OpenAI TTS; concatenates to one MP3."""
    try:
        from pydub import AudioSegment
    except ImportError:
        return None
    try:
        from gtts import gTTS
    except ImportError:
        return None

    out = AudioSegment.empty()
    for text, lang in segments:
        if not text.strip():
            continue
        if lang == "pt":
            buf = BytesIO()
            gTTS(text=text, lang="pt", lang_check=False).write_to_fp(buf)
            buf.seek(0)
            seg_audio = AudioSegment.from_mp3(buf)
        else:
            resp = client.audio.speech.create(
                model=TTS_MODEL,
                voice=TTS_VOICE,
                input=text,
                response_format="mp3",
            )
            seg_audio = AudioSegment.from_mp3(BytesIO(resp.content))
        out += seg_audio

    buf_out = BytesIO()
    out.export(buf_out, format="mp3", bitrate="128k")
    return buf_out.getvalue()


@app.post("/tts")
async def tts(request: Request):
    try:
        data = await request.json()
        text = (data.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "Texto vazio"}, status_code=400)

        # Mixed PT/EN: [[word]] = English pronunciation, rest = Portuguese
        if "[[" in text and "]]" in text:
            segments = _parse_tts_segments(text)
            mixed = _synthesize_mixed_tts(segments)
            if mixed is not None:
                audio_b64 = base64.b64encode(mixed).decode("utf-8")
                return {"audio_base64": audio_b64, "mime": "audio/mpeg"}
            # fallback: strip markers and use single OpenAI TTS
            text = re.sub(r"\[\[(.*?)\]\]", r"\1", text)
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