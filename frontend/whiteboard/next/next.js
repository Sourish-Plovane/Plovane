/*
Input: The whiteboard API (/board, code.translate, code.explain, security.scan) and the person token in the page
Output: The click-to-open whiteboard: tiles you open with a click, a file view, overlays, and the APIs and Checks spaces
Processing: Loads the board, draws the current folder as tiles, opens files and nodes on click, lists APIs and risky code
Changed by: Claude Code
Date: 2026-11-23
*/

// Prototype whiteboard. Instead of zooming, a click opens a block: a folder opens into its
// contents, a file opens into what it does (summary, connections, risks, then the code with plain
// English beside each line). Hovering a tile draws its connections. Three spaces sit side by
// side (Board, APIs, Checks); swipe or use the tab bar. Overlays ("lenses") change what the board
// shows: Structure only hides folders and files and keeps services, external APIs and agents.
(() => {
  const TOKEN = document.querySelector('meta[name="plovane-token"]')?.content || ''
  const API = '/api/v1/whiteboard'
  const $ = (s, root = document) => root.querySelector(s)
  const $$ = (s, root = document) => [...root.querySelectorAll(s)]
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

  const STRUCTURAL = new Set(['agent', 'external', 'service', 'database', 'queue', 'server', 'note'])
  const KIND = {
    folder: ['📁', '#FFE45C'], agent: ['🤖', '#CDB8FF'], external: ['🌐', '#FFB3D9'], service: ['⚙️', '#9ED8FF'],
    database: ['🗄️', '#A8F0C6'], queue: ['📬', '#FFE08A'], server: ['🖥️', '#9ED8FF'], note: ['📝', '#FFF3A6'], file: ['📄', '#E4E4EA'],
  }
  const LANG = {
    python: ['Py', '#9CC8FF'], javascript: ['JS', '#FFF07A'], typescript: ['TS', '#8EC5FF'], css: ['#', '#D3BCFF'],
    html: ['<>', '#FFB899'], markdown: ['¶', '#E4E4EA'], json: ['{}', '#B8F2C8'], cpp: ['C+', '#A9C7FF'], shell: ['$', '#C8F5A0'],
  }
  const LENSES = [
    { id: 'structure', icon: '⬡', label: 'Structure only', tip: 'Hide folders and files; show services, external APIs, agents and the arrows between them' },
    { id: 'risks', icon: '!', label: 'Risky code', tip: 'Outline blocks that contain risky code' },
    { id: 'tests', icon: '✓', label: 'Tests', tip: 'Stripe the files no test covers' },
  ]

  const st = {
    data: null, seq: null, blocks: {}, kids: {}, arrows: [], byPath: {}, risks: {}, scan: null, riskCount: {}, untested: {},
    explain: {}, lines: {}, lenses: new Set(load('lenses', ['risks'])), find: '', space: 0, checkFilter: '', prevDepth: 0,
  }

  function load(key, fallback) { try { return JSON.parse(localStorage.getItem('plovane-next-' + key)) ?? fallback } catch (_) { return fallback } }
  function save(key, value) { try { localStorage.setItem('plovane-next-' + key, JSON.stringify(value)) } catch (_) { } }

  /**
   * Input: path (string), body (object | undefined)
   * Output: Promise of the envelope's data
   * Processing: Call the whiteboard API with the person token; throw the server's readable message on failure.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  async function api(path, body) {
    let reply
    try {
      reply = await fetch(`${API}/${path}`, {
        method: body ? 'POST' : 'GET',
        headers: { 'Content-Type': 'application/json', ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}) },
        body: body ? JSON.stringify(body) : undefined,
      })
    } catch (_) { throw new Error("Can't reach the whiteboard server. Start it with: plovane open") }
    const json = await reply.json().catch(() => ({}))
    if (!json.ok) throw new Error(json.error?.message || `The server answered ${reply.status}`)
    return json.data
  }
  const op = (name, args = {}) => api('op', { op: name, args }).then(d => d.result || {})

  function toast(text) {
    const el = $('#toast'); el.textContent = text; el.hidden = false
    clearTimeout(toast.t); toast.t = setTimeout(() => { el.hidden = true }, 3200)
  }

  // ------------------------------------------------------------------ Data -----

  /**
   * Input: data (the /board reply)
   * Output: none
   * Processing: Index blocks by parent and path, normalise arrows, and roll test status up to folders.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function index(data) {
    st.data = data; st.seq = data.seq
    st.blocks = data.board.blocks || {}
    st.kids = {}; st.byPath = {}
    for (const b of Object.values(st.blocks)) {
      (st.kids[b.parent || ''] ||= []).push(b.id)
      if (b.path) st.byPath[b.path] = b.id
    }
    st.arrows = Object.values(data.board.arrows || {}).map(a => ({ id: a.id, from: a.source ?? a.from, to: a.target ?? a.to, type: a.type, endpoint: a.endpoint }))
      .filter(a => st.blocks[a.from] && st.blocks[a.to])
    st.untested = {}
    const tests = data.overlays?.results?.test_health || {}
    for (const [id, r] of Object.entries(tests)) {
      if (r.own && r.status === 'untested' && st.blocks[id]) for (const up of chain(id)) st.untested[up] = (st.untested[up] || 0) + 1
    }
    st.tests = tests
    $('#project-name').textContent = (data.project || 'Plovane').split(/[\\/]/).filter(Boolean).pop() || 'Plovane'
    rollRisks()
  }

  function chain(id) { const out = []; let b = st.blocks[id]; while (b) { out.push(b.id); b = st.blocks[b.parent] } return out }
  function rollRisks() {
    st.riskCount = {}
    for (const [path, items] of Object.entries(st.risks)) {
      const id = st.byPath[path]; if (!id) continue
      const high = items.filter(x => x.severity === 'high').length
      for (const up of chain(id)) { const c = (st.riskCount[up] ||= { all: 0, high: 0 }); c.all += items.length; c.high += high }
    }
  }
  // The child of container that holds block id (itself, or the ancestor directly under container).
  function childAt(container, id) {
    let b = st.blocks[id]
    while (b) { if ((b.parent || '') === container) return b.id; b = st.blocks[b.parent] }
    return null
  }
  function sortKids(ids) {
    const rank = { agent: 0, external: 1, service: 2, database: 3, folder: 4, file: 5 }
    return [...ids].sort((a, b) => ((rank[st.blocks[a].kind] ?? 6) - (rank[st.blocks[b].kind] ?? 6)) || st.blocks[a].name.localeCompare(st.blocks[b].name))
  }
  function glyph(b, cls = '') {
    const lang = b.kind === 'file' ? LANG[b.meta?.language] : null
    const [mark, color] = lang || KIND[b.kind] || KIND.file
    return `<span class="glyph ${cls}" style="--c:${color}">${esc(mark)}</span>`
  }

  async function refresh(force) {
    try {
      const data = await api('board')
      if (!force && data.seq === st.seq) return
      index(data); render()
    } catch (e) { if (!st.data) showOffline(e.message) }
  }

  async function scan(quiet) {
    try {
      const r = await op('security.scan')
      st.scan = r; st.risks = {}
      for (const w of r.weak_spots || []) (st.risks[w.file] ||= []).push(w)
      rollRisks(); render()
      if (!quiet) toast(`Scanned ${plural(r.scanned || 0, 'file')}: ${plural(r.found || 0, 'risky line')}.`)
    } catch (e) { if (!quiet) toast(e.message) }
  }

  // ------------------------------------------------------------- Routing -----
  // The hash says what is open: #c=<folder id>, #f=<block id>&l=<line>, or nothing for the project.
  function route() {
    const p = new URLSearchParams(location.hash.slice(1))
    return { container: p.get('c') || '', open: p.get('f') || '', line: Number(p.get('l')) || 0 }
  }
  function go(hash) { if (location.hash.slice(1) !== hash) location.hash = hash; else render() }
  function openBlock(id, line) {
    const b = st.blocks[id]; if (!b) return
    st.find = ''; $('#find').value = ''
    if (b.kind === 'folder' && !st.lenses.has('structure')) go(`c=${encodeURIComponent(id)}`)
    else go(`f=${encodeURIComponent(id)}${line ? `&l=${line}` : ''}`)
    toSpace(0)
  }
  function back() {
    const r = route()
    const at = r.open || r.container
    const parent = st.blocks[at]?.parent
    go(parent && !st.lenses.has('structure') ? `c=${encodeURIComponent(parent)}` : '')
  }

  // ------------------------------------------------------------ Rendering -----

  function render() {
    if (!st.data) return
    renderLenses(); renderTrail(); renderBoard(); renderApis(); renderChecks()
  }

  function renderLenses() {
    const n = { structure: Object.values(st.blocks).filter(b => STRUCTURAL.has(b.kind)).length, risks: st.scan?.found ?? '', tests: Object.values(st.tests || {}).filter(r => r.own && r.status === 'untested').length }
    $('#lenses').innerHTML = LENSES.map(l => `<button class="lens${st.lenses.has(l.id) ? ' on' : ''}" data-lens="${l.id}" title="${esc(l.tip)}" aria-pressed="${st.lenses.has(l.id)}"><i>${l.icon}</i>${esc(l.label)}${n[l.id] !== '' ? ` <small>${n[l.id]}</small>` : ''}</button>`).join('')
  }

  function renderTrail() {
    const r = route(), at = r.open || r.container
    const path = st.lenses.has('structure') ? (r.open ? [r.open] : []) : chain(at).reverse()
    $('#trail').innerHTML = path.map(id => `<i>›</i><button data-open="${esc(id)}">${esc(st.blocks[id]?.name || id)}</button>`).join('')
    $('#trail').scrollLeft = 1e6
  }

  /**
   * Input: none (reads the route, lenses and search)
   * Output: none
   * Processing: Fill the Board space with search results, the structure map, an opened block, or a folder's tiles; animate in or out.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function renderBoard() {
    const host = $('#board'), r = route()
    host.classList.toggle('lens-risks', st.lenses.has('risks'))
    host.classList.toggle('lens-tests', st.lenses.has('tests'))
    if (st.find) return renderSearch(host)
    if (r.open && st.blocks[r.open]) renderOpened(host, r.open, r.line)
    else if (st.lenses.has('structure')) renderStructure(host)
    else renderFolder(host, st.blocks[r.container] ? r.container : '')
    const depth = r.open ? chain(r.open).length + 1 : chain(r.container).length
    if (depth !== st.prevDepth && render.ready) {
      host.classList.remove('opening', 'closing'); void host.offsetWidth
      host.classList.add(depth > st.prevDepth ? 'opening' : 'closing')
    }
    st.prevDepth = depth; render.ready = true
  }

  function tile(id, i, extra = '') {
    const b = st.blocks[id], kids = st.kids[id] || [], risk = st.riskCount[id], untested = st.untested[id] || 0
    const folders = kids.filter(k => st.blocks[k].kind === 'folder').length, files = kids.length - folders
    const sub = b.meta?.summary || (b.kind === 'folder' ? [folders && plural(folders, 'folder'), files && plural(files, 'file')].filter(Boolean).join(' · ') || 'Empty folder'
      : b.kind === 'file' ? (b.path || '') : { agent: 'AI agent', external: 'External API', service: 'Service', database: 'Database', queue: 'Queue' }[b.kind] || b.kind)
    const badges = []
    if (risk) badges.push(`<span class="badge ${risk.high ? 'risk' : 'warn'}" title="Risky lines inside">⚠ ${risk.all}</span>`)
    if (b.kind === 'file' && st.tests?.[id]?.own) badges.push(st.tests[id].status === 'tested' ? '<span class="badge ok">✓ tested</span>' : '<span class="badge no">no tests</span>')
    else if (b.kind === 'folder' && untested) badges.push(`<span class="badge no">${untested} untested</span>`)
    if (extra) badges.push(extra)
    const cls = ['tile', risk ? 'has-risk' : '', (b.kind === 'file' && st.tests?.[id]?.status === 'untested') ? 'untested' : ''].join(' ')
    return `<button class="${cls}" data-open="${esc(id)}" data-tile="${esc(id)}" style="--i:${Math.min(i, 30)}"><span class="tile-top">${glyph(b)}${b.kind === 'folder' ? '<span class="badge link">open ›</span>' : ''}</span><b>${esc(b.name)}</b><p>${esc(sub)}</p><span class="badges">${badges.join('')}</span></button>`
  }

  function renderFolder(host, container) {
    const ids = sortKids(st.kids[container] || [])
    const b = st.blocks[container]
    const links = {}, outside = {}
    for (const a of st.arrows) {
      const x = childAt(container, a.from), y = childAt(container, a.to)
      if (x && y && x !== y) { const key = x < y ? `${x}|${y}` : `${y}|${x}`; links[key] = (links[key] || 0) + 1 }
      else if (x && !y) outside[x] = (outside[x] || 0) + 1
      else if (y && !x) outside[y] = (outside[y] || 0) + 1
    }
    st.links = links
    const degree = {}
    for (const key of Object.keys(links)) for (const id of key.split('|')) degree[id] = (degree[id] || 0) + 1
    const title = b ? b.name : 'Your project'
    const sub = b ? (b.meta?.summary || b.path || '') : 'Click anything to open it. Hover to see what it connects to.'
    host.innerHTML = `<div class="head"><div><h1>${esc(title)}</h1><p>${esc(sub)}</p></div>${b ? `<button class="chip-btn" data-summaries="${esc(b.path || '')}" title="Give each file here a one-line summary (free, no AI)">Write summaries</button>` : ''}</div>
      ${ids.length ? `<div class="stage" id="stage"><svg class="links" id="links"></svg><div class="grid">${ids.map((id, i) => tile(id, i, degree[id] || outside[id] ? `<span class="badge link" title="Connections">↔ ${(degree[id] || 0) + (outside[id] || 0)}</span>` : '')).join('')}</div></div>`
        : '<div class="empty"><b>Nothing here yet</b>Import a project from the main whiteboard, or run <code>/project import</code>.</div>'}`
  }

  function renderSearch(host) {
    const q = st.find.toLowerCase()
    const hits = Object.values(st.blocks).filter(b => b.name.toLowerCase().includes(q) || (b.path || '').toLowerCase().includes(q))
      .sort((a, b) => (a.name.toLowerCase().startsWith(q) ? 0 : 1) - (b.name.toLowerCase().startsWith(q) ? 0 : 1) || a.name.length - b.name.length).slice(0, 60)
    host.innerHTML = `<div class="head"><div><h1>“${esc(st.find)}”</h1><p>${plural(hits.length, 'match')}${hits.length === 60 ? ' (first 60)' : ''}. Enter opens the first.</p></div></div>
      ${hits.length ? `<div class="grid">${hits.map((b, i) => tile(b.id, i)).join('')}</div>` : '<div class="empty"><b>No matches</b>Try part of a file or folder name.</div>'}`
  }

  /**
   * Input: host (element)
   * Output: none
   * Processing: The Structure only overlay: services, external APIs and agents on one map, with the arrows between them; no folders or files.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function renderStructure(host) {
    const nodes = sortKids(Object.values(st.blocks).filter(b => STRUCTURAL.has(b.kind)).map(b => b.id))
    const ids = new Set(nodes)
    // Files that call a structural block count as a link from the block's nearest structural neighbour; keep only node-to-node arrows here.
    const edges = st.arrows.filter(a => ids.has(a.from) && ids.has(a.to))
    const callers = {}
    for (const a of st.arrows) if (ids.has(a.to) && !ids.has(a.from)) callers[a.to] = (callers[a.to] || 0) + 1
    if (!nodes.length) {
      host.innerHTML = `<div class="head"><div><h1>Structure</h1><p>Only services, external APIs and agents, without folders or files.</p></div></div><div class="empty"><b>No structure yet</b>Add an external API or a service in the main whiteboard, or run <code>/block add external aws-s3</code>.</div>`
      return
    }
    const cols = { agent: 0.14, service: 0.5, server: 0.5, database: 0.5, queue: 0.5, note: 0.5, external: 0.86 }
    const groups = {}
    for (const id of nodes) (groups[cols[st.blocks[id].kind] ?? 0.5] ||= []).push(id)
    const pos = {}
    for (const [x, list] of Object.entries(groups)) list.forEach((id, i) => { pos[id] = [Number(x) * 100, ((i + 1) / (list.length + 1)) * 100] })
    const height = Math.max(420, Math.max(...Object.values(groups).map(l => l.length)) * 130)
    host.innerHTML = `<div class="head"><div><h1>Structure</h1><p>Services, external APIs and agents only. Folders and files are hidden; click a block to see what uses it.</p></div></div>
      <div class="map" style="height:${height}px"><svg viewBox="0 0 100 100" preserveAspectRatio="none">${edges.map(a => { const [x1, y1] = pos[a.from], [x2, y2] = pos[a.to]; const mx = (x1 + x2) / 2; return `<path vector-effect="non-scaling-stroke" d="M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}"/>` }).join('')}</svg>
      ${nodes.map((id, i) => { const b = st.blocks[id]; return `<button class="node" data-open="${esc(id)}" style="left:${pos[id][0]}%;top:${pos[id][1]}%;animation-delay:${i * 40}ms">${glyph(b)}${esc(b.name)}<small>${esc(b.kind === 'external' ? 'External API' : b.kind === 'agent' ? 'AI agent' : b.kind)}${callers[id] ? ` · ${plural(callers[id], 'caller')}` : ''}</small></button>` }).join('')}</div>`
  }

  /**
   * Input: host (element), id (block id), line (number)
   * Output: none
   * Processing: An opened block: what it does, every connection, its risks and tests, then (for a file) the code with plain English beside each line.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function renderOpened(host, id, line) {
    const b = st.blocks[id], isFile = b.kind === 'file' && b.path
    const uses = st.arrows.filter(a => a.from === id).map(a => a.to), usedBy = st.arrows.filter(a => a.to === id).map(a => a.from)
    const risks = isFile ? (st.risks[b.path] || []).slice().sort((x, y) => x.line - y.line) : []
    const test = st.tests?.[id]
    const about = b.meta?.summary || (isFile ? st.explain[b.path] : null)
    const chips = list => list.length ? `<div class="conn">${[...new Set(list)].map(x => `<button data-open="${esc(x)}" title="${esc(st.blocks[x].path || st.blocks[x].name)}">${glyph(st.blocks[x], 'small')}<span>${esc(st.blocks[x].name)}</span></button>`).join('')}</div>` : '<div class="none">Nothing</div>'
    const kindName = isFile ? (b.meta?.language || 'file') : ({ agent: 'AI agent', external: 'External API' }[b.kind] || b.kind)
    host.innerHTML = `
      <div class="file-hero">${glyph(b)}<div><h1>${esc(b.name)}</h1><div class="path">${esc(b.path || kindName)}</div>
        <p class="about${about ? '' : ' loading'}" id="about">${esc(about || (isFile ? 'Reading what this file does…' : `${kindName[0].toUpperCase()}${kindName.slice(1)} on your whiteboard.`))}</p></div>
        <button class="chip-btn" data-back>‹ Back</button></div>
      <div class="cols">
        <div>${isFile ? `<div class="card"><h2>What each line does <span>${risks.length ? `⚠ ${plural(risks.length, 'risky line')}` : ''}</span></h2><div class="reader-head"><span>In plain English</span><span></span><span>Code</span></div><ol class="reader" id="reader"><li><span class="en">Loading…</span></li></ol></div>`
          : `<div class="card"><h2>Used by</h2>${chips(usedBy)}</div>`}</div>
        <aside class="side">
          <div class="card"><h2>Connections</h2>
            <div class="conn-label">Uses (${new Set(uses).size})</div>${chips(uses)}
            <div class="conn-label">Used by (${new Set(usedBy).size})</div>${chips(usedBy)}</div>
          ${isFile ? `<div class="card"><h2>Risky code <span>${risks.length || ''}</span></h2>${risks.length ? risks.map(w => `<button class="risk-item ${esc(w.severity)}" data-line="${w.line}"><b>Line ${w.line}: ${esc(w.why)}</b><span>${esc(riskName(w.kind))} · ${esc(w.severity)}</span></button>`).join('') : `<div class="none">${st.scan ? 'Nothing risky found.' : 'Scanning…'}</div>`}</div>` : ''}
          ${test?.own ? `<div class="card"><h2>Tests</h2><span class="badge ${test.status === 'tested' ? 'ok' : 'no'}">${test.status === 'tested' ? '✓ tested' : 'no tests'}</span><p class="none" style="margin:8px 0 0">${esc(test.items?.[0]?.message || '')}</p></div>` : ''}
        </aside>
      </div>`
    if (isFile) { loadAbout(b); loadLines(b, line) }
  }

  async function loadAbout(b) {
    if (b.meta?.summary || st.explain[b.path]) return
    try {
      const r = await op('code.explain', { target: b.path })
      const text = String(r.explanation || '').split('\n')[0]
      st.explain[b.path] = text
    } catch (e) { st.explain[b.path] = '' }
    const el = $('#about'); if (el && route().open === b.id) { el.textContent = st.explain[b.path] || 'No description yet.'; el.classList.remove('loading') }
  }

  /**
   * Input: b (file block), line (number to jump to, or 0)
   * Output: none
   * Processing: Fetch the line-by-line English, then draw English | line number | code, marking each risky line with why it is risky.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  async function loadLines(b, line) {
    let rows = st.lines[b.path]
    if (!rows) {
      try { rows = st.lines[b.path] = (await op('code.translate', { file: b.path })).lines || [] }
      catch (e) { const el = $('#reader'); if (el) el.innerHTML = `<li><span class="en">${esc(e.message)}</span></li>`; return }
    }
    const el = $('#reader'); if (!el || route().open !== b.id) return
    const risky = {}
    for (const w of st.risks[b.path] || []) (risky[w.line] ||= []).push(w)
    const lang = b.meta?.language || ''
    const shown = rows.slice(0, 2000)
    el.innerHTML = shown.map(r => {
      const w = risky[r.line], sev = w ? (w.some(x => x.severity === 'high') ? 'high' : w[0].severity) : ''
      const en = r.source === 'continues' || r.source === 'blank' || !r.english ? '' : r.english
      return `<li data-line="${r.line}" class="${w ? `risky ${sev}` : ''}"><span class="en${en ? '' : ' cont'}">${esc(en)}</span><span class="n">${r.line}</span><code>${paint(r.code, lang)}</code>${w ? `<span class="why">⚠ ${w.map(x => esc(x.why)).join(' · ')}</span>` : ''}</li>`
    }).join('') + (rows.length > shown.length ? `<li><span class="en">…${rows.length - shown.length} more lines</span></li>` : '')
    if (line) jumpTo(line)
  }

  function jumpTo(line) {
    const li = $(`#reader li[data-line="${line}"]`); if (!li) return
    li.scrollIntoView({ block: 'center', behavior: 'smooth' })
    li.classList.remove('flash'); void li.offsetWidth; li.classList.add('flash')
  }

  const KEYWORDS = 'def|class|return|if|elif|else|for|while|import|from|as|with|try|except|finally|raise|async|await|function|const|let|var|new|export|default|in|not|and|or|is|None|True|False|null|undefined|true|false|this|self|lambda|yield|pass|break|continue|switch|case|interface|type|extends|public|private|static'
  function paint(code, lang) {
    const hash = ['python', 'shell', 'yaml', 'ruby', 'toml'].includes(lang)
    const re = new RegExp(`("(?:[^"\\\\]|\\\\.)*"|'(?:[^'\\\\]|\\\\.)*'|\`[^\`]*\`)|(${hash ? '#' : '\\/\\/'}.*$)|\\b(${KEYWORDS})\\b|\\b(\\d+(?:\\.\\d+)?)\\b`, 'g')
    let out = '', last = 0
    for (const m of String(code || '').matchAll(re)) {
      out += esc(code.slice(last, m.index))
      const cls = m[1] ? 'tok-s' : m[2] ? 'tok-c' : m[3] ? 'tok-k' : 'tok-n'
      out += `<span class="${cls}">${esc(m[0])}</span>`; last = m.index + m[0].length
    }
    return out + esc(String(code || '').slice(last))
  }
  function riskName(kind) {
    return { shell: 'Command injection', sql_from_text: 'SQL injection', secret_in_code: 'Secret in code', eval: 'Runs text as code', html_injection: 'Unsafe HTML',
      unsafe_html: 'Unsafe HTML', pickle: 'Unsafe loading', weak_hash: 'Weak hashing', no_verify: 'TLS check turned off', path_traversal: 'Path from user input' }[kind] || String(kind || 'Risk').replace(/_/g, ' ')
  }

  /**
   * Input: none
   * Output: none
   * Processing: The APIs space: external APIs and who calls them, then the project's own endpoints.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function renderApis() {
    const externals = Object.values(st.blocks).filter(b => b.kind === 'external')
    const endpoints = Object.values(st.data.board.endpoints || {})
    $('#count-apis').textContent = ''
    const callers = id => st.arrows.filter(a => a.to === id).length
    $('#apis').innerHTML = `<div class="head"><div><h1>APIs</h1><p>Every API your project talks to, and the ones it offers, kept apart from the code so it stays readable.</p></div><button class="chip-btn primary" data-command="/endpoint scan">Find endpoints</button></div>
      <div class="group-title">Outside services (${externals.length})</div>
      ${externals.length ? `<div class="list">${externals.map(b => `<button class="row" data-open="${esc(b.id)}">${glyph(b)}<div><b>${esc(b.name)}</b><span>${callers(b.id) ? `Called from ${plural(callers(b.id), 'place')}` : 'Not connected to any code yet'}</span></div><span class="badge link">open ›</span></button>`).join('')}</div>`
        : '<div class="empty"><b>No outside services yet</b>Drag “External API” onto the main whiteboard, or run <code>/block add external aws-s3</code>.</div>'}
      <div class="group-title">Your endpoints (${endpoints.length})</div>
      ${endpoints.length ? `<div class="list">${endpoints.map(e => { const [method, ...rest] = String(e.id).split(' '); const handler = e.handler && st.blocks[e.handler] ? e.handler : ''; return `<button class="row" ${handler ? `data-open="${esc(handler)}"` : ''}><span class="sev ${e.status === 'built' ? 'low' : 'medium'}">${esc(method)}</span><div><b>${esc(rest.join(' ') || e.id)}</b><span>${esc(e.status || '')}${handler ? ` · handled in ${esc(st.blocks[handler].name)}` : ''}</span></div><span class="badge link">${handler ? 'open ›' : ''}</span></button>` }).join('')}</div>`
        : '<div class="empty"><b>No endpoints found yet</b>Press “Find endpoints” to look for routes such as <code>@app.get("/users")</code> in your code.</div>'}`
  }

  /**
   * Input: none
   * Output: none
   * Processing: The Checks space: counts of risky lines and untested files, then each finding with its line and reason; a tap opens the line.
   * Changed by: Claude Code
   * Date: 2026-11-23
   */
  function renderChecks() {
    const all = st.scan?.weak_spots || [], counts = st.scan?.counts || {}
    const untested = Object.entries(st.tests || {}).filter(([id, r]) => r.own && r.status === 'untested' && st.blocks[id]).map(([id]) => id)
    const high = counts.high || 0
    $('#count-checks').textContent = high ? String(high) : ''
    const f = st.checkFilter
    const stat = (key, n, label, color) => `<button class="stat ${color}${f === key ? ' on' : ''}" data-filter="${key}"><b>${n}</b><span>${label}</span></button>`
    const showRisks = !f || ['high', 'medium', 'low'].includes(f)
    const risks = all.filter(w => !f || w.severity === f)
    const bySev = { high: [], medium: [], low: [] }
    for (const w of risks) (bySev[w.severity] || bySev.low).push(w)
    const rows = list => `<div class="list">${list.map(w => { const id = st.byPath[w.file]; return `<button class="row" ${id ? `data-open="${esc(id)}" data-line="${w.line}"` : ''}><span class="sev ${esc(w.severity)}">${esc(w.severity)}</span><div><b>${esc(riskName(w.kind))}: ${esc(w.why)}</b><span>${esc(w.file)} · line ${w.line}</span><code>${esc(String(w.code || '').trim())}</code></div><span class="badge link">${id ? 'open ›' : ''}</span></button>` }).join('')}</div>`
    $('#checks').innerHTML = `<div class="head"><div><h1>Checks</h1><p>${st.scan ? `${plural(st.scan.scanned || 0, 'file')} scanned for risky code. Tap a finding to open the exact line.` : 'Scanning your code for risky lines…'}</p></div><button class="chip-btn primary" data-rescan>Scan again</button></div>
      <div class="stats">${stat('high', high, 'High risk', high ? 'red' : '')}${stat('medium', counts.medium || 0, 'Medium risk', counts.medium ? 'orange' : '')}${stat('low', counts.low || 0, 'Low risk', '')}${stat('untested', untested.length, 'Files with no tests', untested.length ? 'yellow' : 'green')}</div>
      ${showRisks ? ['high', 'medium', 'low'].filter(s => bySev[s].length).map(s => `<div class="group-title">${s} risk (${bySev[s].length})</div>${rows(bySev[s].slice(0, 200))}`).join('') || (st.scan ? '<div class="empty"><b>Nothing risky found</b>No risky patterns in the scanned files.</div>' : '') : ''}
      ${!f || f === 'untested' ? `<div class="group-title">No tests (${untested.length})</div>${untested.length ? `<div class="list">${untested.slice(0, 200).map(id => `<button class="row" data-open="${esc(id)}">${glyph(st.blocks[id])}<div><b>${esc(st.blocks[id].name)}</b><span>${esc(st.blocks[id].path || '')}</span></div><span class="badge link">open ›</span></button>`).join('')}</div>` : '<div class="empty"><b>Every file has a test</b></div>'}` : ''}`
  }

  function showOffline(message) {
    $('#board').innerHTML = `<div class="empty"><b>Can't load the whiteboard</b>${esc(message)}</div>`
  }

  // ------------------------------------------------------------- Spaces ------

  function toSpace(i) {
    const spaces = $('#spaces')
    spaces.scrollTo({ left: i * spaces.clientWidth, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
    setSpace(i)
  }
  function setSpace(i) {
    st.space = i
    $('#tab-pill').style.transform = `translateX(${i * 100}%)`
    $$('#tabbar button').forEach((b, j) => b.classList.toggle('on', j === i))
  }

  // Hovering a tile lights it, its neighbours, and the curves between them.
  function hover(id) {
    const stage = $('#stage'), svg = $('#links'); if (!stage || !svg) return
    if (!id) { stage.classList.remove('hovering'); $$('.tile.lit', stage).forEach(t => t.classList.remove('lit', 'me')); return }
    const box = stage.getBoundingClientRect(), center = el => { const r = el.getBoundingClientRect(); return [r.left - box.left + r.width / 2, r.top - box.top + r.height / 2] }
    const me = stage.querySelector(`[data-tile="${CSS.escape(id)}"]`); if (!me) return
    const near = Object.keys(st.links || {}).map(k => k.split('|')).filter(([a, b]) => a === id || b === id).map(([a, b]) => a === id ? b : a)
    $$('.tile', stage).forEach(t => t.classList.toggle('lit', t === me || near.includes(t.dataset.tile)))
    me.classList.add('me')
    // Each curve leaves from the side of one tile that faces the other, so it never crosses the text.
    const rect = el => { const r = el.getBoundingClientRect(); return { l: r.left - box.left, t: r.top - box.top, r: r.right - box.left, b: r.bottom - box.top } }
    const edge = (a, b) => {
      const [ax, ay] = [(a.l + a.r) / 2, (a.t + a.b) / 2], [bx, by] = [(b.l + b.r) / 2, (b.t + b.b) / 2]
      if (Math.abs(bx - ax) * (a.b - a.t) > Math.abs(by - ay) * (a.r - a.l)) return bx > ax ? [a.r, ay, 1, 0] : [a.l, ay, -1, 0]
      return by > ay ? [ax, a.b, 0, 1] : [ax, a.t, 0, -1]
    }
    const mine = rect(me)
    svg.innerHTML = near.map(other => {
      const el = stage.querySelector(`[data-tile="${CSS.escape(other)}"]`); if (!el) return ''
      const theirs = rect(el), [x1, y1, dx1, dy1] = edge(mine, theirs), [x2, y2, dx2, dy2] = edge(theirs, mine), k = 70
      const d = `M${x1} ${y1} C${x1 + dx1 * k} ${y1 + dy1 * k} ${x2 + dx2 * k} ${y2 + dy2 * k} ${x2} ${y2}`
      return `<path class="halo show" d="${d}"/><path class="show" d="${d}"/><circle class="show" cx="${x2}" cy="${y2}" r="5"/>`
    }).join('')
    stage.classList.toggle('hovering', true)
  }

  // ------------------------------------------------------------- Events ------

  document.addEventListener('click', async e => {
    const t = e.target.closest('button'); if (!t) return
    if (t.dataset.lens) {
      const id = t.dataset.lens
      st.lenses.has(id) ? st.lenses.delete(id) : st.lenses.add(id)
      save('lenses', [...st.lenses])
      if (id === 'structure') go('')
      render(); return
    }
    if (t.dataset.space) return toSpace(Number(t.dataset.space))
    if (t.dataset.go === 'root') return go('')
    if (t.dataset.back !== undefined) return back()
    if (t.dataset.rescan !== undefined) return scan()
    if (t.dataset.filter) { st.checkFilter = st.checkFilter === t.dataset.filter ? '' : t.dataset.filter; return renderChecks() }
    if (t.dataset.line && !t.dataset.open) return jumpTo(Number(t.dataset.line))
    if (t.dataset.open) return openBlock(t.dataset.open, Number(t.dataset.line) || 0)
    if (t.dataset.summaries !== undefined) {
      try { const r = await op('code.summary', { target: t.dataset.summaries }); toast(`Summaries written: ${(r.counts?.free || 0) + (r.counts?.ai || 0)} new, ${r.counts?.cached || 0} kept.`); refresh(true) } catch (err) { toast(err.message) }
      return
    }
    if (t.dataset.command) {
      try { await api('command', { command: t.dataset.command }); toast('Done.'); refresh(true) } catch (err) { toast(err.message) }
    }
  })
  document.addEventListener('pointerover', e => { const t = e.target.closest('[data-tile]'); if (t && $('#stage')?.contains(t)) hover(t.dataset.tile) })
  document.addEventListener('pointerout', e => { const t = e.target.closest('[data-tile]'); if (t && !t.contains(e.relatedTarget)) hover(null) })
  document.addEventListener('focusin', e => { const t = e.target.closest('[data-tile]'); hover(t ? t.dataset.tile : null) })

  $('#find').addEventListener('input', e => { st.find = e.target.value.trim(); renderBoard(); if (st.find) toSpace(0) })
  $('#find').addEventListener('keydown', e => {
    if (e.key === 'Enter') { const first = $('#board .tile'); if (first) openBlock(first.dataset.open) }
    if (e.key === 'Escape') { e.target.value = ''; st.find = ''; e.target.blur(); renderBoard() }
  })
  document.addEventListener('keydown', e => {
    if (e.target.closest('input, textarea')) return
    if (e.key === '/') { e.preventDefault(); $('#find').focus() }
    else if (e.key === 'Escape' || e.key === 'Backspace') { e.preventDefault(); back() }
    else if ((e.metaKey || e.ctrlKey) && ['1', '2', '3'].includes(e.key)) { e.preventDefault(); toSpace(Number(e.key) - 1) }
  })
  window.addEventListener('hashchange', () => { renderTrail(); renderBoard(); $('#space-board').scrollTop = 0 })
  const spaces = $('#spaces')
  spaces.addEventListener('scroll', () => { const i = Math.round(spaces.scrollLeft / spaces.clientWidth); if (i !== st.space) setSpace(i) }, { passive: true })
  window.addEventListener('resize', () => { spaces.scrollLeft = st.space * spaces.clientWidth })
  window.addEventListener('focus', () => refresh())
  setInterval(() => { if (!document.hidden) refresh() }, 15000)

  setSpace(0)
  $('#board').innerHTML = `<div class="head"><div><h1>Opening your project…</h1><p>Reading folders, files and connections.</p></div></div><div class="grid">${Array.from({ length: 8 }, (_, i) => `<div class="tile skeleton" style="--i:${i}"><span class="glyph"></span><b>&nbsp;</b><p>&nbsp;</p></div>`).join('')}</div>`
  refresh(true).then(() => scan(true))
})()
