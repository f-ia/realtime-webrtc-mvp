const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const audioEl = document.getElementById("audio");
const startBtn = document.getElementById("start");
const stopBtn = document.getElementById("stop");

let pc = null;
let dc = null;
let micStream = null;

function setStatus(text, cls = "ready") {
  statusEl.textContent = text;
  statusEl.className = `status-text ${cls}`;
}

function log(msg, isErr=false) {
  const div = document.createElement("div");
  div.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  if (isErr) div.className = "err";
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

async function start() {
  startBtn.disabled = true;
  stopBtn.disabled = false;
  logEl.innerHTML = "";
  setStatus("Initializing...", "connecting");

  try {
    pc = new RTCPeerConnection({
      iceServers: [{ urls: ["stun:stun.l.google.com:19302"] }],
    });

    pc.onconnectionstatechange = () => {
      log(`pc.connectionState=${pc.connectionState}`);
    };

    pc.ontrack = (e) => {
      audioEl.srcObject = e.streams[0];
      setStatus("Listening / Playing audio...", "listening");
      log("Received remote audio track");
    };

    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    pc.addTrack(micStream.getTracks()[0], micStream);
    log("Microphone track added");

    // DataChannel: só pra logar eventos (não dependemos dele pra tocar áudio)
    dc = pc.createDataChannel("oai-events");
    dc.onopen = () => log("DataChannel open");
    dc.onclose = () => log("DataChannel close");
    dc.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        log(`event: ${ev.type}`);
      } catch {
        log(`event(raw): ${e.data}`);
      }
    };

       const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    log("Created SDP offer");
    
    // DEBUG: verifica se o SDP tá completo
    console.log("SDP offer length:", offer.sdp.length);
     
    // SDP offer length:
    //1632

    console.log("SDP offer first 100 chars:", offer.sdp.substring(0, 100));

    // SDP offer first 100 chars: 
    // v=0
    // o=- 1890451027434753848 2 IN IP4 127.0.0.1
    // s=-
    // t=0 0
    // a=group:BUNDLE 0 1
    // a=extmap-allow-mixe

    setStatus("Calling /session ...", "connecting");
    
    // BACKUP
    // const resp = await fetch("/session", {
    //   method: "POST",
    //   headers: { "Content-Type": "application/sdp" },
    //   body: offer.sdp,  // ← Tem que ser STRING, não objeto
    // });

    const resp = await fetch("/session", {
      method: "POST",
      body: offer.sdp,
    });

    if (!resp.ok) {
      const errText = await resp.text();
      let errMsg = errText;
      try {
        const errJson = JSON.parse(errText);
        errMsg = errJson.error?.message || errJson.details || errText;
      } catch {}
      throw new Error(errMsg);
    }

    const answerSdp = await resp.text();
    log(`Got answer SDP (${answerSdp.length} chars)`);
    
    await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
    log("Applied SDP answer");


    setStatus("Speak now (server VAD)", "ready");
  } catch (err) {
    log(`ERROR: ${err.message}`, true);
    setStatus(`Error: ${err.message}`, "error");
    stop();
  }
}

function stop() {
  try { if (dc) dc.close(); } catch {}
  try { if (pc) pc.close(); } catch {}
  try { if (micStream) micStream.getTracks().forEach(t => t.stop()); } catch {}

  dc = null;
  pc = null;
  micStream = null;

  startBtn.disabled = false;
  stopBtn.disabled = true;
  setStatus("Stopped", "ready");
  log("Stopped");
}

startBtn.onclick = start;
stopBtn.onclick = stop;
