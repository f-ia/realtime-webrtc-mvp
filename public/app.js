let pc = null;
let dc = null;
let stream = null;

let phrases = [];
let currentIndex = 0;

let buffer = "";
let isEvaluating = false;
let armed = false;
let connected = false;

const log = (msg) => {
  document.querySelector("#logs").innerHTML += msg + "<br>";
};

async function loadPhrases() {
  const res = await fetch("/phrases");
  const data = await res.json();
  phrases = data.phrases || [];
  currentIndex = 0;
  showPhrase();
}

function showPhrase() {
  if (currentIndex >= phrases.length) {
    log("🎉 Sessão concluída!");
    return;
  }

  const phrase = phrases[currentIndex];
  document.querySelector("#phrase").innerText = phrase.text;
  document.querySelector("#translation").innerText = phrase.translation;
  log(`📖 Frase ${currentIndex + 1}: ${phrase.text}`);
}

async function ensureConnected() {
  if (connected) return;

  pc = new RTCPeerConnection();
  dc = pc.createDataChannel("oai-events");

  dc.onopen = () => {
    connected = true;
    log("🎧 Realtime conectado.");

    // habilita VAD e garante que a IA não responda sozinha [web:19]
    dc.send(JSON.stringify({
      type: "session.update",
      session: {
        modalities: ["text", "audio"],
        turn_detection: { type: "server_vad", create_response: false },
        instructions: "Responda em PT-BR. Quando solicitado, retorne SOMENTE JSON válido.",
      }
    }));

    log("✅ Configurado. Clique FALAR AGORA.");
  };

  dc.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    handleRealtimeEvent(msg);
  };

  stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  stream.getTracks().forEach((track) => pc.addTrack(track, stream));

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  const sdpToSend = pc.localDescription?.sdp || "";
  log(`🧪 SDP local len=${sdpToSend.length}`);

  const resp = await fetch("/rtc/offer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sdp: sdpToSend }),
  });

  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error || "Falha no SDP");

  await pc.setRemoteDescription({ type: "answer", sdp: data.sdp });
}

function handleRealtimeEvent(event) {
  // VAD events: quem manda é o servidor [web:84]
  if (event.type === "input_audio_buffer.speech_started") {
    if (!armed) return;
    log("🎤 Detectei fala...");
    return;
  }

  if (event.type === "input_audio_buffer.speech_stopped") {
    if (!armed) return;
    armed = false;
    log("⏹️ Fim da fala. Avaliando...");
    startEvaluation(); // agora é no momento certo
    return;
  }

  if (event.type === "response.text.delta") {
    buffer += event.delta || "";
    return;
  }

  if (event.type === "response.done") {
    const text = buffer.trim();
    buffer = "";
    isEvaluating = false;

    const obj = safeJson(text);
    if (!obj) {
      log("⚠️ JSON inválido. Tente novamente.");
      return;
    }

    log("🧠 " + obj.feedback);
    if (obj.result === "pass") nextExercise();
    else log("🔁 Tente novamente.");
  }
}

function safeJson(text) {
  try { return JSON.parse(text); } catch {}
  const a = text.indexOf("{"), b = text.lastIndexOf("}");
  if (a >= 0 && b > a) {
    try { return JSON.parse(text.slice(a, b + 1)); } catch {}
  }
  return null;
}

function startEvaluation() {
  if (!dc || dc.readyState !== "open" || isEvaluating) return;
  isEvaluating = true;

  const phrase = phrases[currentIndex];

  dc.send(JSON.stringify({
    type: "response.create",
    response: {
      modalities: ["text", "audio"],
      instructions: `
Você é um avaliador de pronúncia de inglês para brasileiros.

Frase esperada (inglês): "${phrase.text}"

Regras:
- NÃO repita a frase esperada.
- O áudio deve ser só feedback curto em PT-BR.
- O TEXTO deve ser SOMENTE JSON válido (sem markdown e sem texto fora do JSON).

Retorne:
{"result":"pass|fail","feedback":"comentário curto em português","heard":"o que você entendeu","tips":["até 3 dicas"],"scoreAi":0-100}
`
    }
  }));
}

function nextExercise() {
  currentIndex++;
  showPhrase();
}

window.addEventListener("DOMContentLoaded", () => {
  const startBtn = document.querySelector("#startBtn");
  const talkBtn = document.querySelector("#talkBtn");

  startBtn.onclick = async () => {
    await loadPhrases();
    await ensureConnected();
  };

  talkBtn.onclick = async () => {
    await ensureConnected();
    if (!dc || dc.readyState !== "open") {
      log("⚠️ Ainda conectando...");
      return;
    }
    armed = true;
    log("✅ Pode falar agora.");
  };
});
