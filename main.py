import os
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY2") or os.getenv("OPENAI_API_KEY")
REALTIME_MODEL = os.getenv("REALTIME_MODEL", "gpt-4o-realtime-preview")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/public", StaticFiles(directory="public"), name="public")

@app.get("/")
def root():
    return FileResponse("public/index.html")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/phrases")
def phrases():
    return {
        "phrases": [
            {"id": 1, "text": "My birthday is next week.", "translation": "Meu aniversário é semana que vem.", "difficulty": "Easy"},
            {"id": 2, "text": "I like to drink water in the morning.", "translation": "Eu gosto de beber água pela manhã.", "difficulty": "Easy"},
            {"id": 3, "text": "I loved meeting my new friend.", "translation": "Eu adorei conhecer meu novo amigo.", "difficulty": "Easy"},
        ]
    }

@app.get("/session")
async def session():
    """
    Gera token efêmero para o browser conectar direto no Realtime
    """
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.openai.com/v1/realtime/sessions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": REALTIME_MODEL,
                "modalities": ["audio", "text"],
            },
        )
    return JSONResponse(r.json(), status_code=r.status_code)
