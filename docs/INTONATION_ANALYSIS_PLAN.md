# Análise e plano: avaliação de entonação no áudio

## Situação atual (atualizado)

- **Fluxo unificado (principal):** O áudio (WAV) é enviado ao modelo **gpt-audio** (Chat Completions com `input_audio`). O modelo **ouve** o áudio e devolve transcrição literal + avaliação (nota, feedback, dicas). A avaliação de **pronúncia e entonação** é feita **pelo próprio modelo** a partir do sinal de áudio — não há extração explícita de pitch/ritmo no backend; o prompt pede que a nota considere "transcript + pronúncia + entonação". Ou seja: entonação **é** considerada no fluxo unificado, de forma implícita (o modelo treinado em fala julga a partir do áudio).
- **Fluxo fallback:** Quando o unificado falha (conversão WAV ou API), usamos Transcriptions (Whisper) + Chat Completions (só texto). Nesse caso **não** há áudio na avaliação — só transcript vs frase esperada; entonação não é avaliada.

**Resumo:** No fluxo principal, entonação é avaliada implicitamente pelo gpt-audio. Não há (ainda) módulo separado de análise acústica (librosa, etc.) no projeto.

---

## Limitações das APIs atuais

| Fonte | Entonação / prosódia |
|-------|----------------------|
| **OpenAI Whisper / Transcriptions** | Não expõe pitch, prosódia nem métricas de fluência. Apenas texto. |
| **OpenAI Realtime** | Focado em tempo real; não há documentação de métricas de entonação. |

Conclusão: **Whisper/Transcriptions** não expõem entonação. **gpt-audio** (Chat Completions com áudio) avalia entonação de forma implícita ao "ouvir" o áudio. Para métricas **objetivas/quantitativas** de entonação (pitch, ritmo), seria preciso analisar o sinal no backend (ex.: librosa).

---

## Abordagens viáveis

### 1. Extrair prosódia no backend (recomendada)

**Ideia:** No servidor, além do STT, rodar uma análise acústica do mesmo áudio e usar o resultado para:
- calcular um **score de entonação** (0–100) por heurísticas, e/ou
- passar um **resumo textual** (ex.: "Variação de pitch: baixa. Ritmo: normal.") para o GPT incluir no feedback e no `scoreAi`.

**Métricas úteis (fáceis de extrair):**

| Métrica | O que indica | Uso no feedback |
|--------|----------------|------------------|
| **Variação de pitch (F0)** | Monotonia vs entonação viva | "Tente variar mais a entonação." |
| **Energia / RMS** | Volume e ênfase ao longo do tempo | "Fale um pouco mais alto/mais estável." |
| **Duração total** | Ritmo (muito rápido/lento) | "Fale um pouco mais devagar." |
| **Pausas (silêncios)** | Fluência / hesitação | "Menos pausas no meio da frase." |

**Bibliotecas em Python:**

- **librosa**: `librosa.pyin()` para F0 (pitch), `librosa.feature.rms()` para energia; `librosa.load()` pode carregar WebM se houver ffmpeg/audioread. Já é comum em projetos de áudio.
- **Parselmouth** (Praat em Python): pitch e intensidade muito precisos; em geral espera WAV (ou arquivo). Pode exigir conversão WebM → WAV (ex.: pydub + ffmpeg).

**Fluxo sugerido (futuro, se quiser métricas objetivas):**

1. O fluxo atual já usa gpt-audio (unificado) para avaliar áudio; opcionalmente pode-se **enriquecer** com prosódia extraída.
2. **Novo:** Carregar o mesmo arquivo de áudio (ou bytes) com librosa (e, se necessário, converter WebM → WAV com pydub).
3. **Novo:** Extrair F0 (pyin), RMS e duração; opcionalmente detectar silêncios (energia abaixo de um limiar).
4. **Novo:** Calcular um **intonation_score** (0–100) por regras simples, por exemplo:
   - boa variação de pitch → contribui positivamente;
   - ritmo muito rápido/lento → penalidade leve;
   - muitas pausas longas → penalidade leve.
5. **Novo:** Montar uma string de contexto para o GPT, ex.:  
   `Prosódia: variação de pitch=baixa, ritmo=normal, pausas=poucas.`
6. Incluir esse contexto no prompt de `generate_personalized_feedback` e, no mesmo prompt, pedir que o feedback e o `scoreAi` considerem **também a entonação/prosódia** (e que evite ser crítico demais com iniciantes).
7. Combinar scores: por exemplo `score_final = 0.7 * score_transcript + 0.3 * intonation_score`, ou deixar o GPT usar o texto de prosódia para ajustar um único `scoreAi` (mais simples de manter).

**Prós:** Não depende de nova API paga; reutiliza o áudio que já existe; controle total dos critérios.  
**Contras:** Requer dependências (librosa, talvez pydub/ffmpeg); heurísticas precisam ser afinadas (limiares de “monotonia”, “muitas pausas”, etc.).

---

### 2. Usar Azure Pronunciation Assessment

- API da Microsoft que devolve **accuracy**, **fluency**, **completeness** e **overall**.
- Fluência captura parte do que consideramos “ritmo/flow”; não é estritamente “curva de pitch”, mas ajuda.
- Exige conta Azure, Speech resource e SDK `azure-cognitiveservices-speech`; áudio em formato suportado (ex.: WAV/streaming).

**Prós:** Métricas prontas, bem documentadas.  
**Contras:** Novo provedor, custo, e possível conversão WebM → formato suportado.

---

### 3. Híbrido (OpenAI + prosódia local)

- Manter STT + GPT como hoje.
- Adicionar o módulo de prosódia com librosa (como no item 1) para **só enriquecer o prompt** e, opcionalmente, um segundo score numérico.
- Se no futuro quiser mais “pronunciation/fluency” prontos, pode adicionar Azure como canal opcional (ex.: por feature flag).

---

## Recomendação

- **Hoje:** O fluxo unificado (gpt-audio) já considera entonação na avaliação (o modelo ouve o áudio). Não há extração explícita de prosódia.
- **Opcional (futuro):** Se quiser **métricas objetivas/quantitativas** de entonação (ex.: relatório "variação de pitch: baixa"), implementar a **opção 1 (prosódia no backend com librosa)**:
  1. Adicionar `librosa` (e, se necessário, `soundfile`/`audioread`) ao `requirements.txt`; garantir ffmpeg ou pydub para WebM → WAV.
  2. Criar função `extract_prosody_summary(audio_path_or_bytes, mime) -> dict` retornando `pitch_variation`, `speaking_rate`, `pauses`, `intonation_score`.
  3. No fluxo unificado ou no fallback, chamar `extract_prosody_summary` e passar o resumo para o prompt do GPT (ou combinar `scoreAi` com `intonation_score` no código).

Assim o documento fica alinhado ao estado atual (avaliação implícita via gpt-audio) e o plano de prosódia explícita permanece como melhoria futura.
