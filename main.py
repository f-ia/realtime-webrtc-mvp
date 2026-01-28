import json
import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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

app.mount("/public", StaticFiles(directory="public"), name="public")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/session")
async def create_call(request: Request):
    """
    Recebe SDP offer do browser e cria uma Realtime Call.
    """
    try:
        # Tenta ler o body como bytes primeiro
        body = await request.body()
        sdp_offer = body.decode('utf-8').strip()
        
        # Debug
        print(f"DEBUG: SDP length = {len(sdp_offer)} chars")
        print(f"DEBUG: SDP first 100 chars = {sdp_offer[:100]}")
        
        if not sdp_offer or len(sdp_offer) < 50:
            return JSONResponse(
                {"error": f"SDP inválido (length={len(sdp_offer)})"},
                status_code=400
            )
    except Exception as e:
        return JSONResponse({"error": f"Failed to parse body: {str(e)}"}, status_code=400)

    # BACKUP
    # session = {
    #     "type": "realtime",
    #     "model": MODEL,
    #     "output_modalities": ["audio"],
    #     "instructions": (
    #         "You are an English speaking tutor. "
    #         "Be direct and short. "
    #         "Correct pronunciation and grammar. "
    #         "Ask the user to repeat the corrected sentence."
    #     ),
    #     "audio": {
    #         "input": {
    #             "turn_detection": {
    #                 "type": "server_vad",
    #                 "threshold": 0.5,
    #                 "prefix_padding_ms": 300,
    #                 "silence_duration_ms": 200,
    #                 "create_response": True,
    #                 "interrupt_response": True,
    #             }
    #         },
    #         "output": {
    #             "voice": VOICE,
    #         },
    #     },
    # }

    # files = [
    #     ("sdp", ("offer.sdp", sdp_offer.encode(), "application/sdp")),
    #     ("session", ("session.json", json.dumps(session).encode(), "application/json")),
    # ]

    payload = {
        "sdp": sdp_offer,
        "session": {
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
        },
    }

    print(payload)
    
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.openai.com/v1/realtime/calls",
                headers={"Authorization": f"Bearer {API_KEY}"},
                files=files,
            )

        print(f"DEBUG: OpenAI response status = {r.status_code}")
        
        if r.status_code not in (200, 201):
            print(f"DEBUG: Error body = {r.text}")
            return JSONResponse(
                {"error": f"OpenAI error {r.status_code}", "details": r.text},
                status_code=r.status_code,
            )

        return Response(content=r.text, media_type="application/sdp")

    except Exception as e:
        print(f"DEBUG: Exception = {str(e)}")
        return JSONResponse(
            {"error": f"Backend error: {str(e)}"},
            status_code=500,
        )

    