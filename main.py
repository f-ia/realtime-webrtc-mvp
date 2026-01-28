import json
import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("OPENAI_MODEL", "gpt-realtime-2025-08-25")
VOICE = os.getenv("OPENAI_VOICE", "alloy")

if not API_KEY:
    raise ValueError("OPENAI_API_KEY não definida no .env")

app = FastAPI()

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

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/session")
async def create_call(request: Request):
    # Recebe e decodifica o SDP offer do cliente
    sdp_offer = (await request.body()).decode("utf-8")

    print(f"DEBUG: SDP length = {len(sdp_offer)} chars")
    print("DEBUG: SDP first 80 chars:")
    print(sdp_offer[:80])

    # Valida que o SDP começa com o header correto
    if not sdp_offer.startswith("v=0"):
        return JSONResponse({"error": "Invalid SDP"}, status_code=400)

    # Isso é importante para que o SDP seja valido e possa ser usado pela OpenAI Realtime API
    if not sdp_offer.endswith("\r\n"):
        sdp_offer += "\r\n"

    # Configuração da sessão Realtime que será usada pela OpenAI Realtime API
    session_config = {
        "type": "realtime",
        "model": MODEL,
        "output_modalities": ["audio"],
        "instructions": (
            "You are an English speaking tutor. "
            "Be direct and short. "
            "Correct pronunciation and grammar. "
            "Ask the user to repeat the corrected sentence."
        ),
        "audio": {
            "input": {
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 200,
                    "create_response": True,
                    "interrupt_response": True,
                }
            },
            "output": {"voice": VOICE},
        },
    }

    # Prepara o multipart/form-data conforme a documentação da OpenAI Realtime API
    # Os dois campos devem ser enviados como "files" com os tipos de conteúdo apropriados
    files = {
        "session": (None, json.dumps(session_config), "application/json"),
        "sdp": (None, sdp_offer, "application/sdp")
    }

    try:
        # Envia o SDP offer e a configuração da sessão para a OpenAI Realtime API
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.openai.com/v1/realtime/calls",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                },
                files=files,
            )

        print("DEBUG: OpenAI status =", r.status_code)

        # Trata erros da API retornando os detalhes do erro para o cliente
        if r.status_code not in (200, 201):
            print("DEBUG: OpenAI error body =", r.text)
            return JSONResponse(
                {"error": "OpenAI API error", "details": r.text},
                status_code=r.status_code,
            )

        # Retorna o SDP answer da OpenAI para completar a handshake WebRTC
        return Response(
            content=r.text,
            media_type="application/sdp",
        )

    except Exception as e:
        print("DEBUG: Backend exception:", str(e))
        return JSONResponse(
            {"error": f"Backend error: {str(e)}"},
            status_code=500,
        )