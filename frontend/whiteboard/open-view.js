/*
Input: The whiteboard API (window.PlovaneWhiteboard), the board reply, and code.explain, code.translate and security.scan
Output: The open panel (click a block or an API) and the API diagram overlay
Processing: Shows what a clicked block does, its connections, risky lines, tests and code with plain English;
  the API overlay hides the folder and file tree and draws only your code, your endpoints and the outside APIs
Changed by: Claude Code
Date: 2026-11-23
*/

// One diagram explains everything. Clicking a block opens a panel beside the board: what it does,
// what it uses and what uses it, its risky lines, its tests, and (for a file) the code with plain
// English under each line. Zooming still works as before (scroll, double-click, "Zoom in").
// "API diagram" in the canvas toolbar is an overlay: it removes the folder and file tree and draws
// only the API connections, from the code that calls or serves them, through your endpoints and
// services, to the outside APIs. Clicking anything in it opens the same panel.
(() => {
  const wb = window.PlovaneWhiteboard
  if (!wb) return
  const $ = (s, root = document) => root.querySelector(s)
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`
  const op = (name, args = {}) => wb.request('op', { method: 'POST', body: JSON.stringify({ op: name, args }) }).then(d => d.result || {})

  const KIND_ICON = { agent: '🤖', service: '⚙️', server: '🖥️', external: '🌐', database: '🗄️', queue: '📬', endpoint: '🔌', folder: '📁', group: '🗂️', file: '📄', function: '🔧', class: '🧩', step: '👣', code: '💻', note: '📝' }
  const LANG_ICON = { python: ['Py', '#9CC8FF'], javascript: ['JS', '#FFF07A'], typescript: ['TS', '#8EC5FF'], css: ['#', '#D3BCFF'], html: ['<>', '#FFB899'], markdown: ['¶', '#E4E4EA'], json: ['{}', '#B8F2C8'], cpp: ['C+', '#A9C7FF'], shell: ['$', '#C8F5A0'] }
  const OUTSIDE = new Set(['external'])
  const MIDDLE = new Set(['service', 'database', 'queue', 'server'])
  const KIND_NAME = { agent: 'AI agent', external: 'Outside API', service: 'Service', database: 'Database', queue: 'Queue', server: 'Server', folder: 'Folder' }
  const RISK_NAME = { shell: 'Command injection', sql_from_text: 'SQL injection', secret_in_code: 'Secret in code', eval: 'Runs text as code', unsafe_html: 'Unsafe HTML', html_injection: 'Unsafe HTML', pickle: 'Unsafe loading', weak_hash: 'Weak hashing', no_verify: 'TLS check turned off', tls_off: 'TLS check turned off', path_traversal: 'Path from user input' }
  // Short query sent to library.rank for each risk, chosen to match verified helpers people would actually swap in.
  const FIX_QUERY = { shell: 'run a shell command safely without shell injection', sql_from_text: 'safely build a SQL query with parameters', secret_in_code: 'read a secret from an environment variable', eval: 'parse JSON safely instead of eval', unsafe_html: 'escape HTML to prevent XSS', html_injection: 'escape HTML to prevent XSS', pickle: 'load JSON or YAML safely', weak_hash: 'hash a password with bcrypt or argon2', no_verify: 'make an HTTPS request with certificate verification', tls_off: 'make an HTTPS request with certificate verification', path_traversal: 'validate a file path stays inside a base directory' }
  const MAX_CODE = 40

  const st = { open: null, apis: false, scan: null, risks: {}, explain: {}, lines: {}, seq: null,
    trace: null, traceRunning: false, lastTest: localStorage.getItem('plovane-last-test') || '',
    fixes: {}, fixing: {} }

  const icon = b => {
    const lang = b?.kind === 'file' && LANG_ICON[b.meta?.language]
    return lang ? `<span class="lang-icon" style="--c:${lang[1]}">${esc(lang[0])}</span>` : `<span class="kind-icon">${KIND_ICON[b?.kind] || '⬜'}</span>`
  }
  const blocks = () => wb.board?.board?.blocks || {}
  const endpoints = () => wb.board?.board?.endpoints || {}
  const arrows = () => Object.values(wb.board?.board?.arrows || {}).map(a => ({ id: a.id, from: a.source ?? a.from, to: a.target ?? a.to, endpoint: a.endpoint })).filter(a => blocks()[a.from] && blocks()[a.to])
  const tests = () => wb.board?.overlays?.results?.test_health || {}
  const riskName = kind => RISK_NAME[kind] || String(kind || 'Risk').replace(/_/g, ' ')
  const riskCount = b => (b?.path && st.risks[b.path]?.length) || 0
  function inside(id, ancestor) { let b = blocks()[id]; while (b) { if (b.id === ancestor) return true; b = blocks()[b.parent] } return false }
  function byPath() { const out = {}; for (const b of Object.values(blocks())) if (b.path) out[b.path] = b.id; return out }
  // Endpoint ids look like "GET /users" or "routes-py:GET /users" (the serving block first).
  function splitEndpoint(id) { const text = String(id).replace(/^[^\s:]+:(?=[A-Z]+ )/, ''), [method, ...rest] = text.split(' '); return rest.length ? [method, rest.join(' ')] : ['', text] }
  // An endpoint's handler may be a block id or a file path.
  function handlerOf(e) { if (!e?.handler) return null; return blocks()[e.handler] ? e.handler : byPath()[e.handler] || null }

  // ------------------------------------------------------------------ Mount -----

  const area = $('.canvas-area'), viewport = $('#canvas-viewport'), toolbar = $('.canvas-toolbar')
  if (!area || !viewport || !toolbar) return
  const toggle = document.createElement('button')
  toggle.className = 'ov-toggle'; toggle.dataset.ovApis = ''
  toggle.title = 'Show only the API connections, without the folder and file tree'
  toggle.textContent = '⇄ API diagram'
  const toolbarLeft = $('.toolbar-left', toolbar) || toolbar
  toolbarLeft.append(toggle)
  const overlay = document.createElement('section')
  overlay.className = 'ov-pane ov-structure'; overlay.hidden = true
  overlay.innerHTML = '<div class="ov-inner"></div>'
  area.append(overlay)
  const panel = document.createElement('aside')
  panel.className = 'op-panel'; panel.setAttribute('aria-label', 'What this block does')
  area.append(panel)

  function place() {
    const top = Math.max(viewport.offsetTop, toolbar.offsetTop + toolbar.offsetHeight)
    overlay.style.top = `${top}px`
    panel.style.top = `${top + 12}px`
  }

  // --------------------------------------------------------------- Trace ----

  /**
   * Input: path (string)
   * Output: { ran: Set<number>, missed: Set<number> } | null
   * Processing: The saved trace for one project file: which lines ran and which didn't.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function traceFor(path) {
    const rec = st.trace?.files?.[path]
    if (!rec) return null
    return { ran: new Set(rec.ran || []), missed: new Set(rec.missed || []) }
  }

  async function loadTrace(force) {
    if (st.trace && !force) return
    try { st.trace = await op('trace.read') } catch (_) { st.trace = null }
  }

  /**
   * Input: w (risky-line record), isFile (bool), b (file or folder block)
   * Output: string (HTML)
   * Processing: Draw one risky-line row with a "Fix with ⇢" button that offers a verified library match.
   * Changed by: Claude Code
   * Date: 2026-11-24
   */
  function riskRow(w, isFile, b) {
    const key = `${w.file}:${w.line}:${w.kind}`
    const fix = st.fixes[key], loading = st.fixing[key]
    const data = isFile ? `data-op-line="${w.line}"` : `data-op-file="${esc(w.file)}" data-op-line="${w.line}"`
    const canFix = !!FIX_QUERY[w.kind]
    const header = `<button class="op-risk ${esc(w.severity)}" ${data}><b>${isFile ? '' : `${esc(w.file.split('/').pop())} · `}Line ${w.line}: ${esc(w.why)}</b><span>${esc(riskName(w.kind))} · ${esc(w.severity)}</span></button>`
    if (!canFix) return header
    let fixBox = ''
    if (fix?.best) {
      const e = fix.best, into = (b.kind === 'folder' ? b.path : (b.path || '').split('/').slice(0, -1).join('/')) || '.'
      fixBox = `<div class="op-fix"><div class="op-fix-top"><b>${esc(e.name)}</b> <small>· ${esc(e.language || '')} · ${esc(e.license || 'unknown licence')}</small><button class="op-fix-insert" data-op-fix-insert="${esc(key)}" data-op-fix-entry="${esc(e.block_id)}" data-op-fix-into="${esc(into)}">Insert into ${esc(into || 'project')}</button></div>${e.description ? `<div class="op-fix-desc">${esc(e.description)}</div>` : ''}<pre class="op-fix-code">${esc((e.preview || e.code || '').split('\n').slice(0, 8).join('\n'))}</pre></div>`
    } else if (fix?.none) {
      fixBox = `<div class="op-fix-none">No verified helper matched this one.</div>`
    } else if (fix?.error) {
      fixBox = `<div class="op-fix-none">Couldn't look up a fix: ${esc(fix.error)}</div>`
    }
    const btn = fix ? '' : `<button class="op-fix-btn" data-op-fix="${esc(key)}" data-op-fix-kind="${esc(w.kind)}">${loading ? 'Searching…' : 'Fix with ⇢'}</button>`
    return header + btn + fixBox
  }

  async function loadFix(key, kind) {
    if (st.fixes[key] || st.fixing[key]) return
    const query = FIX_QUERY[kind]; if (!query) return
    st.fixing[key] = true; if (st.open && !st.open.startsWith('ep:')) render(st.open)
    try {
      const r = await op('library.rank', { query, k: 1 })
      const best = (r.recommendations || [])[0] || null
      st.fixes[key] = best ? { best } : { none: true }
    } catch (e) { st.fixes[key] = { error: e.message } }
    finally { delete st.fixing[key] }
    if (st.open && !st.open.startsWith('ep:')) render(st.open)
  }

  async function insertFix(entry, into) {
    try {
      const r = await op('library.insert', { entry, into })
      wb.toast(`Inserted ${entry} into ${r.file || into}. Review the diff on disk.`)
      await wb.refresh()
    } catch (e) { wb.toast(e.message, true) }
  }

  /**
   * Input: b (file block)
   * Output: string (HTML)
   * Processing: One-line summary above the reader: "N lines didn't run" or "every executable line ran", or empty.
   * Changed by: Claude Code
   * Date: 2026-11-24
   */
  function traceSummary(b) {
    const t = traceFor(b.path); if (!t) return ''
    const missed = t.missed.size, ran = t.ran.size
    if (!ran && !missed) return `<div class="op-trace-note">Not covered by the last trace.</div>`
    if (!missed) return `<div class="op-trace-note ok">✓ Every executable line ran (${ran}).</div>`
    return `<div class="op-trace-note">${plural(missed, 'line')} didn't run under ${esc(st.trace?.target || 'the last trace')}.</div>`
  }

  async function runTrace(testFile) {
    if (!testFile || st.traceRunning) return
    st.traceRunning = true; wb.toast(`Tracing ${testFile}…`)
    try {
      const r = await op('trace.run', { target: testFile, timeout: 180 })
      await loadTrace(true)
      st.lastTest = testFile; localStorage.setItem('plovane-last-test', testFile)
      wb.toast(`Trace done: ${r.counts?.ran || 0} lines ran, ${r.counts?.missed || 0} not reached.`)
      if (st.open && !st.open.startsWith('ep:')) render(st.open)
    } catch (e) { wb.toast(e.message, true) }
    finally { st.traceRunning = false }
  }

  // ----------------------------------------------------------- Risk scan -----

  async function scan() {
    try {
      const r = await op('security.scan')
      st.scan = r; st.risks = {}
      for (const w of r.weak_spots || []) (st.risks[w.file] ||= []).push(w)
      if (st.apis) renderApis()
      if (st.open) st.open.startsWith('ep:') ? renderEndpoint(st.open.slice(3)) : render(st.open)
    } catch (_) { }
  }

  // ---------------------------------------------------------- Open panel -----

  function hide() { panel.classList.remove('show'); overlay.classList.remove('with-panel'); st.open = null }
  function show() { place(); panel.classList.add('show'); overlay.classList.add('with-panel'); if (!st.scan) scan(); loadTrace() }

  /**
   * Input: id (block id or null)
   * Output: string
   * Processing: Guess a sibling test file for a file block ("foo.py" → "test_foo.py" in a tests folder nearby).
   * Changed by: Claude Code
   * Date: 2026-11-24
   */
  function guessTestFor(id) {
    const b = blocks()[id]; if (!b?.path) return ''
    const parts = b.path.split('/'), base = parts.pop(), stem = base.replace(/\.py$/, '')
    const candidates = [
      `${parts.join('/')}/tests/test_${stem}.py`,
      `tests/test_${stem}.py`,
      `tests/${stem}/test_${stem}.py`,
    ]
    for (const c of candidates) if (byPath()[c]) return c
    return ''
  }

  /**
   * Input: id (block id), line (number, optional)
   * Output: none
   * Processing: Open the panel for a block and fill it.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function open(id, line) {
    if (!blocks()[id]) return
    st.open = id; render(id, line); show()
  }

  function chips(ids) {
    const list = [...new Set(ids)].filter(x => blocks()[x])
    if (!list.length) return '<div class="op-none">Nothing</div>'
    const shown = list.slice(0, 40)
    return `<div class="op-chips">${shown.map(x => `<button data-op-go="${esc(x)}" title="${esc(blocks()[x].path || blocks()[x].name)}">${icon(blocks()[x])}<span>${esc(blocks()[x].name)}</span></button>`).join('')}${list.length > shown.length ? `<span class="op-none">+${list.length - shown.length} more</span>` : ''}</div>`
  }
  function endpointChips(ids) {
    const list = [...new Set(ids)].filter(x => endpoints()[x])
    if (!list.length) return ''
    return `<div class="op-chips">${list.map(x => { const [m, p] = splitEndpoint(x); return `<button data-op-endpoint="${esc(x)}"><span class="kind-icon">🔌</span><span>${esc(m)} ${esc(p)}</span></button>` }).join('')}</div>`
  }

  /**
   * Input: id (block id), line (number to jump to, or 0)
   * Output: none
   * Processing: Fill the panel: what the block does, what it uses and what uses it (a folder counts everything inside it),
   *   the endpoints it serves or calls, its risky lines and tests, and for a file the code with plain English under each line.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function render(id, line = 0) {
    const all = blocks(), b = all[id]; if (!b) return hide()
    const isFile = b.kind === 'file' && b.path, isFolder = b.kind === 'folder' || b.kind === 'group'
    const mine = x => isFolder ? inside(x, id) : x === id
    const uses = [], usedBy = [], calls = []
    for (const a of arrows()) {
      if (mine(a.from) && !mine(a.to)) uses.push(a.to)
      if (mine(a.to) && !mine(a.from)) usedBy.push(a.from)
      if (a.endpoint && mine(a.from)) calls.push(a.endpoint)
    }
    const serves = Object.values(endpoints()).filter(e => handlerOf(e) && mine(handlerOf(e))).map(e => e.id)
    const risks = isFile ? (st.risks[b.path] || []).slice().sort((x, y) => x.line - y.line)
      : isFolder ? Object.entries(st.risks).filter(([p]) => p === b.path || p.startsWith((b.path || '') + '/')).flatMap(([, v]) => v) : []
    const test = tests()[id]
    const kids = Object.values(all).filter(x => x.parent === id)
    const untested = isFolder ? Object.entries(tests()).filter(([k, r]) => r.own && r.status === 'untested' && inside(k, id)).length : 0
    const folders = kids.filter(k => k.kind === 'folder').length, files = kids.length - folders
    const about = b.meta?.summary || (isFile ? st.explain[b.path] : null)
      || (isFolder ? `A folder with ${[folders && plural(folders, 'folder'), files && plural(files, 'file')].filter(Boolean).join(' and ') || 'nothing in it yet'}.` : null)
      || (b.kind === 'external' ? `An outside API${usedBy.length ? `, called from ${plural(new Set(usedBy).size, 'place')} in your code` : ' that no code is connected to yet'}.` : null)
    const kind = isFile ? (b.meta?.language || 'file') : (KIND_NAME[b.kind] || b.kind)
    panel.innerHTML = `
      <div class="op-head"><span class="op-icon">${icon(b)}</span><div><h3>${esc(b.name)}</h3><div class="op-path">${esc(b.path || kind)}</div></div>
        <div class="op-actions"><button data-op-zoom="${esc(id)}" title="Zoom the board into this block">⤢ Zoom in</button><button data-op-close aria-label="Close" title="Close (Esc)">✕</button></div></div>
      <div class="op-body">
        <p class="op-about${about ? '' : ' loading'}" id="op-about">${esc(about || (isFile ? 'Reading what this file does…' : `${kind[0].toUpperCase()}${kind.slice(1)} on your whiteboard.`))}</p>
        <div class="op-sec"><h4>Connections</h4>
          <div class="op-label">Uses (${new Set(uses).size})</div>${chips(uses)}
          <div class="op-label">Used by (${new Set(usedBy).size})</div>${chips(usedBy)}
          ${serves.length ? `<div class="op-label">Serves these endpoints (${serves.length})</div>${endpointChips(serves)}` : ''}
          ${calls.length ? `<div class="op-label">Calls these endpoints (${new Set(calls).size})</div>${endpointChips(calls)}` : ''}
          ${isFolder && kids.length ? `<div class="op-label">Inside (${kids.length})</div>${chips(kids.map(k => k.id))}` : ''}</div>
        ${isFile || isFolder ? `<div class="op-sec"><h4>Risky code <span>${risks.length || ''}</span></h4>${risks.length ? risks.slice(0, 30).map(w => riskRow(w, isFile, b)).join('') + (risks.length > 30 ? `<div class="op-none">+${risks.length - 30} more</div>` : '') : `<div class="op-none">${st.scan ? 'Nothing risky found.' : 'Scanning…'}</div>`}</div>` : ''}
        ${test?.own ? `<div class="op-sec"><h4>Tests</h4><span class="op-badge ${test.status === 'tested' ? 'ok' : ''}">${test.status === 'tested' ? '✓ Tested' : 'No tests'}</span></div>` : isFolder && untested ? `<div class="op-sec"><h4>Tests</h4><span class="op-badge">${plural(untested, 'file')} with no tests</span></div>` : ''}
        ${isFile ? `<div class="op-sec"><h4>What each line does <button class="op-trace-btn" data-op-trace title="Run a pytest file under coverage and mark the lines that didn't run">${st.traceRunning ? 'Tracing…' : (traceFor(b.path) ? '↻ Re-run trace' : '▶ Trace with…')}</button></h4>${traceSummary(b)}<ol class="op-reader" id="op-reader"><li><span class="en">Loading…</span></li></ol></div>` : ''}
      </div>`
    if (isFile) { loadAbout(b); loadLines(b, line) }
  }

  /**
   * Input: eid (endpoint id, such as "POST /charge")
   * Output: none
   * Processing: Fill the panel for one of your endpoints: its status, the code that serves it, the code that calls it,
   *   and its request, response and last test when known.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function renderEndpoint(eid) {
    const e = endpoints()[eid]; if (!e) return hide()
    const [method, path] = splitEndpoint(eid)
    const handler = handlerOf(e)
    const callers = arrows().filter(a => a.endpoint === eid).map(a => a.from)
    const json = v => `<pre class="op-json">${esc(JSON.stringify(v, null, 2))}</pre>`
    panel.innerHTML = `
      <div class="op-head"><span class="op-icon"><span class="kind-icon">🔌</span></span><div><h3>${esc(method)} ${esc(path)}</h3><div class="op-path">Endpoint · ${esc(e.status || 'unknown')}</div></div>
        <div class="op-actions">${handler ? `<button data-op-zoom="${esc(handler)}" title="Zoom the board into the code that serves it">⤢ Zoom in</button>` : ''}<button data-op-close aria-label="Close" title="Close (Esc)">✕</button></div></div>
      <div class="op-body">
        <p class="op-about">${esc(`Your project answers ${method || 'requests'} ${path}${handler ? ` in ${blocks()[handler].name}` : ''}${callers.length ? `, and ${plural(new Set(callers).size, 'place')} in your code call it` : ''}.`)}</p>
        <div class="op-sec"><h4>Connections</h4>
          <div class="op-label">Served by</div>${handler ? chips([handler]) : '<div class="op-none">No handler found yet</div>'}
          <div class="op-label">Called from (${new Set(callers).size})</div>${chips(callers)}</div>
        ${e.request ? `<div class="op-sec"><h4>Request</h4>${json(e.request)}</div>` : ''}
        ${e.response ? `<div class="op-sec"><h4>Response</h4>${json(e.response)}</div>` : ''}
        ${e.last_test ? `<div class="op-sec"><h4>Last check</h4>${json(e.last_test)}</div>` : ''}
      </div>`
  }
  function openEndpoint(eid) { if (!endpoints()[eid]) return; st.open = `ep:${eid}`; renderEndpoint(eid); show() }

  async function loadAbout(b) {
    if (b.meta?.summary || st.explain[b.path] !== undefined) return
    try { st.explain[b.path] = String((await op('code.explain', { target: b.path })).explanation || '').split('\n')[0] }
    catch (_) { st.explain[b.path] = '' }
    const el = $('#op-about'); if (el && st.open === b.id) { el.textContent = st.explain[b.path] || 'No description yet.'; el.classList.remove('loading') }
  }

  async function loadLines(b, line) {
    let rows = st.lines[b.path]
    if (!rows) {
      try { rows = st.lines[b.path] = (await op('code.translate', { file: b.path })).lines || [] }
      catch (e) { const el = $('#op-reader'); if (el && st.open === b.id) el.innerHTML = `<li><span class="en">${esc(e.message)}</span></li>`; return }
    }
    const el = $('#op-reader'); if (!el || st.open !== b.id) return
    const risky = {}
    for (const w of st.risks[b.path] || []) (risky[w.line] ||= []).push(w)
    const shown = rows.slice(0, 1500), lang = b.meta?.language || ''
    const trace = traceFor(b.path)
    el.innerHTML = shown.map(r => {
      const w = risky[r.line], sev = w ? (w.some(x => x.severity === 'high') ? 'high' : w[0].severity) : ''
      const miss = trace && trace.missed.has(r.line) && !w
      const cls = w ? `risky ${sev}` : miss ? 'missed' : ''
      const en = ['continues', 'blank'].includes(r.source) || !r.english ? '' : r.english
      return `<li data-line="${r.line}" class="${cls}">${en ? `<span class="en">${esc(en)}</span>` : ''}<span class="n">${r.line}</span><code>${paint(r.code, lang)}</code>${w ? `<span class="why">⚠ ${w.map(x => esc(x.why)).join(' · ')}</span>` : miss ? `<span class="miss">never ran under the last trace</span>` : ''}</li>`
    }).join('') + (rows.length > shown.length ? `<li><span class="en">…${rows.length - shown.length} more lines</span></li>` : '')
    if (line) jump(line)
  }

  function jump(line) {
    const li = $(`#op-reader li[data-line="${line}"]`); if (!li) return
    // Scroll only the panel: scrollIntoView would also slide the canvas area sideways.
    const body = $('.op-body', panel)
    if (body) body.scrollTo({ top: body.scrollTop + li.getBoundingClientRect().top - body.getBoundingClientRect().top - body.clientHeight / 2, behavior: 'smooth' })
    li.classList.remove('flash'); void li.offsetWidth; li.classList.add('flash')
  }

  const KEYWORDS = 'def|class|return|if|elif|else|for|while|import|from|as|with|try|except|finally|raise|async|await|function|const|let|var|new|export|default|in|not|and|or|is|None|True|False|null|undefined|true|false|this|self|lambda|yield|pass|break|continue|switch|case|interface|type|extends'
  function paint(code, lang) {
    code = String(code || '')
    const hash = ['python', 'shell', 'yaml', 'ruby', 'toml'].includes(lang)
    const re = new RegExp(`("(?:[^"\\\\]|\\\\.)*"|'(?:[^'\\\\]|\\\\.)*'|\`[^\`]*\`)|(${hash ? '#' : '\\/\\/'}.*$)|\\b(${KEYWORDS})\\b|\\b(\\d+(?:\\.\\d+)?)\\b`, 'g')
    let out = '', last = 0
    for (const m of code.matchAll(re)) {
      out += esc(code.slice(last, m.index))
      out += `<span class="${m[1] ? 'op-ts' : m[2] ? 'op-tc' : m[3] ? 'op-tk' : 'op-tn'}">${esc(m[0])}</span>`
      last = m.index + m[0].length
    }
    return out + esc(code.slice(last))
  }

  // -------------------------------------------------------- API diagram -----

  /**
   * Input: none
   * Output: { nodes, edges } for the API diagram
   * Processing: The code that calls an outside API, a service or an endpoint, or serves an endpoint (left); your endpoints
   *   and services (middle); the outside APIs (right). Folders and files with no API connection are left out.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function apiGraph() {
    const all = blocks(), nodes = new Map(), edges = []
    const add = (key, column, data) => { if (!nodes.has(key)) nodes.set(key, { key, column, ...data }) }
    const isApi = b => OUTSIDE.has(b.kind) || MIDDLE.has(b.kind)
    for (const b of Object.values(all)) if (isApi(b)) add(b.id, OUTSIDE.has(b.kind) ? 2 : 1, { block: b })
    for (const e of Object.values(endpoints())) add(`ep:${e.id}`, 1, { endpoint: e })
    for (const a of arrows()) {
      const from = all[a.from], to = all[a.to]
      if (a.endpoint && endpoints()[a.endpoint]) { add(from.id, 0, { block: from }); edges.push([from.id, `ep:${a.endpoint}`, a.id]); continue }
      if (!isApi(to)) continue
      add(from.id, isApi(from) ? (OUTSIDE.has(from.kind) ? 2 : 1) : 0, { block: from })
      edges.push([from.id, to.id, a.id])
    }
    for (const e of Object.values(endpoints())) { const h = handlerOf(e); if (h) { add(h, 0, { block: all[h] }); edges.push([`ep:${e.id}`, h]) } }
    return { nodes: [...nodes.values()], edges }
  }

  function nodeHTML(n, x, y) {
    if (n.endpoint) {
      const [method, path] = splitEndpoint(n.endpoint.id)
      return `<button class="ov-node endpoint" data-op-endpoint="${esc(n.endpoint.id)}" style="left:${x}%;top:${y}%"><span class="ov-method">${esc(method || 'API')}</span>${esc(path)}<small>${esc(n.endpoint.status || 'endpoint')}</small></button>`
    }
    const b = n.block, risks = riskCount(b)
    const sub = n.column === 0 ? (b.path ? b.path.split('/').slice(-2, -1)[0] || 'project' : KIND_NAME[b.kind] || b.kind) : KIND_NAME[b.kind] || b.kind
    return `<button class="ov-node${risks ? ' risky' : ''}" data-op-open="${esc(b.id)}" style="left:${x}%;top:${y}%" title="${esc(b.path || b.name)}">${icon(b)}${esc(b.name)}<small>${esc(sub)}</small>${risks ? `<span class="ov-risk" title="Risky lines">⚠ ${risks}</span>` : ''}</button>`
  }

  /**
   * Input: none
   * Output: none
   * Processing: Draw the API diagram over the board in three columns (your code, your endpoints and services, outside APIs)
   *   with an arrow for every call; risky files carry a ⚠ count.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function renderApis() {
    const host = $('.ov-inner', overlay), { nodes, edges } = apiGraph()
    const columns = [[], [], []]
    for (const n of nodes) columns[n.column].push(n)
    const name = n => n.endpoint ? n.endpoint.id : n.block.name
    columns.forEach(list => list.sort((a, b) => name(a).localeCompare(name(b))))
    const hidden = Math.max(0, columns[0].length - MAX_CODE)
    columns[0] = columns[0].slice(0, MAX_CODE)
    const pos = {}, X = [14, 50, 86]
    columns.forEach((list, c) => list.forEach((n, i) => { pos[n.key] = [X[c], ((i + 1) / (list.length + 1)) * 100] }))
    const rows = Math.max(3, ...columns.map(l => l.length))
    const head = `<div class="ov-head"><div><h2>API diagram</h2><p>Only the API connections: the code that calls or serves an API, your endpoints and services, and the outside APIs. The folder and file tree is hidden. Click anything to see what it does.</p></div>
      <div class="op-actions"><button class="ov-btn" data-ov-command="/endpoint scan">Find API routes in the code</button><button class="ov-btn primary" data-ov-apis>Back to the board</button></div></div>`
    if (!nodes.length) {
      host.innerHTML = `${head}<div class="ov-empty"><b>No API connections on this whiteboard yet</b>“Find API routes in the code” adds your endpoints, such as <code>@app.get("/users")</code>.<br>For an outside API, drag “External API” onto the board, or run <code>/block add external aws-s3</code>, then connect the code that calls it.</div>`
      return
    }
    const drawn = edges.filter(([a, b]) => pos[a] && pos[b])
    // Free lanes between the middle column's nodes, so an arrow from your code to an outside API never runs under an endpoint.
    const middle = columns[1].map(n => pos[n.key][1]), stops = [0, ...middle, 100]
    const gaps = stops.slice(1).map((y, i) => (y + stops[i]) / 2)
    const labels = ['Your code', 'Your endpoints and services', 'Outside APIs']
    host.innerHTML = `${head}<div class="ov-map ov-api-map" style="height:${Math.max(420, rows * 96 + 60)}px">
      ${labels.map((l, c) => `<div class="ov-col-label" style="left:${X[c]}%">${l}<small>${columns[c].length + (c === 0 ? hidden : 0)}</small></div>`).join('')}
      <svg viewBox="0 0 100 100" preserveAspectRatio="none"><defs><marker id="ov-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z"/></marker></defs>
        ${drawn.map(([a, b, id]) => edgePath(pos[a], pos[b], gaps, id)).join('')}</svg>
      ${nodes.filter(n => pos[n.key]).map(n => nodeHTML(n, ...pos[n.key])).join('')}
      ${hidden ? `<div class="ov-more" style="left:${X[0]}%">+${hidden} more files</div>` : ''}</div>`
  }

  /**
   * Input: from ([x, y] in %), to ([x, y] in %), gaps (free y lanes in the middle column), id (board arrow id, if any)
   * Output: string (an SVG path)
   * Processing: A curved arrow between two nodes; one that skips the middle column bends through the nearest free lane.
   *   A switched-on rule overlay can colour it (e.g. red when its API call failed), with the reason as its tooltip.
   * Changed by: Claude Code
   * Date: 2026-11-25
   */
  function edgePath([x1, y1], [x2, y2], gaps, id) {
    const dir = x2 >= x1 ? 1 : -1, sx = x1 + dir * 6.5, ex = x2 - dir * 6.5
    let d
    if (Math.abs(x2 - x1) > 50) {
      const want = (y1 + y2) / 2, lane = gaps.reduce((best, g) => Math.abs(g - want) < Math.abs(best - want) ? g : best, gaps[0])
      d = `M${sx} ${y1} C${sx + dir * 18} ${y1} ${50 - dir * 14} ${lane} 50 ${lane} C${50 + dir * 14} ${lane} ${ex - dir * 18} ${y2} ${ex} ${y2}`
    } else {
      const mx = (sx + ex) / 2
      d = `M${sx} ${y1} C${mx} ${y1} ${mx} ${y2} ${ex} ${y2}`
    }
    const look = id ? window.PlovaneOverlays?.arrowLook?.(id) : null
    const paint = look ? ` class="ov-arrow" style="stroke:${look.color}"` : ''
    const tip = look?.tip ? `<title>${esc(look.tip)}</title>` : ''
    return `<path vector-effect="non-scaling-stroke" marker-end="url(#ov-arrow)" d="${d}"${paint}>${tip}</path>`
  }

  function setApis(on) {
    st.apis = on
    toggle.classList.toggle('on', on)
    place()
    overlay.hidden = !on
    if (on) { $('#selection-popup')?.classList.remove('show'); renderApis(); if (!st.scan) scan() }
  }

  // ------------------------------------------------------------- Events -----

  function focusOn(id, line) {
    if (st.apis) return open(id, line)
    wb.flyTo(id)
    setTimeout(() => open(id, line), 60)
  }

  document.addEventListener('click', async e => {
    const t = e.target.closest('button'); if (!t) return
    const d = t.dataset
    if (d.ovApis !== undefined) return setApis(!st.apis)
    if (d.opClose !== undefined) return hide()
    if (d.opZoom) { setApis(false); return wb.flyTo(d.opZoom) }
    if (d.opEndpoint) return openEndpoint(d.opEndpoint)
    if (d.opGo) return focusOn(d.opGo)
    if (d.opFile && d.opLine) { const id = byPath()[d.opFile]; if (id) focusOn(id, Number(d.opLine)); return }
    if (d.opLine && !d.opOpen) return jump(Number(d.opLine))
    if (d.opOpen) return focusOn(d.opOpen, Number(d.opLine) || 0)
    if (d.opTrace !== undefined) {
      const guess = st.lastTest || guessTestFor(st.open) || 'tests/'
      const ans = window.prompt('Trace which test file? (will run under coverage.py)', guess)
      if (ans) runTrace(ans.trim())
      return
    }
    if (d.opFix) { e.stopPropagation(); return loadFix(d.opFix, d.opFixKind) }
    if (d.opFixInsert) { e.stopPropagation(); return insertFix(d.opFixEntry, d.opFixInto) }
    if (d.ovCommand) { try { await wb.request('command', { method: 'POST', body: JSON.stringify({ command: d.ovCommand }) }); await wb.refresh(); renderApis(); wb.toast('Done.') } catch (err) { wb.toast(err.message, true) } }
  })

  // Esc closes the panel first, then the API diagram; only then does it go up a zoom level.
  window.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName) || $('#modal')?.open) return
    if (panel.classList.contains('show')) { e.stopImmediatePropagation(); e.preventDefault(); hide() }
    else if (st.apis) { e.stopImmediatePropagation(); e.preventDefault(); setApis(false) }
  }, true)

  // Keep the diagram and an open panel in step with the board as it changes.
  setInterval(() => {
    const seq = wb.board?.seq
    if (seq === st.seq) return
    st.seq = seq
    if (st.apis) renderApis()
    if (st.open && !panel.matches(':hover')) st.open.startsWith('ep:') ? renderEndpoint(st.open.slice(3)) : blocks()[st.open] && render(st.open)
  }, 1500)
  window.addEventListener('resize', place)

  place()
  window.PlovaneOpen = { open, openEndpoint, close: hide, apis: setApis, scan }
})()
