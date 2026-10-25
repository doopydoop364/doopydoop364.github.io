(function () {
  'use strict';
  var csrf = null, view = document.getElementById('view'), timers = [];
  var NAV = [['library', 'Library'], ['downloads', 'Downloads'], ['installs', 'Installs'], ['audio', 'Audio'], ['wine', 'Wine'], ['diagnostics', 'Diagnostics']];
  var TONE = { 'Verified working': 'ok', 'Working with issues': 'warn', 'Likely compatible': 'ok', 'Requires configuration': 'warn', 'Likely incompatible': 'bad', 'Unsupported': 'bad' };

  // DOM helper: text is only ever set via DOM text nodes (titles come from a remote server).
  function h(tag, attrs) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (k === 'class') el.className = v; else if (k === 'style') el.style.cssText = v;  /* CSSOM is allowed by the strict CSP; the style attribute is not */ else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) el.setAttribute(k, v === true ? '' : v);
    });
    (function add(list) { list.forEach(function (c) { if (c == null || c === false) return; if (Array.isArray(c)) return add(c); el.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c); }); })([].slice.call(arguments, 2));
    return el;
  }
  function toast(msg, bad) { var t = document.getElementById('toast'), d = h('div', { class: bad ? 'bad' : '' }, msg); t.appendChild(d); setTimeout(function () { d.remove(); }, bad ? 7000 : 3500); }
  function bytes(n) { if (n == null) return '?'; var u = ['B', 'KiB', 'MiB', 'GiB', 'TiB'], i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return (i ? n.toFixed(1) : n) + ' ' + u[i]; }
  function clear() { timers.forEach(clearInterval); timers = []; view.textContent = ''; }

  function api(method, path, body) {
    var opts = { method: method, credentials: 'same-origin', headers: {} };
    if (method !== 'GET') { opts.headers['Content-Type'] = 'application/json'; opts.headers['X-CSRF-Token'] = csrf || ''; opts.body = JSON.stringify(body || {}); }
    return fetch('/api/' + path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 401) { showLogin(); throw new Error('login'); }
        if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
        return j;
      });
    });
  }
  function act(btn, promise, ok) {
    btn.disabled = true;
    return promise.then(function (r) { if (ok) toast(ok); return r; }, function (e) { if (e.message !== 'login') toast(e.message, true); throw e; }).finally(function () { btn.disabled = false; });
  }
  function pollJob(job, onDone) {
    var t = setInterval(function () {
      api('GET', 'job/' + job.id).then(function (j) {
        if (j.state !== 'running') { clearInterval(t); if (j.state === 'failed') toast(j.message, true); else toast('Done'); onDone && onDone(j); }
      }).catch(function () { clearInterval(t); });
    }, 1000); timers.push(t);
  }

  function badge(text, tone) { return h('span', { class: 'badge ' + (tone || '') }, text); }
  function cover(g, big) {
    var box = h('div', { class: 'cover' }, big ? '🎮' : '🎮');
    if (g.cover) { var img = h('img', { src: '/api/cover/' + encodeURIComponent(g.cover), alt: '', loading: 'lazy' }); img.addEventListener('error', function () { img.remove(); }); box.appendChild(img); }
    return box;
  }

  // -- views ----------------------------------------------------------------------------------------------------------
  function showLogin() {
    clear();
    var input = h('input', { type: 'password', placeholder: 'Access token', 'aria-label': 'Access token', autocomplete: 'off' });
    var msg = h('p', { class: 'err' });
    function go() { api('POST', 'login', { token: input.value }).then(function (r) { csrf = r.csrf; route(); }).catch(function (e) { msg.textContent = e.message === 'login' ? 'Invalid token' : e.message; }); }
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') go(); });
    view.appendChild(h('div', { class: 'panel', style: 'max-width:460px;margin:60px auto' }, h('h1', {}, 'Sign in'),
      h('p', { class: 'muted' }, 'The token is in ' + 'the file web-token in your Arch Games config directory, and is printed by "arch-games web".'), input, h('p', {}, h('button', { class: 'primary', onclick: go }, 'Sign in')), msg));
    input.focus();
  }

  function libraryView() {
    var f = { q: '', source: 'all', status: '', installed: '', favorites: '', sort: 'title' };
    var q = h('input', { type: 'search', placeholder: 'Search name, alias, tag, source…', 'aria-label': 'Search' });
    function sel(name, opts) { var s = h('select', { 'aria-label': name }); opts.forEach(function (o) { s.appendChild(h('option', { value: o[0] }, o[1])); }); s.addEventListener('change', function () { f[name] = s.value; load(); }); return s; }
    var source = sel('source', [['all', 'All sources'], ['pacman', 'Pacman'], ['gamevault', 'GameVault'], ['local', 'Local']]);
    var status = sel('status', [['', 'Any compatibility'], ['Verified working', 'Verified working'], ['Working with issues', 'Working with issues'], ['Likely compatible', 'Likely compatible'], ['Requires configuration', 'Requires configuration'], ['Likely incompatible', 'Likely incompatible'], ['Unsupported', 'Unsupported'], ['Unknown', 'Unknown']]);
    var sort = sel('sort', [['title', 'Sort: title'], ['status', 'Sort: compatibility'], ['source', 'Sort: source'], ['category', 'Sort: category']]);
    var inst = h('input', { type: 'checkbox', 'aria-label': 'Installed only' }), favs = h('input', { type: 'checkbox', 'aria-label': 'Favorites only' });
    inst.addEventListener('change', function () { f.installed = inst.checked ? '1' : ''; load(); }); favs.addEventListener('change', function () { f.favorites = favs.checked ? '1' : ''; load(); });
    var all = h('input', { type: 'checkbox', 'aria-label': 'Show unverified GameVault entries' }); all.addEventListener('change', function () { f.all = all.checked ? '1' : ''; load(); });
    var grid = h('div', { class: 'grid' }), count = h('p', { class: 'muted' }), recent = h('div', { class: 'strip' }), t;
    q.addEventListener('input', function () { clearTimeout(t); t = setTimeout(function () { f.q = q.value; load(); }, 200); });
    clear();
    view.appendChild(h('h1', {}, 'Library'));
    view.appendChild(h('div', { class: 'bar' }, q, source, status, sort, h('label', {}, inst, ' Installed'), h('label', {}, favs, ' ★ Favorites'), h('label', {}, all, ' Show unverified')));
    view.appendChild(h('h2', {}, 'Recently played')); view.appendChild(recent); view.appendChild(count); view.appendChild(grid);
    api('GET', 'recent').then(function (r) { recent.textContent = ''; if (!r.length) recent.appendChild(h('span', { class: 'muted' }, 'Nothing played yet')); r.forEach(function (x) { recent.appendChild(h('a', { href: '#/game/' + encodeURIComponent(x.id) }, x.id + (x.exit_code ? ' (exit ' + x.exit_code + ')' : ''))); }); }).catch(function () {});
    function load() {
      var qs = Object.keys(f).filter(function (k) { return f[k]; }).map(function (k) { return k + '=' + encodeURIComponent(f[k]); }).join('&');
      api('GET', 'library?' + qs).then(function (r) {
        grid.textContent = ''; count.textContent = r.total + ' games' + (r.total > r.games.length ? ' (showing ' + r.games.length + ')' : '');
        r.games.forEach(function (g) {
          var fav = h('button', { class: 'fav', 'aria-pressed': g.favorite ? 'true' : 'false', title: 'Favorite', 'aria-label': 'Toggle favorite for ' + g.title });
          fav.textContent = g.favorite ? '★' : '☆';
          fav.addEventListener('click', function () { g.favorite = !g.favorite; fav.textContent = g.favorite ? '★' : '☆'; fav.setAttribute('aria-pressed', g.favorite); api('POST', 'favorite/' + encodeURIComponent(g.id), { value: g.favorite }).catch(function (e) { toast(e.message, true); }); });
          var c = cover(g); c.appendChild(fav);
          grid.appendChild(h('div', { class: 'card' }, c, h('div', { class: 'meta' }, h('h3', {}, h('a', { href: '#/game/' + encodeURIComponent(g.id) }, g.title)),
            badge(g.status, TONE[g.status]), g.installed ? badge('installed', 'ok') : null, badge(g.source))));
        });
        if (!r.games.length) grid.appendChild(h('p', { class: 'muted' }, 'No games match. Connect GameVault with "arch-games gamevault connect" or loosen the filters.'));
      }).catch(function (e) { if (e.message !== 'login') toast(e.message, true); });
    }
    load();
  }

  function kv(pairs) { var d = h('dl', { class: 'kv' }); pairs.forEach(function (p) { if (p[1] == null || p[1] === '') return; d.appendChild(h('dt', {}, p[0])); d.appendChild(h('dd', {}, p[1])); }); return d; }

  function gameView(id) {
    clear(); view.appendChild(h('p', { class: 'muted' }, 'Loading…'));
    function render() {
      api('GET', 'game/' + encodeURIComponent(id)).then(function (g) {
        clear();
        var fav = h('button', { onclick: function () { act(fav, api('POST', 'favorite/' + encodeURIComponent(id), { value: !g.favorite })).then(render); } }, g.favorite ? '★ Favorite' : '☆ Favorite');
        var buttons = h('div', { class: 'row' });
        if (g.launcher && g.launcher.exists) { var launch = h('button', { class: 'primary', onclick: function () { act(launch, api('POST', 'launch', { game: id }), 'Launching…').then(function (j) { pollJob(j, render); }); } }, 'Launch'); buttons.appendChild(launch); }
        if (g.source === 'gamevault') {
          var dl = h('button', { onclick: function () { act(dl, api('POST', 'downloads/add', { game: id }), 'Queued; see Downloads'); } }, 'Queue download'); buttons.appendChild(dl);
          var prep = h('button', { class: g.installed ? '' : 'primary', onclick: function () { act(prep, api('POST', 'install/prepare', { game: id }), 'Preparing plan (download, inspection)…').then(function (j) { pollJob(j, render); }); } }, g.installed ? 'Reinstall plan' : 'Prepare install'); buttons.appendChild(prep);
        }
        buttons.appendChild(fav);
        var comp = g.compatibility;
        view.appendChild(h('p', {}, h('a', { href: '#/library' }, '← Library')));
        view.appendChild(h('div', { class: 'hero' }, cover(g, true), h('div', {}, h('h1', {}, g.title), h('div', {}, badge(comp.classification, TONE[comp.classification]), g.installed ? badge('installed', 'ok') : badge('not installed'), badge(g.source), badge(g.os)), h('p', { class: 'muted' }, g.description ? g.description.slice(0, 400) : ''), buttons)));
        view.appendChild(h('div', { class: 'panel' }, kv([['ID', g.id], ['Aliases', g.aliases.join(', ')], ['Architecture', g.architecture], ['Wine prefix', g.prefix ? (g.prefix.detected_architecture || g.prefix.requested_architecture) + ' (' + g.prefix.state + ')' : null],
          ['Executable', g.launcher ? g.launcher.executable + (g.launcher.exists ? '' : '  — MISSING') : 'not registered (run: arch-games discover ' + g.id + ')'], ['Last launch', g.last_launch ? g.last_launch.started_at + ' · ' + g.last_launch.state + (g.last_launch.exit_code ? ' · exit ' + g.last_launch.exit_code : '') : null],
          ['Storage', g.storage ? bytes(g.storage.bytes) + (g.storage.partial ? '+' : '') : null], ['Tags', g.tags.join(', ')]])));
        view.appendChild(h('h2', {}, 'Compatibility: ' + comp.classification));
        if (comp.reasons.length) view.appendChild(h('div', { class: 'panel' }, h('ul', { class: 'plain' }, comp.reasons.map(function (e) { return h('li', {}, e); }))));
        view.appendChild(h('h2', {}, 'Evidence'));
        view.appendChild(h('div', { class: 'panel' }, h('ul', { class: 'plain' }, comp.evidence.map(function (e) { return h('li', {}, e); })), comp.uncertainties.length ? h('p', { class: 'muted' }, 'Uncertain:') : null, h('ul', { class: 'plain' }, comp.uncertainties.map(function (e) { return h('li', { class: 'muted' }, e); }))));
        if (g.job) view.appendChild(planPanel(g, render));
        view.appendChild(wineForm(g));
        var notes = h('textarea', { 'aria-label': 'Notes' }); notes.value = g.notes || '';
        var tags = h('input', { 'aria-label': 'Tags (comma separated)', value: g.tags.join(', ') });
        var save = h('button', { onclick: function () { act(save, api('POST', 'notes/' + encodeURIComponent(id), { text: notes.value, tags: tags.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean) }), 'Saved'); } }, 'Save');
        view.appendChild(h('h2', {}, 'Notes & tags')); view.appendChild(h('div', { class: 'panel' }, notes, h('p', {}, tags), save));
      }).catch(function (e) { if (e.message !== 'login') { clear(); view.appendChild(h('p', { class: 'err' }, e.message)); } });
    }
    render();
  }

  function planPanel(g, render) {
    var j = g.job, box = h('div', { class: 'panel' }, h('h2', { style: 'margin-top:0' }, 'Installation: ' + j.status + (j.step ? ' (' + j.step + ')' : '')));
    if (j.error) box.appendChild(h('p', { class: 'err' }, j.error));
    var plan = j.plan;
    if (plan) {
      box.appendChild(kv([['Method', plan.method], ['Installer', plan.installer], ['Installer architecture', plan.installer_architecture], ['Game architecture', plan.game_architecture],
        ['Wine prefix', plan.prefix ? plan.prefix.action + ' ' + plan.prefix.architecture : null], ['Why', plan.prefix ? plan.prefix.reason : null], ['Archive SHA-256', j.sha256], ['Needs free space', bytes(plan.required_free_bytes)]]));
      (plan.notes || []).forEach(function (n) { box.appendChild(h('p', { class: 'muted' }, n)); });
      if (j.status === 'plan_only' || j.status === 'awaiting_approval') {
        var runs = plan.method === 'wine-installer', ok = h('input', { type: 'checkbox', id: 'ok' });
        if (runs) box.appendChild(h('div', { class: 'warnbox' }, h('p', {}, 'This will run the downloaded installer ', h('b', {}, plan.installer || ''), ' under Wine in an isolated prefix. Only continue if you trust this source.'), h('label', {}, ok, ' I approve running this installer')));
        var go = h('button', { class: 'primary', onclick: function () { if (runs && !ok.checked) { toast('Tick the approval box to run the installer', true); return; } act(go, api('POST', 'install/approve', { game: g.id, plan_sha256: j.sha256, approve_installer: runs && ok.checked }), 'Installing…').then(function (r) { pollJob(r, render); }); } }, runs ? 'Approve and install' : 'Approve');
        box.appendChild(h('p', {}, go));
      }
    }
    (j.next || []).forEach(function (n) { box.appendChild(h('p', { class: 'muted' }, n)); });
    return box;
  }

  function wineForm(g) {
    var s = g.wine_settings || {};
    var runtime = h('input', { value: s.runtime || '', 'aria-label': 'Wine runtime name' }), ver = h('select', { 'aria-label': 'Windows version' }, h('option', { value: '' }, 'default'));
    ['winxp', 'win7', 'win8', 'win81', 'win10', 'win11'].forEach(function (v) { var o = h('option', { value: v }, v); if (s.windows_version === v) o.selected = true; ver.appendChild(o); });
    var vd = h('input', { placeholder: '1280x720', value: s.virtual_desktop || '', 'aria-label': 'Virtual desktop' }), dbg = h('input', { placeholder: '-all', value: s.debug || '', 'aria-label': 'WINEDEBUG' });
    var dll = h('input', { placeholder: 'dxgi=n,b; d3d9=b', value: Object.keys(s.dll_overrides || {}).map(function (k) { return k + '=' + s.dll_overrides[k]; }).join('; '), 'aria-label': 'DLL overrides' });
    var save = h('button', { onclick: function () {
      var ov = {}; dll.value.split(';').map(function (x) { return x.trim(); }).filter(Boolean).forEach(function (p) { var i = p.indexOf('='); ov[p.slice(0, i)] = p.slice(i + 1); });
      var body = { dll_overrides: ov, virtual_desktop: vd.value, debug: dbg.value }; if (runtime.value) body.runtime = runtime.value; if (ver.value) body.windows_version = ver.value;
      act(save, api('POST', 'wine/config/' + encodeURIComponent(g.id), body), 'Wine settings saved'); } }, 'Save Wine settings');
    return h('div', {}, h('h2', {}, 'Wine settings'), h('div', { class: 'panel' }, kv([['Runtime', runtime], ['Windows version', ver], ['Virtual desktop', vd], ['WINEDEBUG', dbg], ['DLL overrides', dll]]), h('p', {}, save), h('p', { class: 'muted' }, 'Saved settings apply at the next launch.')));
  }

  function downloadsView() {
    clear(); var body = h('tbody'), run = h('button', { class: 'primary', onclick: function () { act(run, api('POST', 'downloads/run'), 'Download queue started').then(function (j) { pollJob(j, load); }); } }, 'Run queue');
    view.appendChild(h('h1', {}, 'Downloads')); view.appendChild(h('p', {}, run));
    view.appendChild(h('div', { class: 'panel' }, h('table', {}, h('thead', {}, h('tr', {}, ['Game', 'State', 'Progress', 'Speed', ''].map(function (x) { return h('th', {}, x); }))), body)));
    function load() {
      api('GET', 'downloads').then(function (rows) {
        body.textContent = '';
        rows.forEach(function (d) {
          var pct = d.expected_size ? Math.min(100, d.received * 100 / d.expected_size) : 0;
          var acts = h('td');
          if (['failed', 'paused', 'cancelled'].indexOf(d.state) >= 0) { var r = h('button', { onclick: function () { act(r, api('POST', 'downloads/retry', { id: d.id }), 'Retrying (resumes if the server allows)').then(load); } }, 'Retry'); acts.appendChild(r); }
          if (['queued', 'running'].indexOf(d.state) >= 0) { var pz = h('button', { onclick: function () { act(pz, api('POST', 'downloads/pause', { id: d.id }), 'Pause requested (partial file kept)').then(load); } }, 'Pause'); acts.appendChild(pz); }
          if (['queued', 'running', 'paused', 'failed'].indexOf(d.state) >= 0) { var c = h('button', { class: 'danger', onclick: function () { act(c, api('POST', 'downloads/cancel', { id: d.id }), 'Cancel requested').then(load); } }, 'Cancel'); acts.appendChild(c); }
          body.appendChild(h('tr', {}, h('td', {}, h('a', { href: '#/game/' + encodeURIComponent(d.game_id) }, d.title || d.game_id), d.error ? h('div', { class: 'err' }, d.error) : null), h('td', {}, badge(d.state, d.state === 'completed' ? 'ok' : d.state === 'failed' ? 'bad' : '')),
            h('td', {}, h('div', { class: 'progress', role: 'progressbar', 'aria-valuenow': Math.round(pct) }, h('i', { style: 'width:' + pct + '%' })), h('small', { class: 'muted' }, bytes(d.received) + ' / ' + bytes(d.expected_size))), h('td', {}, d.speed ? bytes(d.speed) + '/s' : ''), acts));
        });
        if (!rows.length) body.appendChild(h('tr', {}, h('td', { colspan: 5, class: 'muted' }, 'No downloads')));
      }).catch(function () {});
    }
    load(); timers.push(setInterval(load, 2000));
  }

  function installsView() {
    clear(); view.appendChild(h('h1', {}, 'Installation jobs'));
    api('GET', 'installs').then(function (rows) {
      var t = h('tbody'); rows.forEach(function (r) { t.appendChild(h('tr', {}, h('td', {}, h('a', { href: '#/game/' + encodeURIComponent(r.game) }, r.title || r.game)), h('td', {}, badge(r.status, r.status === 'done' ? 'ok' : r.status === 'failed' ? 'bad' : 'warn')), h('td', {}, r.step || ''), h('td', { class: 'err' }, r.error || ''))); });
      if (!rows.length) t.appendChild(h('tr', {}, h('td', { colspan: 4, class: 'muted' }, 'No installation jobs. Open a GameVault game and choose "Prepare install".')));
      view.appendChild(h('div', { class: 'panel' }, h('table', {}, h('thead', {}, h('tr', {}, ['Game', 'Status', 'Step', 'Error'].map(function (x) { return h('th', {}, x); }))), t)));
    });
  }

  function checks(items) {
    var t = h('tbody'); items.forEach(function (i) { t.appendChild(h('tr', {}, h('td', {}, badge(i.status.toUpperCase(), i.status === 'ok' ? 'ok' : i.status === 'warn' ? 'warn' : i.status === 'fail' ? 'bad' : '')), h('td', {}, i.group ? i.group + ' / ' + i.check : i.check), h('td', {}, i.detail, i.fix ? h('div', { class: 'muted' }, 'Fix: ' + i.fix) : null))); });
    return h('div', { class: 'panel' }, h('table', {}, t));
  }
  function wineView() { clear(); view.appendChild(h('h1', {}, 'Wine')); api('GET', 'wine/doctor').then(function (r) { view.appendChild(checks(r.checks)); view.appendChild(h('p', { class: 'muted' }, 'Host architecture: ' + r.host_architecture)); }); }
  function diagView() {
    clear(); view.appendChild(h('h1', {}, 'Diagnostics'));
    api('GET', 'doctor').then(function (r) { view.appendChild(h('p', {}, Object.keys(r.summary).map(function (k) { return badge(k + ' ' + r.summary[k], k === 'fail' ? 'bad' : k === 'warn' ? 'warn' : k === 'ok' ? 'ok' : ''); }))); view.appendChild(checks(r.checks)); });
    api('GET', 'storage').then(function (s) { view.appendChild(h('h2', {}, 'Storage')); view.appendChild(h('div', { class: 'panel' }, kv(Object.keys(s).filter(function (k) { return k !== 'free_bytes'; }).map(function (k) { return [k, bytes(s[k].bytes) + (s[k].partial ? '+' : '')]; }).concat([['free', bytes(s.free_bytes)]])))); });
  }
  function audioView() {
    clear(); view.appendChild(h('h1', {}, 'Audio'));
    api('GET', 'audio').then(function (a) {
      view.appendChild(h('h2', {}, 'Local audio (this machine): ' + a.local.verdict));
      view.appendChild(checks(a.local.layers.map(function (l) { return { status: l.status, check: l.layer, detail: l.detail, fix: l.fix }; })));
      var r = a.remote; view.appendChild(h('h2', {}, 'Remote browser audio'));
      var box = h('div', { class: 'panel' });
      if (!r.enabled) box.appendChild(h('p', { class: 'muted' }, 'Off. It is optional and needs the host bridge (see docs/REMOTE_AUDIO_3.md); enable with: arch-games audio remote configure …'));
      else {
        box.appendChild(kv([['Reachable', r.reachable ? 'yes' : 'no'], ['Authenticated', r.authenticated == null ? '' : (r.authenticated ? 'yes' : 'no')], ['VM', r.vm], ['Stream', r.stream ? r.stream.state + ', ' + r.stream.listeners + ' listener(s)' : ''], ['Error', r.error]]));
        if (r.page) box.appendChild(h('p', {}, h('a', { href: r.page, target: '_blank', rel: 'noopener noreferrer' }, 'Open the browser audio page')));
        var d = h('button', { class: 'danger', onclick: function () { act(d, api('POST', 'audio/remote/disable'), 'Remote audio disabled').then(audioView); } }, 'Disable remote audio'); box.appendChild(d);
      }
      view.appendChild(box);
    });
  }

  function route() {
    var hash = location.hash.replace(/^#\/?/, '') || 'library', parts = hash.split('/');
    document.querySelectorAll('nav a').forEach(function (a) { a.classList.toggle('on', a.dataset.r === parts[0]); });
    if (parts[0] === 'game' && parts[1]) return gameView(decodeURIComponent(parts[1]));
    ({ library: libraryView, downloads: downloadsView, installs: installsView, audio: audioView, wine: wineView, diagnostics: diagView }[parts[0]] || libraryView)();
    view.focus({ preventScroll: true });
  }

  var nav = document.getElementById('nav'); NAV.forEach(function (n) { var a = h('a', { href: '#/' + n[0] }, n[1]); a.dataset.r = n[0]; nav.appendChild(a); });
  window.addEventListener('hashchange', route);
  var frag = new URLSearchParams(location.hash.replace(/^#\/?/, '').replace(/^.*?(?=token=)/, '')).get('token');
  function start() { fetch('/api/session', { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (s) { if (s.authenticated) { csrf = s.csrf; route(); } else showLogin(); }); }
  if (frag) { history.replaceState(null, '', location.pathname); api('POST', 'login', { token: frag }).then(function (r) { csrf = r.csrf; route(); }).catch(function () { showLogin(); }); } else start();
})();
