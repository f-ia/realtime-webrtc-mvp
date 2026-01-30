# Análise: Completions vs Realtime vs TTS para avaliação de fala por professor IA

## Proposta do projeto

- Aluno grava um áudio falando uma frase em inglês (ex.: "My birthday is next week.").
- Um "professor de inglês IA" avalia: transcrição, comparação com a frase esperada, pronúncia, nota (0–100), feedback em texto e áudio.
- O feedback em áudio deve ser em português do Brasil (com palavras da frase em inglês citadas em inglês).

---

## O que cada API faz

| API | Função | Entrada | Saída |
|-----|--------|---------|--------|
| **Chat Completions** | Modelo de linguagem (texto e/ou áudio) que “pensa” e gera texto estruturado (ex.: JSON). | Texto e/ou áudio (ex.: gpt-audio). | Texto (ex.: transcript + feedback_ui + feedback_tts + tips + scoreAi). |
| **Transcriptions** | Apenas reconhecer fala (speech-to-text). | Áudio. | Texto (transcrição). |
| **TTS (Text-to-Speech)** | Converter texto em áudio. | Texto. | Áudio (ex.: MP3). |
| **Realtime API** | Conversa em tempo real por WebSocket: usuário fala, assistente responde com áudio/texto em streaming. | Áudio em streaming + estado da conversa. | Áudio e/ou texto em streaming, turn-taking. |

---

## Por que usamos **Completions** (e não Realtime) para a avaliação

- O fluxo do app é **um ciclo por frase**: gravar → enviar áudio completo → receber **uma** avaliação estruturada (transcript, nota, feedback, dicas) → falar o feedback.
- Isso é um **request/response único** com saída **estruturada** (JSON): exatamente o que **Chat Completions** oferece quando recebe áudio (ex.: gpt-audio) + prompt de “professor de inglês”.
- **Realtime API** é pensada para:
  - **Conversa contínua** (vários turnos, ida e volta).
  - **WebSocket**, áudio em **streaming**, detecção de turn-taking, resposta em áudio em tempo real.
  - Casos como: atendimento por voz, tutor que conversa em tempo real, assistente que fala e escuta ao vivo.
- Para “avaliar **um** áudio gravado e devolver **um** JSON de avaliação”, Realtime seria:
  - Mais complexo (WebSocket, estado, streaming).
  - Menos alinhado ao formato (não é voltado a “retornar um JSON de avaliação” por clip).
  - Melhor quando o objetivo é **conversa ao vivo**, não **avaliação sob demanda** de um trecho já gravado.

**Conclusão:** para “professor IA que **avalia** a fala do aluno (uma frase por vez) e devolve nota + feedback estruturado”, **Completions (com áudio)** é o cenário adequado. Realtime é o cenário adequado para “professor IA que **conversa** em tempo real com o aluno”.

---

## Por que usamos **TTS** separado

- Completions (e Realtime) podem até gerar **texto** para o assistente falar, mas **não** geram o arquivo de áudio do feedback.
- Quem gera o áudio a partir do texto é a API **TTS** (Text-to-Speech): recebe o texto do feedback (em pt-BR + palavras em inglês citadas) e devolve MP3.
- Por isso o fluxo é: **Completions** gera o texto do feedback → **TTS** transforma esse texto em áudio que o app reproduz.

---

## Resumo: melhor cenário para este projeto

| Necessidade | API usada | Motivo |
|-------------|-----------|--------|
| Avaliar o áudio da frase (transcrição + comparação + nota + feedback em texto) | **Chat Completions** (com áudio, ex.: gpt-audio) | Request/response único, saída estruturada (JSON), modelo “professor” que ouve e avalia. |
| Fallback quando o fluxo unificado falha (ex.: conversão WAV ou API) | **Transcriptions** + **Chat Completions** (só texto) | O servidor tenta o unificado duas vezes; se falhar, transcreve com Transcriptions e Completions avalia só o texto (sem áudio). |
| Falar o feedback para o aluno (áudio em pt-BR) | **TTS** | Completions não gera áudio; TTS gera o áudio a partir do texto do feedback. |
| Conversa ao vivo com o aluno (não é o caso atual) | Realtime API | Seria outro produto: tutor que fala e escuta em tempo real, não “avaliar um clip e devolver JSON”. |

---

## Quando considerarmos Realtime no futuro

- Se a proposta evoluir para **sessão ao vivo** com o professor IA (aluno fala, professor responde falando na hora, vários turnos).
- Nesse cenário: Realtime para a conversa; a **avaliação** de uma frase específica ainda pode ser feita com Completions (enviando o trecho de áudio correspondente) se quiserem manter o mesmo formato de feedback estruturado (nota, dicas, etc.).

Em resumo: para **análise do áudio de um aluno falando em inglês por um professor de inglês IA** que devolve **uma avaliação por frase** (transcrição, nota, feedback, dicas), o cenário atual — **Completions (com áudio) + TTS** — é o mais adequado; Realtime entra se o foco for **conversa em tempo real**, não avaliação sob demanda de um trecho gravado.

---

## Onde e como pronúncia e entonação são avaliadas

| Fluxo | Onde | Como |
|-------|------|------|
| **Unificado (gpt-audio)** | Em `evaluate_speech_unified()`: o áudio (WAV em base64) é enviado no `content` da mensagem como `input_audio` para o modelo **gpt-audio**. | O modelo **recebe o áudio** e “ouve” o sinal. Não há código nosso que extraia pitch, energia ou prosódia (ex.: librosa). A avaliação de pronúncia e entonação é **implícita**: o prompt pede que a nota considere “palavras + pronúncia + entonação”, e o modelo usa o que “ouviu” para responder. **Como ele sabe inglês:** o gpt-audio foi treinado com dados de fala (incluindo inglês) e aprendeu a julgar qualidade de pronúncia/entonação a partir do áudio. Não há análise acústica no backend (ex.: pitch com librosa); o prompt passa regras fonéticas/gramaticais (th, r, vogais, linking, schwa, concordância, tempos, etc.) e o modelo usa o áudio + essas regras para avaliar. |
| **Fallback (Transcriptions + Completions)** | Em `generate_personalized_feedback()`: só entra o **texto** (transcript). O Completions **não recebe áudio**. | **Não** avaliamos pronúncia nem entonação no fallback. Só comparamos transcript vs frase esperada (palavras certas/erradas). O prompt diz “entonação pouco relevante” porque não temos sinal de áudio nesse fluxo. |

**Resumo:** A única avaliação de pronúncia e entonação acontece no **fluxo unificado**, dentro do modelo **gpt-audio**, que recebe o áudio e é instruído pelo prompt. Não há módulo separado de análise acústica no projeto. Para avaliação mais objetiva de entonação (ex.: pitch, ritmo), seria necessário implementar algo como no `docs/INTONATION_ANALYSIS_PLAN.md` (ex.: librosa/parselmouth).
