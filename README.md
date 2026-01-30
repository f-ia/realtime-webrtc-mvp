# Speaking Practice — English Evaluation

Aplicação web para prática de speaking em inglês: o usuário grava a fala, o sistema transcreve literalmente, avalia pronúncia/entonação/gramática e devolve feedback com dicas (e áudio TTS opcional).

---

## Funcionamento

1. **Frases:** O app exibe frases em inglês (com tradução). O usuário pode ouvir a pronúncia correta (TTS).
2. **Gravação:** O usuário clica em START, grava no microfone e para com STOP.
3. **Avaliação:** O áudio é enviado ao servidor. O fluxo principal usa **gpt-audio** (OpenAI): o modelo ouve o áudio, transcreve **literalmente** (sem corrigir) e avalia pronúncia, entonação e gramática em relação à frase esperada.
4. **Resultado:** Nota (0–100), feedback na tela, dicas (tips) e áudio do feedback (TTS). Meta de aprovação configurável (ex.: 90%).

Se o fluxo principal falhar (ex.: conversão de áudio ou API), o servidor usa **fallback**: transcrição com Whisper + avaliação por texto (sem áudio). O frontend indica quando o fallback foi usado.

---

## Uso

### Pré-requisitos

- Python 3.9+
- Chave da API OpenAI (`OPENAI_API_KEY`)

### Instalação

```bash
# Clone ou acesse o projeto
cd realtime-webrtc-mvp

# Crie o ambiente virtual e ative
python -m venv venv
# Windows: venv\Scripts\activate
# Linux/macOS: source venv/bin/activate

# Instale dependências
pip install -r requirements.txt

# Configure o .env (copie de .env.example se existir)
# Defina OPENAI_API_KEY=sk-...
```

### Executar

```bash
uvicorn main:app --reload
```

Acesse: **http://127.0.0.1:8000**

- **START** — Carrega as frases e inicia o exercício.
- **🔊** — Ouvir pronúncia correta da frase.
- **START (gravar)** / **STOP** — Gravar e enviar para avaliação.
- **RETRY** / **PRÓXIMO** — Repetir a frase ou avançar.

### Variáveis de ambiente (opcional)

| Variável           | Padrão   | Descrição                          |
|--------------------|----------|------------------------------------|
| `OPENAI_API_KEY`   | —        | **Obrigatório.** Chave OpenAI.     |
| `PASS_SCORE`       | `90`     | Nota mínima para passar (0–100).   |
| `TTS_VOICE`        | `alloy`  | Voz do TTS (alloy, echo, nova, …).  |
| `ENVIRONMENT`      | `prod`   | `dev` exibe a caixa de logs na UI. |

Outras opções (modelos, etc.) estão em `docs/SYSTEM_OVERVIEW.md`.

---

## Documentação

- **`docs/SYSTEM_OVERVIEW.md`** — Visão geral do sistema: arquitetura, fluxo de avaliação (unificado e fallback), APIs, variáveis de ambiente e frontend.
