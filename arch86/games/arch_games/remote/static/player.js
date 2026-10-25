(function () {
  'use strict';
  var bar = document.getElementById('audio-bar');
  var vm = bar.dataset.vm;
  var els = {};
  ['connect', 'disconnect', 'mute', 'volume', 'status', 'latency', 'error', 'token', 'tokenrow'].forEach(function (id) { els[id] = document.getElementById(id); });
  var hash = new URLSearchParams(location.hash.slice(1));
  var token = hash.get('token') || '';
  if (token) { history.replaceState(null, '', location.pathname + location.search); }  // keep it out of history/referrers
  if (!token) { els.tokenrow.hidden = false; }
  var ctx = null, gain = null, ws = null, cfg = null, nextTime = 0, muted = false, frames = 0, stopping = false;
  var TARGET = 0.12, MAX_BUFFER = 0.6;

  function status(text, level) { els.status.textContent = text; els.status.dataset.level = level || 'warn'; }
  function error(text) { els.error.textContent = text || ''; }
  function ui(connected) {
    els.connect.disabled = connected; els.disconnect.disabled = !connected; els.mute.disabled = !connected;
  }
  function applyGain() { if (gain) { gain.gain.value = muted ? 0 : Math.pow(els.volume.value / 100, 2); } }

  function play(buffer) {
    var samples = new Int16Array(buffer.buffer || buffer, buffer.byteOffset || 0, Math.floor(buffer.byteLength / 2));
    var ch = cfg.channels, n = Math.floor(samples.length / ch);
    if (!n) { return; }
    var ab = ctx.createBuffer(ch, n, cfg.rate);
    for (var c = 0; c < ch; c++) {
      var out = ab.getChannelData(c);
      for (var i = 0; i < n; i++) { out[i] = samples[i * ch + c] / 32768; }
    }
    var now = ctx.currentTime;
    if (nextTime < now + 0.02) { nextTime = now + TARGET; }                 // underrun: re-buffer
    if (nextTime - now > MAX_BUFFER) { return; }                          // too far behind: drop to catch up
    var src = ctx.createBufferSource();
    src.buffer = ab; src.connect(gain); src.start(nextTime);
    nextTime += ab.duration;
    frames++;
  }

  function connect() {
    error('');
    var t = token || els.token.value;
    if (!t) { error('Enter the access token (or open this page with #token=…).'); return; }
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) { error('This browser has no Web Audio support.'); return; }
    ctx = ctx || new AC({ latencyHint: 'interactive' });
    ctx.resume();                                                         // inside the click: satisfies autoplay policy
    gain = gain || ctx.createGain(); gain.connect(ctx.destination); applyGain();
    stopping = false; nextTime = 0; frames = 0;
    status('Connecting…', 'warn'); ui(true);
    var proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
    ws = new WebSocket(proto + location.host + '/vm/' + vm + '/ws');
    ws.binaryType = 'arraybuffer';
    ws.onopen = function () { ws.send(JSON.stringify({ type: 'auth', token: t })); };
    ws.onmessage = function (ev) {
      if (typeof ev.data === 'string') {
        var m = JSON.parse(ev.data);
        if (m.type === 'hello') { cfg = m; status('Streaming ' + m.vm + ' (' + m.rate + ' Hz)', 'ok'); }
        else if (m.type === 'error') { error(m.message); status('Stopped', 'bad'); }
        else if (m.type === 'stats' && m.dropped) { els.latency.textContent = 'dropped ' + m.dropped + ' frames'; }
      } else if (cfg) {
        play(new Uint8Array(ev.data));
        els.latency.textContent = 'buffer ' + Math.max(0, Math.round((nextTime - ctx.currentTime) * 1000)) + ' ms';
      }
    };
    ws.onclose = function () {
      ui(false);
      if (!stopping && els.error.textContent === '') { error('Connection closed by the host.'); }
      status('Not connected', 'warn'); els.latency.textContent = ''; cfg = null; ws = null;
    };
    ws.onerror = function () { error('Could not reach the audio bridge.'); };
  }

  function disconnect() {
    stopping = true;
    if (ws) { try { ws.close(1000); } catch (e) {} }
    if (ctx) { ctx.suspend(); }
  }

  els.connect.addEventListener('click', connect);
  els.disconnect.addEventListener('click', disconnect);
  els.mute.addEventListener('click', function () { muted = !muted; els.mute.setAttribute('aria-pressed', muted); els.mute.textContent = muted ? 'Unmute' : 'Mute'; applyGain(); });
  els.volume.addEventListener('input', applyGain);
  window.addEventListener('pagehide', disconnect);                        // capture stops when the tab goes away
  window.__archAudio = { stats: function () { return { frames: frames, state: ctx && ctx.state, connected: !!ws, rate: cfg && cfg.rate }; } };
})();
