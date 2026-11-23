/*
Input: The whiteboard API (window.PlovaneWhiteboard), the board reply, and code.explain, code.translate and security.scan
Output: The open panel (click a block), the Board · APIs · Checks spaces, and the Structure only overlay
Processing: Shows what a clicked block does, its connections, risky lines, tests and code with plain English;
  lists outside APIs and endpoints; lists every risky line and untested file; hides folders and files on request
Changed by: Claude Code
Date: 2026-11-23
*/

// Clicking a block opens a panel beside the board instead of making you zoom: what it does, what
// it uses and what uses it, its risky lines, its tests, and (for a file) the code with plain
// English under each line. Zooming still works as before (scroll, double-click, "Zoom in").
// The canvas toolbar gains a switch between three spaces: Board, APIs and Checks; swipe sideways
// on the APIs or Checks space, or press ⌘1, ⌘2, ⌘3. "Structure only" draws just the services,
// outside APIs and agents with the arrows between them, without the folder and file tree.
(() => {
  const wb = window.PlovaneWhiteboard
  if (!wb) return
  const $ = (s, root = document) => root.querySelector(s)
  const $$ = (s, root = document) => [...root.querySelectorAll(s)]
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`
  const op = (name, args = {}) => wb.request('op', { method: 'POST', body: JSON.stringify({ op: name, args }) }).then(d => d.result || {})

  const KIND_ICON = { agent: '🤖', service: '⚙️', server: '🖥️', external: '🌐', database: '🗄️', queue: '📬', endpoint: '🔌', folder: '📁', group: '🗂️', file: '📄', function: '🔧', class: '🧩', step: '👣', code: '💻', note: '📝' }
  const LANG_ICON = { python: ['Py', '#9CC8FF'], javascript: ['JS', '#FFF07A'], typescript: ['TS', '#8EC5FF'], css: ['#', '#D3BCFF'], html: ['<>', '#FFB899'], markdown: ['¶', '#E4E4EA'], json: ['{}', '#B8F2C8'], cpp: ['C+', '#A9C7FF'], shell: ['$', '#C8F5A0'] }
  const STRUCTURAL = new Set(['agent', 'external', 'service', 'database', 'queue', 'server'])
  const KIND_NAME = { agent: 'AI agent', external: 'External API', service: 'Service', database: 'Database', queue: 'Queue', server: 'Server', folder: 'Folder' }
  const RISK_NAME = { shell: 'Command injection', sql_from_text: 'SQL injection', secret_in_code: 'Secret in code', eval: 'Runs text as code', unsafe_html: 'Unsafe HTML', html_injection: 'Unsafe HTML', pickle: 'Unsafe loading', weak_hash: 'Weak hashing', no_verify: 'TLS check turned off', tls_off: 'TLS check turned off', path_traversal: 'Path from user input' }

  const st = { open: null, space: 0, structure: false, scan: null, risks: {}, explain: {}, lines: {}, filter: '', seq: null, swipe: 0 }

  const icon = b => {
    const lang = b?.kind === 'file' && LANG_ICON[b.meta?.language]
    return lang ? `<span class="lang-icon" style="--c:${lang[1]}">${esc(lang[0])}</span>` : `<span class="kind-icon">${KIND_ICON[b?.kind] || '⬜'}</span>`
  }
  const blocks = () => wb.board?.board?.blocks || {}
  const arrows = () => Object.values(wb.board?.board?.arrows || {}).map(a => ({ from: a.source ?? a.from, to: a.target ?? a.to })).filter(a => blocks()[a.from] && blocks()[a.to])
  const tests = () => wb.board?.overlays?.results?.test_health || {}
  const riskName = kind => RISK_NAME[kind] || String(kind || 'Risk').replace(/_/g, ' ')
  function inside(id, ancestor) { let b = blocks()[id]; while (b) { if (b.id === ancestor) return true; b = blocks()[b.parent] } return false }
  function byPath() { const out = {}; for (const b of Object.values(blocks())) if (b.path) out[b.path] = b.id; return out }

  // ------------------------------------------------------------------ Mount -----

  const area = $('.canvas-area'), viewport = $('#canvas-viewport'), toolbar = $('.canvas-toolbar')
  if (!area || !viewport || !toolbar) return
  const sw = document.createElement('div')
  sw.className = 'ov-switch'
  sw.innerHTML = `<div class="ov-seg" role="tablist" aria-label="Spaces"><span class="ov-seg-pill"></span><button data-ov-space="0" role="tab" title="The whiteboard (⌘1)">▦ Board</button><button data-ov-space="1" role="tab" title="Outside APIs and your endpoints (⌘2)">⇄ APIs</button><button data-ov-space="2" role="tab" title="Risky code and untested files (⌘3)">✓ Checks<em id="ov-checks-count"></em></button></div><button class="ov-toggle" data-ov-structure title="Show only services, outside APIs and agents, without folders and files">⬡ Structure only</button>`
  const toolbarLeft = $('.toolbar-left', toolbar) || toolbar
  toolbarLeft.append(sw)
  const pane = (id, cls = '') => { const el = document.createElement('section'); el.id = id; el.className = `ov-pane ${cls}`; el.innerHTML = '<div class="ov-inner"></div>'; area.append(el); return el }
  const structurePane = pane('ov-structure', 'ov-structure'), apisPane = pane('ov-apis'), checksPane = pane('ov-checks')
  const panel = document.createElement('aside')
  panel.className = 'op-panel'; panel.setAttribute('aria-label', 'What this block does')
  area.append(panel)

  function place() {
    const top = Math.max(viewport.offsetTop, toolbar.offsetTop + toolbar.offsetHeight)
    for (const el of [structurePane, apisPane, checksPane]) el.style.top = `${top}px`
    panel.style.top = `${top + 12}px`
  }

  // ------------------------------------------------------------- Spaces -----

  /**
   * Input: i (number: 0 Board, 1 APIs, 2 Checks)
   * Output: none
   * Processing: Slide the board and the two panes so space i fills the canvas, and move the switch's pill.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function toSpace(i) {
    st.space = Math.max(0, Math.min(2, i))
    place()
    area.scrollLeft = 0
    if (st.space || st.structure) $('#selection-popup')?.classList.remove('show')
    viewport.style.transform = st.space ? `translateX(${-st.space * 100}%)` : ''
    structurePane.style.transform = `translateX(${-st.space * 100}%)`
    apisPane.style.transform = `translateX(${(1 - st.space) * 100}%)`
    checksPane.style.transform = `translateX(${(2 - st.space) * 100}%)`
    apisPane.hidden = st.space !== 1; checksPane.hidden = st.space !== 2
    structurePane.hidden = !st.structure || st.space !== 0
    const buttons = $$('[data-ov-space]', sw), on = buttons[st.space], pill = $('.ov-seg-pill', sw)
    buttons.forEach(b => b.classList.toggle('on', b === on))
    pill.style.width = `${on.offsetWidth}px`; pill.style.transform = `translateX(${on.offsetLeft - 3}px)`
    if (st.space === 1) renderApis()
    if (st.space === 2) { renderChecks(); if (!st.scan) scan() }
    if (st.space) hide()
  }

  // ----------------------------------------------------------- Risk scan -----

  async function scan(loud) {
    try {
      const r = await op('security.scan')
      st.scan = r; st.risks = {}
      for (const w of r.weak_spots || []) (st.risks[w.file] ||= []).push(w)
      $('#ov-checks-count').textContent = r.counts?.high ? String(r.counts.high) : ''
      if (st.space === 2) renderChecks()
      if (st.open) render(st.open)
      if (loud) wb.toast(`Scanned ${plural(r.scanned || 0, 'file')}: ${plural(r.found || 0, 'risky line')}.`)
    } catch (e) { if (loud) wb.toast(e.message, true) }
  }

  // ---------------------------------------------------------- Open panel -----

  function hide() { panel.classList.remove('show'); st.open = null }

  /**
   * Input: id (block id), line (number, optional)
   * Output: none
   * Processing: Open the panel for a block, or close it when the same block is clicked again with it open.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function open(id, line) {
    if (!blocks()[id]) return
    place(); st.open = id; render(id, line)
    panel.classList.add('show')
    if (!st.scan) scan()
  }

  function chips(ids) {
    const list = [...new Set(ids)].filter(x => blocks()[x])
    if (!list.length) return '<div class="op-none">Nothing</div>'
    const shown = list.slice(0, 40)
    return `<div class="op-chips">${shown.map(x => `<button data-op-go="${esc(x)}" title="${esc(blocks()[x].path || blocks()[x].name)}">${icon(blocks()[x])}<span>${esc(blocks()[x].name)}</span></button>`).join('')}${list.length > shown.length ? `<span class="op-none">+${list.length - shown.length} more</span>` : ''}</div>`
  }

  /**
   * Input: id (block id), line (number to jump to, or 0)
   * Output: none
   * Processing: Fill the panel: what the block does, what it uses and what uses it (a folder counts everything inside it),
   *   its risky lines and tests, and for a file the code with plain English under each line.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function render(id, line = 0) {
    const all = blocks(), b = all[id]; if (!b) return hide()
    const isFile = b.kind === 'file' && b.path, isFolder = b.kind === 'folder' || b.kind === 'group'
    const mine = x => isFolder ? inside(x, id) : x === id
    const uses = [], usedBy = []
    for (const a of arrows()) {
      if (mine(a.from) && !mine(a.to)) uses.push(a.to)
      if (mine(a.to) && !mine(a.from)) usedBy.push(a.from)
    }
    const risks = isFile ? (st.risks[b.path] || []).slice().sort((x, y) => x.line - y.line)
      : isFolder ? Object.entries(st.risks).filter(([p]) => p === b.path || p.startsWith((b.path || '') + '/')).flatMap(([, v]) => v) : []
    const test = tests()[id]
    const kids = Object.values(all).filter(x => x.parent === id)
    const untested = isFolder ? Object.entries(tests()).filter(([k, r]) => r.own && r.status === 'untested' && inside(k, id)).length : 0
    const about = b.meta?.summary || (isFile ? st.explain[b.path] : null)
      || (isFolder ? `A folder with ${[kids.filter(k => k.kind === 'folder').length && plural(kids.filter(k => k.kind === 'folder').length, 'folder'), kids.filter(k => k.kind !== 'folder').length && plural(kids.filter(k => k.kind !== 'folder').length, 'file')].filter(Boolean).join(' and ') || 'nothing in it yet'}.` : null)
    const kind = isFile ? (b.meta?.language || 'file') : (KIND_NAME[b.kind] || b.kind)
    panel.innerHTML = `
      <div class="op-head"><span class="op-icon">${icon(b)}</span><div><h3>${esc(b.name)}</h3><div class="op-path">${esc(b.path || kind)}</div></div>
        <div class="op-actions"><button data-op-zoom="${esc(id)}" title="Zoom the board into this block">⤢ Zoom in</button><button data-op-close aria-label="Close" title="Close (Esc)">✕</button></div></div>
      <div class="op-body">
        <p class="op-about${about ? '' : ' loading'}" id="op-about">${esc(about || (isFile ? 'Reading what this file does…' : `${kind[0].toUpperCase()}${kind.slice(1)} on your whiteboard.`))}</p>
        <div class="op-sec"><h4>Connections</h4>
          <div class="op-label">Uses (${new Set(uses).size})</div>${chips(uses)}
          <div class="op-label">Used by (${new Set(usedBy).size})</div>${chips(usedBy)}
          ${isFolder && kids.length ? `<div class="op-label">Inside (${kids.length})</div>${chips(kids.map(k => k.id))}` : ''}</div>
        ${isFile || isFolder ? `<div class="op-sec"><h4>Risky code <span>${risks.length || ''}</span></h4>${risks.length ? risks.slice(0, 30).map(w => `<button class="op-risk ${esc(w.severity)}" ${isFile ? `data-op-line="${w.line}"` : `data-op-file="${esc(w.file)}" data-op-line="${w.line}"`}><b>${isFile ? '' : `${esc(w.file.split('/').pop())} · `}Line ${w.line}: ${esc(w.why)}</b><span>${esc(riskName(w.kind))} · ${esc(w.severity)}</span></button>`).join('') + (risks.length > 30 ? `<div class="op-none">+${risks.length - 30} more in Checks</div>` : '') : `<div class="op-none">${st.scan ? 'Nothing risky found.' : 'Scanning…'}</div>`}</div>` : ''}
        ${test?.own ? `<div class="op-sec"><h4>Tests</h4><span class="op-badge ${test.status === 'tested' ? 'ok' : ''}">${test.status === 'tested' ? '✓ Tested' : 'No tests'}</span></div>` : isFolder && untested ? `<div class="op-sec"><h4>Tests</h4><span class="op-badge">${plural(untested, 'file')} with no tests</span></div>` : ''}
        ${isFile ? `<div class="op-sec"><h4>What each line does</h4><ol class="op-reader" id="op-reader"><li><span class="en">Loading…</span></li></ol></div>` : ''}
      </div>`
    if (isFile) { loadAbout(b); loadLines(b, line) }
  }

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
    el.innerHTML = shown.map(r => {
      const w = risky[r.line], sev = w ? (w.some(x => x.severity === 'high') ? 'high' : w[0].severity) : ''
      const en = ['continues', 'blank'].includes(r.source) || !r.english ? '' : r.english
      return `<li data-line="${r.line}" class="${w ? `risky ${sev}` : ''}">${en ? `<span class="en">${esc(en)}</span>` : ''}<span class="n">${r.line}</span><code>${paint(r.code, lang)}</code>${w ? `<span class="why">⚠ ${w.map(x => esc(x.why)).join(' · ')}</span>` : ''}</li>`
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

  // ------------------------------------------------------ Structure only -----

  /**
   * Input: none
   * Output: none
   * Processing: Draw only services, outside APIs and agents (agents left, services centre, outside APIs right) with the arrows between them.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function renderStructure() {
    const host = $('.ov-inner', structurePane), all = blocks()
    const nodes = Object.values(all).filter(b => STRUCTURAL.has(b.kind)).sort((a, b) => a.name.localeCompare(b.name))
    const ids = new Set(nodes.map(b => b.id)), callers = {}
    const edges = arrows().filter(a => ids.has(a.from) && ids.has(a.to))
    for (const a of arrows()) if (ids.has(a.to) && !ids.has(a.from)) callers[a.to] = (callers[a.to] || 0) + 1
    const head = '<div class="ov-head"><div><h2>Structure only</h2><p>Services, outside APIs and agents, without the folder and file tree. Click one to see what uses it.</p></div><button class="ov-btn" data-ov-structure>Show folders and files</button></div>'
    if (!nodes.length) { host.innerHTML = `${head}<div class="ov-empty"><b>No structure on this whiteboard yet</b>Drag “External API” or “AI agent” onto the board, or run <code>/block add external aws-s3</code>.</div>`; return }
    const column = { agent: 0.15, external: 0.85 }, groups = {}
    for (const b of nodes) (groups[column[b.kind] ?? 0.5] ||= []).push(b.id)
    const pos = {}
    for (const [x, list] of Object.entries(groups)) list.forEach((id, i) => { pos[id] = [Number(x) * 100, ((i + 1) / (list.length + 1)) * 100] })
    const height = Math.max(380, Math.max(...Object.values(groups).map(l => l.length)) * 120)
    host.innerHTML = `${head}<div class="ov-map" style="height:${height}px"><svg viewBox="0 0 100 100" preserveAspectRatio="none">${edges.map(a => { const [x1, y1] = pos[a.from], [x2, y2] = pos[a.to], mx = (x1 + x2) / 2; return `<path vector-effect="non-scaling-stroke" d="M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}"/>` }).join('')}</svg>
      ${nodes.map(b => `<button class="ov-node" data-op-open="${esc(b.id)}" style="left:${pos[b.id][0]}%;top:${pos[b.id][1]}%">${icon(b)}${esc(b.name)}<small>${esc(KIND_NAME[b.kind] || b.kind)}${callers[b.id] ? ` · ${plural(callers[b.id], 'caller')}` : ''}</small></button>`).join('')}</div>`
  }

  // --------------------------------------------------------------- APIs -----

  function renderApis() {
    const host = $('.ov-inner', apisPane), all = blocks()
    const externals = Object.values(all).filter(b => b.kind === 'external')
    const endpoints = Object.values(wb.board?.board?.endpoints || {})
    const calls = id => arrows().filter(a => a.to === id).length
    host.innerHTML = `<div class="ov-head"><div><h2>APIs</h2><p>The outside services your project talks to and the endpoints it offers, kept apart from the code. Swipe or press ⌘1 to go back to the board.</p></div><button class="ov-btn primary" data-ov-command="/endpoint scan">Find API routes in the code</button></div>
      <div class="ov-group">Outside services (${externals.length})</div>
      ${externals.length ? `<div class="ov-list">${externals.map(b => `<button class="ov-row" data-op-open="${esc(b.id)}"><span class="ov-ico">${icon(b)}</span><div><b>${esc(b.name)}</b><span>${calls(b.id) ? `Called from ${plural(calls(b.id), 'place')}` : 'Not connected to any code yet'}</span></div><span class="ov-go">Open ›</span></button>`).join('')}</div>`
        : '<div class="ov-empty"><b>No outside services yet</b>Drag “External API” onto the board, or run <code>/block add external aws-s3</code>.</div>'}
      <div class="ov-group">Your endpoints (${endpoints.length})</div>
      ${endpoints.length ? `<div class="ov-list">${endpoints.map(e => { const [method, ...rest] = String(e.id).split(' '), handler = e.handler && all[e.handler] ? e.handler : ''; return `<button class="ov-row" ${handler ? `data-op-open="${esc(handler)}"` : ''}><span class="sev ${e.status === 'built' ? '' : 'medium'}">${esc(method)}</span><div><b>${esc(rest.join(' ') || e.id)}</b><span>${esc(e.status || '')}${handler ? ` · handled in ${esc(all[handler].name)}` : ''}</span></div><span class="ov-go">${handler ? 'Open ›' : ''}</span></button>` }).join('')}</div>`
        : '<div class="ov-empty"><b>No endpoints found yet</b>“Find API routes in the code” looks for routes such as <code>@app.get("/users")</code>.</div>'}`
  }

  // ------------------------------------------------------------- Checks -----

  /**
   * Input: none
   * Output: none
   * Processing: Counts of risky lines by severity and of untested files, then each finding with its file, line and reason.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function renderChecks() {
    const host = $('.ov-inner', checksPane), all = st.scan?.weak_spots || [], counts = st.scan?.counts || {}, paths = byPath(), f = st.filter
    const untested = Object.entries(tests()).filter(([id, r]) => r.own && r.status === 'untested' && blocks()[id]).map(([id]) => id)
    const stat = (key, n, label) => `<button class="ov-stat ${key}${f === key ? ' on' : ''}" data-ov-filter="${key}"><b>${n}</b><span>${label}</span></button>`
    const groups = { high: [], medium: [], low: [] }
    for (const w of all) if (!f || w.severity === f) (groups[w.severity] || groups.low).push(w)
    const rows = list => `<div class="ov-list">${list.slice(0, 200).map(w => { const id = paths[w.file]; return `<button class="ov-row" ${id ? `data-op-open="${esc(id)}" data-op-line="${w.line}"` : ''}><span class="sev ${esc(w.severity)}">${esc(w.severity)}</span><div><b>${esc(riskName(w.kind))}: ${esc(w.why)}</b><span>${esc(w.file)} · line ${w.line}</span><code>${esc(String(w.code || '').trim())}</code></div><span class="ov-go">${id ? 'Open ›' : ''}</span></button>` }).join('')}</div>`
    const risky = f !== 'untested' ? ['high', 'medium', 'low'].filter(s => groups[s].length).map(s => `<div class="ov-group">${s} risk (${groups[s].length})</div>${rows(groups[s])}`).join('') : ''
    host.innerHTML = `<div class="ov-head"><div><h2>Checks</h2><p>${st.scan ? `${plural(st.scan.scanned || 0, 'file')} scanned for risky code. Click a finding to open that exact line.` : 'Scanning your code for risky lines…'}</p></div><button class="ov-btn primary" data-ov-rescan>Scan again</button></div>
      <div class="ov-stats">${stat('high', counts.high || 0, 'High risk')}${stat('medium', counts.medium || 0, 'Medium risk')}${stat('low', counts.low || 0, 'Low risk')}${stat('untested', untested.length, 'Files with no tests')}</div>
      ${risky || (st.scan && f !== 'untested' ? '<div class="ov-empty"><b>Nothing risky found</b></div>' : '')}
      ${!f || f === 'untested' ? `<div class="ov-group">No tests (${untested.length})</div>${untested.length ? `<div class="ov-list">${untested.slice(0, 200).map(id => `<button class="ov-row" data-op-open="${esc(id)}"><span class="ov-ico">${icon(blocks()[id])}</span><div><b>${esc(blocks()[id].name)}</b><span>${esc(blocks()[id].path || '')}</span></div><span class="ov-go">Open ›</span></button>`).join('')}</div>` : '<div class="ov-empty"><b>Every file has a test</b></div>'}` : ''}`
  }

  // ------------------------------------------------------------- Events -----

  function focusOn(id, line) {
    toSpace(0)
    wb.flyTo(id)
    setTimeout(() => open(id, line), 60)
  }

  document.addEventListener('click', async e => {
    const t = e.target.closest('button'); if (!t) return
    const d = t.dataset
    if (d.ovSpace !== undefined) return toSpace(Number(d.ovSpace))
    if (d.ovStructure !== undefined) {
      st.structure = !st.structure
      $('[data-ov-structure].ov-toggle', sw).classList.toggle('on', st.structure)
      if (st.structure) renderStructure()
      return toSpace(0)
    }
    if (d.opClose !== undefined) return hide()
    if (d.opZoom) return wb.flyTo(d.opZoom)
    if (d.opGo) return focusOn(d.opGo)
    if (d.opFile && d.opLine) { const id = byPath()[d.opFile]; if (id) focusOn(id, Number(d.opLine)); return }
    if (d.opLine && !d.opOpen) return jump(Number(d.opLine))
    if (d.opOpen) { if (st.space === 0 && st.structure) return open(d.opOpen); return focusOn(d.opOpen, Number(d.opLine) || 0) }
    if (d.ovFilter) { st.filter = st.filter === d.ovFilter ? '' : d.ovFilter; return renderChecks() }
    if (d.ovRescan !== undefined) return scan(true)
    if (d.ovCommand) { try { await wb.request('command', { method: 'POST', body: JSON.stringify({ command: d.ovCommand }) }); await wb.refresh(); renderApis(); wb.toast('Done.') } catch (err) { wb.toast(err.message, true) } }
  })

  // Esc closes the panel first; only then does it go up a zoom level.
  window.addEventListener('keydown', e => {
    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)
    if (e.key === 'Escape' && panel.classList.contains('show') && !typing && !$('#modal')?.open) { e.stopImmediatePropagation(); e.preventDefault(); hide() }
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && ['1', '2', '3'].includes(e.key)) { e.preventDefault(); toSpace(Number(e.key) - 1) }
  }, true)

  // A sideways swipe (two fingers on a trackpad) on the APIs or Checks space moves between spaces.
  for (const el of [apisPane, checksPane]) {
    el.addEventListener('wheel', e => {
      if (Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return
      e.preventDefault()
      if (st.swipeLock) return
      st.swipe += e.deltaX
      clearTimeout(st.swipeTimer); st.swipeTimer = setTimeout(() => { st.swipe = 0 }, 220)
      if (Math.abs(st.swipe) > 140) { toSpace(st.space + Math.sign(st.swipe)); st.swipe = 0; st.swipeLock = true; setTimeout(() => { st.swipeLock = false }, 600) }
    }, { passive: false })
  }

  // Keep the panes and an open panel in step with the board as it changes.
  setInterval(() => {
    const seq = wb.board?.seq
    if (seq === st.seq) return
    st.seq = seq
    if (st.structure) renderStructure()
    if (st.space === 1) renderApis()
    if (st.space === 2) renderChecks()
    if (st.open && blocks()[st.open] && !panel.matches(':hover')) render(st.open)
  }, 1500)
  window.addEventListener('resize', () => toSpace(st.space))

  toSpace(0)
  window.PlovaneOpen = { open, close: hide, space: toSpace, scan }
})()
