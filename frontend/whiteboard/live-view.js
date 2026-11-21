// Live view for the whiteboard: what agents are doing, how requests are going, and readable code.
// Runs after every redraw of whiteboard.js (window.PlovaneLive.afterRender) and decorates what it drew:
//   - a file an agent is changing glows in that agent's colour, with a pulsing agent badge
//   - a folder an agent is orchestrating gets a banner: who, what, and how many files so far
//   - file tiles show their one-line summary; the top of the board shows what the project is
//   - API arrows turn green or red from reported requests; click a red one to ask the AI why
//   - attacks and weak spots show as shields on the endpoint or file they concern
//   - page blocks show the live page; the parse-degree bar turns code into English, line by line
//   - connector dots draw arrows by dragging; the block tray drags new blocks onto the board
// Backend: backend/whiteboard/ops/live.py, readable.py and views.py.
(() => {
  const wb = window.PlovaneWhiteboard
  if (!wb) return
  const $ = (s, r = document) => r.querySelector(s)
  const $$ = (s, r = document) => [...r.querySelectorAll(s)]
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  const q = v => wb.quoteArg(String(v))
  const lines = {}      // path -> {rows: {line: {english, source}}, counts, needs}
  const degree = {}     // path -> 'summary' | 1 | 2 | 3 | 'lines'
  const KINDS = [['service', '⬡', 'Service'], ['database', '▤', 'Database'], ['queue', '⇢', 'Queue'], ['external', '↗', 'External API'],
    ['folder', '▰', 'Folder'], ['file', '▧', 'File'], ['page', '▭', 'Page'], ['note', '✎', 'Note']]
  const ATTACK = { sql_injection: 'SQL injection', path_traversal: 'path traversal', command_injection: 'command injection',
    script_injection: 'script injection', scanner: 'scanner', brute_force: 'repeated failed logins' }

  /**
   * Input: name (string)
   * Output: number
   * Processing: A steady hue for an agent, the same one its access chips use.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function hue (name) { let h = 0; for (const c of String(name)) h = (h * 31 + c.charCodeAt(0)) % 360; return h }

  /**
   * Input: b (object)
   * Output: string
   * Processing: The project path a block stands for, if any.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function pathOf (b) { return ((b && (b.path || b.meta?.file)) || '').replace(/^\/+|\/+$/g, '') }

  /**
   * Input: path (string), folder (string)
   * Output: boolean
   * Processing: Whether a path is the folder or inside it ('' is the whole project).
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function inside (path, folder) { return !folder || path === folder || path.startsWith(folder + '/') }

  /**
   * Input: seconds (number)
   * Output: string
   * Processing: "just now", "12s ago" or "3m ago".
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function ago (seconds) { return seconds < 3 ? 'just now' : seconds < 60 ? `${Math.round(seconds)}s ago` : `${Math.round(seconds / 60)}m ago` }

  /**
   * Input: none
   * Output: object
   * Processing: The live state the server sent with the board, with every part present.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function live () { const l = wb.board?.live || {}; return { files: l.files || {}, orchestration: l.orchestration || {}, api: l.api || {}, threats: l.threats || {}, weak_spots: l.weak_spots || {} } }

  /**
   * Input: id (string)
   * Output: object[]
   * Processing: The endpoints a service block serves.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function endpointsOf (id) { return Object.values(wb.board?.board?.endpoints || {}).filter(e => e.service === id) }

  /**
   * Input: job (object), folder (string)
   * Output: string
   * Processing: The orchestration banner for a folder.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function bannerHTML (job, folder) {
    return `<div class="orch-banner" style="--hue:${hue(job.agent)}" title="${esc(job.agent)} is orchestrating ${esc(folder || 'the whole project')}${job.task ? ': ' + esc(job.task) : ''}"><span class="orch-dot"></span>🤖 <strong>${esc(job.agent)}</strong> · orchestrating ${esc(folder || 'the project')}${job.task ? ` — ${esc(job.task)}` : ''}<span class="orch-count">${job.done}/${job.total || '?'} files</span></div>`
  }

  /**
   * Input: el (Element), b (object), state (object)
   * Output: void
   * Processing: Working glow, orchestration banner, summary, API, attack and weak-spot badges for one card or tile.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function decorate (el, b, state) {
    const path = pathOf(b)
    const mark = path && state.files[path]
    const tile = el.classList.contains('nest-tile')
    const badges = []
    if (mark) {
      el.classList.add('is-working'); el.style.setProperty('--work-hue', hue(mark.agent))
      badges.push(`<span class="work-badge" style="--hue:${hue(mark.agent)}" title="${esc(mark.agent)} ${mark.task ? 'is ' + esc(mark.task) : 'changed this'} · ${ago(mark.age || 0)}">🤖 ${esc(mark.agent)}</span>`)
    } else if (b.kind === 'folder' || b.kind === 'group') {
      const busy = Object.entries(state.files).find(([p]) => path && inside(p, path))
      if (busy) { el.classList.add('has-working'); el.style.setProperty('--work-hue', hue(busy[1].agent)) }
    }
    const weak = path && state.weak_spots[path]
    if (weak) badges.push(`<span class="sec-badge sev-${weak.worst}" title="${weak.count} weak spot${weak.count > 1 ? 's' : ''} in this code; /security lists them">⚠ ${weak.count}</span>`)
    if (['service', 'server', 'external'].includes(b.kind)) {
      const eps = endpointsOf(b.id)
      const failing = eps.filter(e => state.api[e.id] && !state.api[e.id].ok)
      const passing = eps.filter(e => state.api[e.id] && state.api[e.id].ok)
      if (failing.length) badges.push(`<span class="api-badge fail" title="${esc(failing.map(e => `${e.method} ${e.path}: ${state.api[e.id].status}`).join('\n'))}">● ${failing.length} failing</span>`)
      else if (passing.length) badges.push(`<span class="api-badge pass" title="every reported request passed">● passing</span>`)
      const hits = eps.map(e => state.threats[e.id]).filter(Boolean)
      if (hits.length) {
        const worst = hits.some(h => h.worst === 'high') ? 'high' : hits.some(h => h.worst === 'medium') ? 'medium' : 'low'
        const kinds = [...new Set(hits.flatMap(h => h.kinds))].map(k => ATTACK[k] || k)
        badges.push(`<span class="sec-badge sev-${worst}" title="Attacks seen: ${esc(kinds.join(', '))}. /security lists them.">🛡 ${esc(kinds[0])}${kinds.length > 1 ? ` +${kinds.length - 1}` : ''}</span>`)
      }
    }
    if (badges.length) {
      const html = `<span class="live-badges">${badges.join('')}</span>`
      const name = !tile && el.querySelector('header .nest-name')
      if (name) name.insertAdjacentHTML('afterend', html)  // beside the name: the header's second line is cut off
      else el.insertAdjacentHTML('beforeend', html)
    }
    if (tile && b.meta?.summary && !el.querySelector('.tile-sum')) {
      el.insertAdjacentHTML('beforeend', `<span class="tile-sum" title="${esc(b.meta.summary)}">${esc(b.meta.summary)}</span>`)
    }
    const job = (b.kind === 'folder' || b.kind === 'group') && state.orchestration[path]
    if (job && path) el.insertAdjacentHTML('afterbegin', bannerHTML(job, path))
  }

  /**
   * Input: rect (object)
   * Output: object
   * Processing: The part of a card below its title (same rule as whiteboard.js innerOf).
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function innerOf (r) { const head = Math.min(r.w * 0.12, r.h * 0.3); return { x: r.x, y: r.y + head, w: r.w, h: r.h - head } }

  /**
   * Input: b (object), r (object)
   * Output: string
   * Processing: A live page in a box, or how to give it an address.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function pageHTML (b, r) {
    const style = `left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px`
    if (!b.meta?.url) return `<div class="page-frame empty" style="${style}"><span>${esc(b.meta?.route || b.name)}<br><small>Run /frontend start, then /board make frontend --refresh</small></span></div>`
    return `<div class="page-frame" style="${style}"><iframe src="${esc(b.meta.url)}" title="${esc(b.name)}" loading="lazy" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe><a class="page-open" href="${esc(b.meta.url)}" target="_blank" rel="noopener" title="Open in the browser">↗</a></div>`
  }

  /**
   * Input: state (object)
   * Output: void
   * Processing: Colour API arrows by the requests reported for their endpoint; red labels open the AI's explanation.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function colourArrows (state) {
    $$('#connections [data-endpoint]').forEach(el => {
      const ep = el.dataset.endpoint
      const info = ep && state.api[ep]
      if (!info) return
      el.classList.add(info.ok ? 'live-pass' : 'live-fail')
      if (el.tagName.toLowerCase() === 'text' && !el.dataset.live) {
        el.dataset.live = '1'
        el.textContent = `${el.textContent} ${info.ok ? '✓' : '✗'} ${info.status || ''}`.trim()
        if (!info.ok) {
          el.insertAdjacentHTML('afterbegin', `<title>${info.fails} of ${info.count} requests failed; click to ask the AI why</title>`)
          el.addEventListener('click', () => explain(ep))
        }
      }
    })
  }

  /**
   * Input: ep (string)
   * Output: Promise<void>
   * Processing: Ask the AI why an endpoint's last request failed, and show the answer.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  async function explain (ep) {
    wb.toast(`Asking why ${ep} failed…`)
    try {
      const data = await wb.request('command', { method: 'POST', body: JSON.stringify({ command: `/api explain ${q(ep)}` }) })
      wb.openModal(`Why ${ep} failed`, `<p>${esc(data.result?.explanation || 'No answer.')}</p>`, 'API FAILURE')
    } catch (e) { wb.toast(e.message, true) }
  }

  /**
   * Input: path (string)
   * Output: void
   * Processing: Put each line's English beside the code shown, when that file has been translated.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function englishBeside (path) {
    const t = lines[path]
    if (!t || degree[path] !== 'lines') return
    $$('#blocks .nest-code').forEach(box => {
      box.classList.add('with-english')
      $$(':scope > div', box).forEach(row => {
        const n = Number(row.querySelector('i')?.textContent)
        const entry = t.rows[n]
        if (!entry || row.querySelector('.en')) return
        const code = document.createElement('span'); code.className = 'code-text'  // keep the line's coloured pieces in one column
        ;[...row.childNodes].filter(n => !(n.nodeType === 1 && n.tagName === 'I')).forEach(n => code.append(n)); row.append(code)
        row.insertAdjacentHTML('beforeend', entry.english ? `<em class="en src-${esc(entry.source)}" title="${esc({ rule: 'worded by a rule: free', cache: 'saved from an earlier AI answer: free', ai: 'worded by the AI', rough: 'rough rule wording; ✦ asks the AI', unknown: 'no rule; ✦ asks the AI', continues: '' }[entry.source] || '')}">${esc(entry.english)}</em>` : '<em class="en"></em>')
      })
    })
  }

  /**
   * Input: b (object)
   * Output: void
   * Processing: The parse-degree bar on a file: one sentence, functions, steps, detailed steps, or line by line.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function degreeBar (b) {
    const bar = $('#focus-bar')
    if (!bar || bar.querySelector('.degree')) return
    const path = pathOf(b)
    if (!path || !(b.kind === 'file' || b.meta?.file)) return
    const now = degree[path]
    const opt = (value, label, tip) => `<button class="${String(now) === String(value) ? 'on' : ''}" data-live="degree" data-degree="${value}" data-path="${esc(path)}" title="${tip}">${label}</button>`
    const t = lines[path]
    const ai = now === 'lines' && t?.needs ? `<button class="degree-ai" data-live="lines-ai" data-path="${esc(path)}" title="${t.needs} lines have no rule; word them with one AI call (cached afterwards)">✦ ${t.needs}</button>` : ''
    bar.insertAdjacentHTML('beforeend', `<span class="degree" title="Parse degree: how much English to show">${opt('summary', 'Summary', 'One sentence for the whole file (free)')}${opt(1, 'Functions', 'One block per function')}${opt(2, 'Steps', 'One English step per statement')}${opt(3, 'Detailed', 'Steps inside steps')}${opt('lines', 'Line by line', 'English beside every line: rules first, AI only for the rest')}${ai}</span>`)
  }

  /**
   * Input: path (string), value (string)
   * Output: Promise<void>
   * Processing: Change a file's parse degree and show the English for it.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  async function setDegree (path, value, provider) {
    degree[path] = value === 'summary' || value === 'lines' ? value : Number(value)
    try {
      if (value === 'summary') {
        const data = await wb.request('command', { method: 'POST', body: JSON.stringify({ command: `/summary ${q(path)}` }) })
        const line = data.result?.files?.[path]
        wb.openModal(path.split('/').pop(), `<p class="summary-line">${esc(line || 'No summary yet.')}</p>${data.result?.about ? `<p class="muted">Project: ${esc(data.result.about)}</p>` : ''}`, 'SUMMARY')
        await wb.refresh()
      } else if (value === 'lines') {
        const data = await wb.request('command', { method: 'POST', body: JSON.stringify({ command: `/translate ${q(path)}${provider ? ` --provider ${provider}` : ''}` }) })
        const r = data.result || {}
        const rows = {}
        for (const row of r.lines || []) rows[row.line] = { english: row.english, source: row.source }
        lines[path] = { rows, counts: r.counts || {}, needs: r.needs_ai || 0 }
        const free = Math.round((r.free_share ?? 1) * 100)
        wb.toast(`${free}% of lines worded free${r.needs_ai ? `; ${r.needs_ai} need the AI (✦)` : ''}${r.usage && Object.keys(r.usage).length ? ' · AI used' : ''}`)
        wb.render()
      } else {
        await wb.runCommand(`/blocks view ${q(path)} --detail ${value} --discard`)
      }
    } catch (e) { wb.toast(e.message, true) }
  }

  /**
   * Input: root (Element)
   * Output: void
   * Processing: Connector dots on each card's edges; dragging one to another block draws an arrow.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function addDots (root) {
    $$('.nest-card', root).forEach(card => {
      if (card.querySelector('.conn-dot')) return
      card.insertAdjacentHTML('beforeend', ['top', 'right', 'bottom', 'left'].map(side => `<span class="conn-dot ${side}" data-side="${side}" title="Drag to another block to connect"></span>`).join(''))
    })
  }

  /**
   * Input: e (PointerEvent)
   * Output: object
   * Processing: A screen point in the whiteboard's own coordinates.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function worldPoint (e) {
    const box = $('#canvas-viewport').getBoundingClientRect(); const v = wb.view
    return { x: (e.clientX - box.left - v.x) / v.scale, y: (e.clientY - box.top - v.y) / v.scale }
  }

  /**
   * Input: x (number), y (number)
   * Output: string | null
   * Processing: The block (card or tile) under a screen point.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function blockAt (x, y) {
    const hit = document.elementsFromPoint(x, y).find(el => el.closest?.('[data-block],[data-tile]'))
    const el = hit && hit.closest('[data-block],[data-tile]')
    return el ? (el.dataset.block || el.dataset.tile) : null
  }

  let drag = null
  document.addEventListener('pointerdown', e => {
    const dot = e.target.closest?.('.conn-dot')
    if (!dot) return
    e.preventDefault(); e.stopPropagation()
    const card = dot.closest('.nest-card'); const start = worldPoint(e)
    drag = { from: card.dataset.block, start }
    $('#connections').insertAdjacentHTML('beforeend', `<line id="drag-line" class="drag-line" x1="${start.x}" y1="${start.y}" x2="${start.x}" y2="${start.y}"/>`)
  }, true)
  document.addEventListener('pointermove', e => {
    if (!drag) return
    const p = worldPoint(e); const line = $('#drag-line')
    if (line) { line.setAttribute('x2', p.x); line.setAttribute('y2', p.y) }
    $$('.connect-target').forEach(el => el.classList.remove('connect-target'))
    const id = blockAt(e.clientX, e.clientY)
    if (id && id !== drag.from) document.querySelector(`[data-block="${CSS.escape(id)}"],[data-tile="${CSS.escape(id)}"]`)?.classList.add('connect-target')
  })
  let dragEnded = 0
  document.addEventListener('pointerup', e => {
    if (!drag) return
    const from = drag.from; drag = null; dragEnded = Date.now()
    $('#drag-line')?.remove(); $$('.connect-target').forEach(el => el.classList.remove('connect-target'))
    const to = blockAt(e.clientX, e.clientY)
    if (to && to !== from) chooseConnection(from, to, e.clientX, e.clientY)
  })

  /**
   * Input: from (string), to (string), x (number), y (number)
   * Output: void
   * Processing: Ask whether a dragged arrow is only drawn, or is a real call in the code.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function chooseConnection (from, to, x, y) {
    $('#connect-pick')?.remove()
    const all = wb.blocks(); const a = all[from]?.name || from; const b = all[to]?.name || to
    document.body.insertAdjacentHTML('beforeend', `<div id="connect-pick" class="connect-pick" style="left:${x}px;top:${y}px"><b>${esc(a)} → ${esc(b)}</b><button data-live="arrow-draw">Just draw an arrow</button><button data-live="arrow-api">API call in the code</button><button data-live="arrow-import">Import in the code</button><button data-live="arrow-cancel" class="muted">Cancel</button></div>`)
    $('#connect-pick').dataset.from = from; $('#connect-pick').dataset.to = to
  }

  /**
   * Input: none
   * Output: void
   * Processing: The block tray: kinds to drag onto the board.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function addTray () {
    const view = $('#canvas-viewport')
    if (!view || $('#block-tray')) return
    view.insertAdjacentHTML('beforeend', `<div id="block-tray" class="block-tray"><button class="tray-toggle" data-live="tray" title="Drag new blocks onto the board">＋ Blocks</button><div class="tray-items">${KINDS.map(([kind, icon, label]) => `<div class="tray-item" draggable="true" data-kind="${kind}" title="Drag onto the board or into a block">${icon} ${label}</div>`).join('')}<button class="tray-views" data-live="views" title="Whiteboards built for one job">▦ Make a whiteboard…</button></div></div>`)
    view.addEventListener('dragover', e => { if (e.dataTransfer?.types.includes('text/plovane-kind')) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy' } })
    view.addEventListener('drop', e => {
      const kind = e.dataTransfer?.getData('text/plovane-kind')
      if (!kind) return
      e.preventDefault()
      const into = blockAt(e.clientX, e.clientY) || wb.focus
      const label = KINDS.find(k => k[0] === kind)?.[2] || kind
      const name = prompt(`Name for the new ${label.toLowerCase()}${into ? ` inside ${wb.blocks()[into]?.name || into}` : ''}`, kind === 'file' ? 'new_module.py' : `new-${kind}`)
      if (!name || !name.trim()) return
      wb.runCommand(`/block add ${kind} ${q(name.trim())}${into ? ` --inside ${q(into)}` : ''}`)
    })
  }
  document.addEventListener('dragstart', e => {
    const item = e.target.closest?.('.tray-item')
    if (item) { e.dataTransfer.setData('text/plovane-kind', item.dataset.kind); e.dataTransfer.effectAllowed = 'copy' }
  })

  /**
   * Input: none
   * Output: void
   * Processing: Offer the whiteboards built for one job.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function showViews () {
    const kinds = [['structure', 'Structure and work', 'Services, folders and files; what each agent is working on right now.'],
      ['api', 'API requests', 'Endpoints and the calls between them; arrows go green or red with each request.'],
      ['security', 'Security', 'Attacks seen in requests and weak spots in the code.'],
      ['frontend', 'Frontend pages', 'Every page of the app, live, with the links between them.']]
    wb.openModal('Make a whiteboard', `<p class="muted">Each one is its own .plo tab. The AI makes these too when its task needs one.</p><div class="board-picks">${kinds.map(([k, t, d]) => `<button class="board-pick" data-live="make-view" data-kind="${k}"><b>${t}</b><small>${d}</small></button>`).join('')}</div>`, 'WHITEBOARDS')
  }

  document.addEventListener('click', async e => {
    const el = e.target.closest?.('[data-live]')
    if (!el) { if (Date.now() - dragEnded > 300 && !e.target.closest?.('#connect-pick')) $('#connect-pick')?.remove(); return }  // the click that ends a drag keeps the chooser
    const act = el.dataset.live; const pick = $('#connect-pick')
    e.preventDefault(); e.stopPropagation()
    if (act === 'degree') setDegree(el.dataset.path, el.dataset.degree)
    else if (act === 'lines-ai') setDegree(el.dataset.path, 'lines', localStorage.getItem('plovane-provider') || 'anthropic')
    else if (act === 'tray') $('#block-tray').classList.toggle('open')
    else if (act === 'views') showViews()
    else if (act === 'make-view') {
      $('#modal')?.close()
      try {
        const data = await wb.request('command', { method: 'POST', body: JSON.stringify({ command: `/board make ${el.dataset.kind} --refresh`, confirm: true }) })
        wb.toast(`Made ${data.result?.board}. Open it from the arrow beside the whiteboard tab.`)
      } catch (err) { wb.toast(err.message, true) }
    } else if (act.startsWith('arrow-') && pick) {
      const { from, to } = pick.dataset; pick.remove()
      if (act === 'arrow-draw') wb.runCommand(`/arrow put ${q(from)} -> ${q(to)} --type calls`)
      else if (act === 'arrow-api') wb.runCommand(`/connect ${q(from)} -> ${q(to)} --type api`)
      else if (act === 'arrow-import') wb.runCommand(`/connect ${q(from)} -> ${q(to)} --type import`)
    }
  }, true)

  /**
   * Input: none
   * Output: void
   * Processing: Decorate what whiteboard.js just drew with the live state and readable code.
   * Changed by: Claude Code
   * Date: 2026-11-18
   */
  function afterRender () {
    const data = wb.board
    if (!data?.board) return
    const state = live(); const all = wb.blocks(); const focusId = wb.focus; const focus = focusId ? all[focusId] : null
    $$('#blocks [data-block], #blocks [data-tile]').forEach(el => { const b = all[el.dataset.block || el.dataset.tile]; if (b) decorate(el, b, state) })
    const frame = $('#blocks .nest-frame'); const head = frame?.querySelector('header')
    if (head && !focus && data.about) { const small = head.querySelector('small'); if (small) { small.textContent = data.about; small.title = 'What this project is (/project describe)' } }
    const job = state.orchestration[focus ? pathOf(focus) : '']
    if (frame && job && (!focus || ['folder', 'group'].includes(focus.kind))) frame.insertAdjacentHTML('afterbegin', bannerHTML(job, focus ? pathOf(focus) : ''))
    if (focus && pathOf(focus) && state.files[pathOf(focus)]) { frame?.classList.add('is-working'); frame?.style.setProperty('--work-hue', hue(state.files[pathOf(focus)].agent)) }
    const items = wb.items
    $$('#blocks .nest-card.page-card').forEach(card => {
      const b = all[card.dataset.block]; const it = items.get(card.dataset.block)
      if (b && it) card.insertAdjacentHTML('afterend', pageHTML(b, innerOf(it.rect)))  // covers the card's placeholder text
    })
    if (focus?.kind === 'page' && frame) {
      $$('#blocks .nest-empty.big').forEach(n => n.remove())
      const w = parseFloat(frame.style.width); const h = parseFloat(frame.style.height)
      frame.insertAdjacentHTML('afterend', pageHTML(focus, innerOf({ x: 0, y: 0, w, h })))
    }
    colourArrows(state)
    if (focus) { degreeBar(focus); englishBeside(pathOf(focus)) }
    addDots($('#blocks')); addTray()
    clearTimeout(afterRender.timer)
    if (Object.keys(state.files).length) afterRender.timer = setTimeout(() => wb.refresh(), 15000)  // let quiet marks fade
  }

  // Live events: agents working, requests, attacks. Refresh soon after, a few at a time.
  let pending = null
  if (window.EventSource) {
    const token = document.querySelector('meta[name="plovane-token"]')?.content || window.PLOVANE_TOKEN || ''
    const origin = window.PLOVANE_API_ORIGIN || (location.pathname.startsWith('/whiteboard') ? location.origin : 'http://127.0.0.1:8700')
    const source = new EventSource(`${origin}/api/v1/whiteboard/events${token ? `?token=${encodeURIComponent(token)}` : ''}`)
    source.addEventListener('live', ev => {
      const e = JSON.parse(ev.data)
      if (e.what === 'orchestration') wb.toast(`${e.agent} is orchestrating ${e.folder || 'the project'}${e.task ? ': ' + e.task : ''}`)
      if (e.what === 'traffic' && e.threats) wb.toast(`⚠ ${e.threats} suspicious request${e.threats > 1 ? 's' : ''} seen`, true)
      clearTimeout(pending); pending = setTimeout(() => wb.refresh(), 250)
    })
  }

  window.PlovaneLive = { afterRender, setDegree, explain }
  if (wb.board) wb.render()
})()
