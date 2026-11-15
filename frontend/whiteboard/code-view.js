/*
Input: Current whiteboard view and selected file
Output: Plain-English file blocks and code panel
Processing: Draws file details at the right zoom level and opens the code view
Changed by: Aarav
Date: 2026-11-14
*/

// Code view (issue #64): zoom into a file card and it fades into plain-English blocks; zoom
// past the blocks and they fade into the file's real code. Also the read-only code panel the
// Files tab opens. whiteboard.js calls focus() and draw() on every render.
(() => {
  const wb = window.PlovaneWhiteboard
  if (!wb) return
  const $ = s => document.querySelector(s)
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))

  const WIDTH = 760               // natural width of a file's detail, in CSS pixels
  const STEPS_IN = [0.45, 0.7]    // file width ÷ view width over which the card fades into blocks
  const CODE_IN = [1.1, 1.4]      // ...and over which the blocks fade into code
  const smooth = (v, [a, b]) => { const t = Math.min(1, Math.max(0, (v - a) / (b - a))); return t * t * (3 - 2 * t) }

  // -- Board data ---------------------------------------------------------------------------
  const board = () => { const d = wb.board || {}; return d.board || d }
  const blocks = () => Object.values(board().blocks || {})

  // Plain-English steps for a file, grouped by function in file order.
  function functionsOf(path) {
    const groups = new Map()
    blocks().filter(b => b.kind === 'step' && b.meta?.file === path)
      .sort((a, b) => (a.meta.lines?.[0] ?? 0) - (b.meta.lines?.[0] ?? 0))
      .forEach(b => {
        const name = b.meta.function || b.symbol || 'code'
        if (!groups.has(name)) groups.set(name, [])
        groups.get(name).push(b)
      })
    return [...groups].map(([name, steps]) => ({
      name, steps,
      first: Math.min(...steps.map(s => s.meta.lines?.[0] ?? 0)),
      last: Math.max(...steps.map(s => s.meta.lines?.[1] ?? 0)),
    }))
  }

  const LANGUAGES = { py: 'python', js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript', ts: 'typescript', tsx: 'typescript', c: 'cpp', cc: 'cpp', cpp: 'cpp', h: 'cpp', hpp: 'cpp', ino: 'cpp', cu: 'cpp' }
  const LABELS = { python: 'Python', javascript: 'JavaScript', typescript: 'TypeScript', cpp: 'C / C++', text: 'Text' }
  const languageOf = path => LANGUAGES[(path.split('.').pop() || '').toLowerCase()] || 'text'

  // File text, fetched once per focus and kept briefly so zooming doesn't refetch.
  const texts = new Map()
  function textOf(path) {
    let entry = texts.get(path)
    if (!entry || (!entry.loading && Date.now() - entry.at > 5000)) {
      entry = entry || { text: null, error: null, at: 0 }
      entry.loading = true
      texts.set(path, entry)
      wb.request('op', { method: 'POST', body: JSON.stringify({ op: 'code.open', args: { target: path } }) })
        .then(data => { const r = data.result || data; entry.text = r.content ?? ''; entry.version = r.version; entry.error = null })
        .catch(err => { entry.error = err.message })
        .finally(() => { entry.loading = false; entry.at = Date.now(); wb.render(); if (panel.path === path && !panel.editing) showPanel() })
    }
    return entry
  }

  // -- Syntax colours -----------------------------------------------------------------------
  const words = s => new Set(s.split(' '))
  const JS = 'async await break case catch class const continue default delete do else export extends false finally for function if import in instanceof let new null of return super switch this throw true try typeof undefined var void while yield'
  const KEYWORDS = {
    python: words('and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield self'),
    javascript: words(JS),
    typescript: words(JS + ' interface type enum implements private public protected readonly declare namespace'),
    cpp: words('auto bool break case char class const continue default define delete do double else endif enum extern false float for if ifdef ifndef include inline int long namespace new nullptr private protected public return short signed sizeof static struct switch template this true typedef unsigned using virtual void volatile while byte uint8_t uint16_t uint32_t int8_t int16_t int32_t size_t String'),
    text: new Set(),
  }
  const tok = (cls, text) => `<span class="tk-${cls}">${esc(text)}</span>`

  function highlight(line, language, state) {
    const keywords = KEYWORDS[language], hashComment = language === 'python', cLike = language !== 'python' && language !== 'text'
    let out = '', i = 0
    while (i < line.length) {
      if (state.block) {
        const end = line.indexOf('*/', i), stop = end < 0 ? line.length : end + 2
        out += tok('cm', line.slice(i, stop)); i = stop
        if (end >= 0) state.block = false
        continue
      }
      const rest = line.slice(i)
      let m
      if (cLike && rest.startsWith('/*')) { state.block = true; continue }
      if ((hashComment && rest[0] === '#') || (cLike && rest.startsWith('//'))) { out += tok('cm', rest); break }
      if (language === 'cpp' && rest[0] === '#' && !line.slice(0, i).trim() && (m = rest.match(/^#\s*\w+/))) { out += tok('kw', m[0]); i += m[0].length; continue }
      if ((m = rest.match(/^("(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\])*'?|`(?:\\.|[^`\\])*`?)/))) { out += tok('str', m[0]); i += m[0].length; continue }
      if ((m = rest.match(/^\d[\w.]*/))) { out += tok('num', m[0]); i += m[0].length; continue }
      if ((m = rest.match(/^[A-Za-z_]\w*/))) {
        const w = m[0]
        out += keywords.has(w) ? tok('kw', w) : /^\s*\(/.test(rest.slice(w.length)) ? tok('fn', w) : esc(w)
        i += w.length; continue
      }
      out += esc(rest[0]); i++
    }
    return out
  }

  // -- Pieces shared by the canvas and the panel ---------------------------------------------
  function stepsHTML(path) {
    const fns = functionsOf(path)
    if (!fns.length) {
      return `<div class="fd-empty"><div class="fd-empty-icon">▧</div><b>Not turned into blocks yet</b>
        <p>Blocks explain each step of this file in plain English, for anyone who doesn't read code.</p>
        <button class="fd-btn primary" data-fd="blocks" data-path="${esc(path)}">Turn into blocks</button>
        <code>/blocks ${esc(path)}</code></div>`
    }
    return fns.map(fn => {
      let n = 0
      return `<article class="fd-fn"><header><span class="fd-fn-icon">ƒ</span><b>${esc(fn.name)}</b>
        <span class="fd-muted">lines ${fn.first}–${fn.last}</span></header><ol class="fd-list">${fn.steps.map(s => {
          const depth = Math.min(4, s.meta.depth || 0), number = depth ? '' : ++n
          return `<li class="fd-step" style="--depth:${depth}"><span class="fd-num">${number}</span><span class="fd-text">${esc(s.meta.english || s.name)}</span></li>`
        }).join('')}</ol></article>`
    }).join('')
  }

  function codeHTML(path) {
    const entry = textOf(path)
    if (entry.text == null) return `<div class="fd-loading">${entry.error ? esc(entry.error) : 'Loading code…'}</div>`
    const language = languageOf(path), state = { block: false }
    // Mark each top-level step where it starts, and shade the lines it covers.
    const starts = new Map(), owner = new Map()
    functionsOf(path).forEach(fn => {
      let n = 0
      fn.steps.forEach(s => {
        const [a, b] = s.meta.lines || [0, 0]
        if (s.meta.depth) return
        n++
        starts.set(a, { n, text: s.meta.english || s.name })
        for (let line = a; line <= b; line++) owner.set(line, n % 2 ? 'odd' : 'even')
      })
    })
    const lines = entry.text.replace(/\n$/, '').split('\n')
    return `<div class="fd-code-grid">${lines.map((text, index) => {
      const number = index + 1, note = starts.get(number), band = owner.get(number)
      return `<div class="fd-line${band ? ' in-step ' + band : ''}"><span class="fd-ln">${number}</span><code>${highlight(text, language, state) || ' '}</code>${note ? `<span class="fd-note"><i>${note.n}</i>${esc(note.text)}</span>` : '<span></span>'}</div>`
    }).join('')}</div>`
  }

  function headHTML(block, path) {
    const steps = functionsOf(path).reduce((n, fn) => n + fn.steps.length, 0)
    return `<div class="fd-head"><span class="fd-file-icon">▧</span><div class="fd-title"><b>${esc(block?.name || path.split('/').pop())}</b>
      <span class="fd-muted">${esc(path)}</span></div><span class="fd-chip">${LABELS[languageOf(path)]}</span>
      ${steps ? `<span class="fd-chip soft">${steps} steps</span>` : ''}
      <button class="fd-btn" data-fd="panel" data-path="${esc(path)}">Open code panel</button></div>`
  }

  // -- Zooming into a file -------------------------------------------------------------------
  // The file nearest the middle of the view is the one you are zooming into.
  function focus(files, view) {
    const cx = view.vw / 2, cy = view.vh / 2
    let best = null
    for (const { b, p } of files) {
      const w = (p.w || b.w || 160) * view.scale, h = (p.h || b.h || 60) * view.scale
      const x = (p.x || 0) * view.scale + view.x, y = (p.y || 0) * view.scale + view.y
      if (x > view.vw || y > view.vh || x + w < 0 || y + h < 0) continue
      const inside = cx >= x && cx <= x + w && cy >= y && cy <= y + h
      const distance = inside ? 0 : Math.hypot(Math.max(x - cx, 0, cx - x - w), Math.max(y - cy, 0, cy - y - h))
      if (!best || distance < best.distance) best = { block: b, p, distance, fill: w / view.vw }
    }
    if (!best) return null
    best.steps = smooth(best.fill, STEPS_IN)
    best.code = smooth(best.fill, CODE_IN)
    return best.steps > 0.001 ? best : null
  }

  // Deep enough to read the code of the narrowest file comfortably.
  function maxScale(vw) {
    const widths = blocks().filter(b => b.kind === 'file' && b.path).map(b => b.w || 160)
    return widths.length ? (CODE_IN[1] * 1.6 * vw) / Math.min(...widths) : 0
  }

  let shown = { key: '', el: null }
  function draw(f) {
    let el = $('#file-detail')
    if (!f) { if (el) el.hidden = true; return }
    if (!el) {
      el = document.createElement('div')
      el.id = 'file-detail'; el.className = 'file-detail'
      $('#canvas-world').append(el)
    }
    const path = f.block.path, entry = texts.get(path)
    const key = [path, functionsOf(path).map(fn => fn.steps.map(s => s.id + (s.meta.english || '')).join()).join('|'),
      f.code > 0.001 ? (entry?.text?.length ?? -1) + (entry?.error || '') : 'no-code'].join('#')
    if (shown.key !== key || shown.el !== el) {
      el.innerHTML = `${headHTML(f.block, path)}<div class="fd-stack"><section class="fd-steps">${stepsHTML(path)}</section>
        <section class="fd-code">${f.code > 0.001 ? codeHTML(path) : ''}</section></div>`
      shown = { key, el }
    }
    const scale = (f.p.w || f.block.w || 160) / WIDTH
    el.hidden = false
    el.style.cssText = `left:${f.p.x || 0}px;top:${f.p.y || 0}px;width:${WIDTH}px;transform:scale(${scale});opacity:${f.steps.toFixed(3)}`
    el.style.pointerEvents = f.steps > 0.5 ? 'auto' : 'none'
    const steps = el.querySelector('.fd-steps'), code = el.querySelector('.fd-code')
    steps.style.opacity = (1 - f.code).toFixed(3)
    steps.style.pointerEvents = f.code < 0.5 ? 'auto' : 'none'
    code.style.opacity = f.code.toFixed(3)
    code.style.pointerEvents = f.code >= 0.5 ? 'auto' : 'none'
  }

  // -- Code panel (#64 view, #65 edit) -----------------------------------------------------
  const panel = { path: null, tab: 'code', el: null, editing: false, draft: null, base: null, notice: '' }
  const dirty = () => panel.editing && panel.draft != null && panel.draft !== texts.get(panel.path)?.text

  // Ask before throwing away unsaved edits.
  const canLeave = () => !dirty() || window.confirm(`Discard your unsaved changes to ${panel.path}?`)

  function open(path, tab) {
    if (window.PlovaneWorkbench) return window.PlovaneWorkbench.open(path) // files open in the editor tabs
    if (panel.path !== path && !canLeave()) return
    if (panel.path !== path) Object.assign(panel, { editing: false, draft: null, notice: '' })
    panel.path = path
    if (tab) panel.tab = tab
    if (!panel.el) {
      panel.el = document.createElement('aside')
      panel.el.className = 'code-panel'
      panel.el.setAttribute('aria-label', 'Code panel')
      $('.canvas-area').append(panel.el)
      panel.el.addEventListener('click', onClick)
      panel.el.addEventListener('wheel', e => e.stopPropagation())
    }
    if (!panel.editing) texts.delete(path) // show the file as it is now
    showPanel()
  }

  function showPanel() {
    if (!panel.el || !panel.path) return
    const path = panel.path, block = blocks().find(b => b.kind === 'file' && b.path === path), has = functionsOf(path).length
    const entry = texts.get(path), loaded = entry?.text != null
    const scroll = panel.el.querySelector('.cp-body')?.scrollTop || 0
    const status = panel.editing ? (dirty() ? '<span class="cp-dirty" title="Unsaved changes">●</span> Unsaved changes' : 'Editing') : LABELS[languageOf(path)]
    const tools = panel.editing
      ? `<button class="fd-btn" data-fd="revert">Revert</button><button class="fd-btn primary" data-fd="save" ${dirty() ? '' : 'disabled'}>Save <kbd>⌘S</kbd></button>`
      : `${loaded && !entry.error ? '<button class="fd-btn" data-fd="edit">Edit</button>' : ''}
         <button class="fd-btn" data-fd="blocks" data-path="${esc(path)}">${has ? 'Refresh blocks' : 'Turn into blocks'}</button>
         ${block ? `<button class="fd-btn" data-fd="show" data-path="${esc(path)}">Show on board</button>` : ''}`
    panel.el.hidden = false
    panel.el.classList.toggle('editing', panel.editing)
    panel.el.innerHTML = `<header class="cp-head"><span class="fd-file-icon">▧</span><div class="fd-title"><b>${esc(block?.name || path.split('/').pop())}</b>
        <span class="fd-muted">${esc(path)} · ${status}</span></div><button class="cp-close" data-fd="close" aria-label="Close code panel">×</button></header>
      <nav class="cp-tools">${panel.editing ? '<span class="cp-hint">Saving updates the file on disk. Undo reverts it.</span>' : `<div class="cp-tabs" role="tablist">
        <button role="tab" class="${panel.tab === 'code' ? 'active' : ''}" data-fd="tab" data-tab="code">Code</button>
        <button role="tab" class="${panel.tab === 'steps' ? 'active' : ''}" data-fd="tab" data-tab="steps">Plain English</button></div>`}
        <span class="cp-spacer"></span>${tools}</nav>
      ${panel.notice ? `<div class="cp-notice" role="alert"><span>${esc(panel.notice)}</span><button class="fd-btn" data-fd="reload">Reload from disk</button></div>` : ''}
      <div class="cp-body ${panel.editing ? 'edit' : panel.tab}">${panel.editing ? editorHTML() : panel.tab === 'code' ? codeHTML(path) : stepsHTML(path)}</div>`
    panel.el.querySelector('.cp-body').scrollTop = scroll
    if (panel.editing) bindEditor()
  }

  // -- Editor: a transparent textarea over the coloured code, both sized to the text ----------
  function editorHTML() {
    return `<div class="cp-editor"><div class="cp-gutter" aria-hidden="true"></div><div class="cp-edit-wrap">
      <pre class="cp-hl" aria-hidden="true"></pre><textarea class="cp-input" spellcheck="false" autocapitalize="off"
      autocomplete="off" autocorrect="off" wrap="off" aria-label="Edit ${esc(panel.path)}"></textarea></div></div>`
  }
  function paint(input) {
    const language = languageOf(panel.path), state = { block: false }, lines = input.value.split('\n')
    panel.el.querySelector('.cp-hl').innerHTML = lines.map(line => highlight(line, language, state)).join('\n') + '\n'
    panel.el.querySelector('.cp-gutter').innerHTML = lines.map((_, i) => `<span>${i + 1}</span>`).join('')
    input.style.height = '0px'
    input.style.height = input.scrollHeight + 'px'
    input.style.width = Math.max(input.parentElement.clientWidth, input.scrollWidth) + 'px'
  }
  function bindEditor() {
    const input = panel.el.querySelector('.cp-input')
    input.value = panel.draft
    paint(input)
    input.focus({ preventScroll: true })
    input.addEventListener('input', () => {
      const was = dirty()
      panel.draft = input.value
      paint(input)
      if (was !== dirty()) refreshStatus()
    })
    input.addEventListener('keydown', e => {
      if (e.key === 'Tab' && !e.metaKey && !e.ctrlKey) {
        e.preventDefault()
        document.execCommand('insertText', false, languageOf(panel.path) === 'python' ? '    ' : '  ')
      }
    })
  }
  // Update the header and buttons without rebuilding the editor (which would lose the cursor).
  function refreshStatus() {
    const title = panel.el.querySelector('.fd-title .fd-muted'), save = panel.el.querySelector('[data-fd=save]')
    if (title) title.innerHTML = `${esc(panel.path)} · ${dirty() ? '<span class="cp-dirty" title="Unsaved changes">●</span> Unsaved changes' : 'Editing'}`
    if (save) save.disabled = !dirty()
  }

  function startEdit() {
    const entry = texts.get(panel.path)
    if (entry?.text == null) return
    Object.assign(panel, { editing: true, draft: entry.text, base: entry.version, notice: '', tab: 'code' })
    showPanel()
  }
  async function save() {
    if (!dirty()) return
    const path = panel.path, content = panel.draft, button = panel.el.querySelector('[data-fd=save]')
    if (button) button.disabled = true
    try {
      const data = await wb.request('op', { method: 'POST', body: JSON.stringify({ op: 'file.save', args: { path, content, base: panel.base } }) })
      const result = data.result || data
      texts.set(path, { text: content, version: result.version, error: null, at: Date.now() })
      Object.assign(panel, { editing: false, draft: null, notice: '' })
      wb.toast(result.blocks_refreshed ? `Saved ${path} and updated its blocks` : `Saved ${path}`)
      await wb.refresh()
      showPanel()
    } catch (err) {
      panel.notice = /changed on disk/.test(err.message) ? 'This file changed on disk since you opened it, so it was not saved. Copy your edits, then reload.' : err.message
      showPanel()
    }
  }
  function reload() {
    if (!window.confirm('Reload the file from disk? Your unsaved edits will be lost.')) return
    texts.delete(panel.path)
    Object.assign(panel, { editing: false, draft: null, notice: '' })
    showPanel()
  }

  function close() {
    if (!canLeave()) return
    if (panel.el) panel.el.hidden = true
    Object.assign(panel, { path: null, editing: false, draft: null, notice: '' })
  }

  // Frame a file so its blocks fill most of the view.
  function show(path) {
    // The nested-zoom whiteboard flies down to the file itself, through its folders.
    const target = blocks().find(b => b.kind === 'file' && b.path === path)
    if (wb.flyTo) { if (target) wb.flyTo(target.id); return }
    const node = document.querySelector(`#blocks [data-block="${CSS.escape(blocks().find(b => b.kind === 'file' && b.path === path)?.id || '')}"]`)
    const vp = $('#canvas-viewport')
    if (!vp) return
    if (node) { // the file is on screen (maybe inside a folder): frame what is drawn
      const r = node.getBoundingClientRect(), v = vp.getBoundingClientRect(), { scale, x, y } = wb.view
      const s = scale * (0.85 * v.width) / r.width
      wb.setView(v.width / 2 - (r.left + r.width / 2 - v.left - x) * s / scale, v.height * 0.12 - (r.top - v.top - y) * s / scale, s)
      return
    }
    const block = blocks().find(b => b.kind === 'file' && b.path === path)
    if (!block) return
    const p = wb.position(block), w = p.w || block.w || 160, scale = (0.85 * vp.clientWidth) / w
    wb.setView(vp.clientWidth / 2 - ((p.x || 0) + w / 2) * scale, vp.clientHeight * 0.12 - (p.y || 0) * scale, scale)
  }

  async function onClick(e) {
    const button = e.target.closest('[data-fd]')
    if (!button) return
    e.stopPropagation()
    const action = button.dataset.fd, path = button.dataset.path
    if (action === 'close') close()
    else if (action === 'tab') { panel.tab = button.dataset.tab; showPanel() }
    else if (action === 'panel') open(path)
    else if (action === 'show') show(path)
    else if (action === 'edit') startEdit()
    else if (action === 'save') save()
    else if (action === 'revert') { if (canLeave()) { Object.assign(panel, { editing: false, draft: null, notice: '' }); showPanel() } }
    else if (action === 'reload') reload()
    else if (action === 'blocks') {
      button.disabled = true
      try {
        await wb.runCommand(`/blocks ${path}`)
        if (panel.path === path) { panel.tab = 'steps'; showPanel() }
      } finally { button.disabled = false }
    }
  }
  document.addEventListener('click', e => { if (e.target.closest('#file-detail')) onClick(e) })
  document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's' && panel.editing) { e.preventDefault(); save() }
    else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'p' && !e.shiftKey) { e.preventDefault(); quickOpen() }
    else if (e.key === 'Escape' && quick.el && !quick.el.hidden) closeQuick()
    else if (e.key === 'Escape' && panel.path && !panel.editing && !document.querySelector('dialog[open]')) close()
  })
  window.addEventListener('beforeunload', e => { if (dirty()) { e.preventDefault(); e.returnValue = '' } })

  // -- Quick open (#66) ------------------------------------------------------------------------
  const quick = { el: null, files: [], index: 0, shown: [] }
  // Score a path against typed text: every character must appear in order; tighter, later matches win.
  function score(path, text) {
    if (!text) return 1
    const lower = path.toLowerCase(), name = lower.slice(lower.lastIndexOf('/') + 1)
    let at = -1, gaps = 0
    for (const ch of text.toLowerCase()) { const next = lower.indexOf(ch, at + 1); if (next < 0) return 0; gaps += next - at - 1; at = next }
    return 1000 - gaps + (name.includes(text.toLowerCase()) ? 500 : 0) - path.length / 10
  }
  async function quickOpen() {
    if (!quick.el) {
      quick.el = document.createElement('div')
      quick.el.className = 'quick-open'
      quick.el.innerHTML = `<div class="qo-box" role="dialog" aria-label="Open file"><input class="qo-input" placeholder="Open a file… type part of its name" spellcheck="false" aria-label="File name">
        <div class="qo-list" role="listbox"></div><div class="qo-foot"><span>↑↓ to choose</span><span>↵ to open</span><span>esc to close</span></div></div>`
      document.body.append(quick.el)
      const input = quick.el.querySelector('.qo-input')
      input.addEventListener('input', () => { quick.index = 0; listQuick() })
      input.addEventListener('keydown', e => {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); quick.index = (quick.index + (e.key === 'ArrowDown' ? 1 : -1) + quick.shown.length) % Math.max(1, quick.shown.length); listQuick() }
        else if (e.key === 'Enter' && quick.shown[quick.index]) { e.preventDefault(); pick(quick.shown[quick.index].path) }
      })
      quick.el.addEventListener('mousedown', e => { if (e.target === quick.el) closeQuick() })
      quick.el.querySelector('.qo-list').addEventListener('click', e => { const row = e.target.closest('[data-path]'); if (row) pick(row.dataset.path) })
    }
    quick.el.hidden = false
    const input = quick.el.querySelector('.qo-input')
    input.value = ''
    input.focus()
    quick.index = 0
    listQuick('Loading files…')
    try {
      const data = await wb.request('op', { method: 'POST', body: JSON.stringify({ op: 'file.list', args: {} }) })
      quick.files = (data.result || data).files || []
    } catch (err) { quick.files = []; listQuick(err.message); return }
    listQuick()
  }
  function listQuick(message) {
    const text = quick.el.querySelector('.qo-input').value.trim(), list = quick.el.querySelector('.qo-list')
    if (message) { list.innerHTML = `<div class="qo-empty">${esc(message)}</div>`; return }
    quick.shown = quick.files.map(f => ({ ...f, s: score(f.path, text) })).filter(f => f.s > 0).sort((a, b) => b.s - a.s).slice(0, 60)
    list.innerHTML = quick.shown.length ? quick.shown.map((f, i) => {
      const slash = f.path.lastIndexOf('/')
      return `<div class="qo-row${i === quick.index ? ' active' : ''}" role="option" aria-selected="${i === quick.index}" data-path="${esc(f.path)}">
        <span class="tree-icon file">▧</span><b>${esc(f.path.slice(slash + 1))}</b><span class="fd-muted">${esc(slash > 0 ? f.path.slice(0, slash) : '')}</span>
        ${f.on_board ? '<span class="qo-tag">on board</span>' : ''}</div>`
    }).join('') : '<div class="qo-empty">No file matches</div>'
    list.querySelector('.active')?.scrollIntoView({ block: 'nearest' })
  }
  function pick(path) { closeQuick(); open(path, 'code') }
  function closeQuick() { if (quick.el) quick.el.hidden = true }
  document.addEventListener('click', e => { if (e.target.closest('[data-action="open-file"]')) { e.preventDefault(); e.stopPropagation(); document.querySelectorAll('.menu-pop').forEach(m => m.closest('.menu-wrap')?.classList.remove('open')); quickOpen() } }, true)

  // -- Files tab -----------------------------------------------------------------------------
  function filesTree(all) {
    const files = all.filter(b => b.kind === 'file' && b.path).sort((a, b) => a.path.localeCompare(b.path))
    if (!files.length) return '<div class="side-section-title">PROJECT FILES</div><div class="empty-state">No files on the board yet. Use File › Import project.</div>'
    const withSteps = new Set(all.filter(b => b.kind === 'step').map(b => b.meta?.file))
    let last = []
    const rows = files.map(f => {
      const parts = f.path.split('/'), folders = parts.slice(0, -1)
      let html = ''
      folders.forEach((name, depth) => {
        if (last[depth] !== name) html += `<div class="tree-row" style="padding-left:${10 + depth * 14}px"><span class="tree-icon">▰</span>${esc(name)}</div>`
      })
      last = folders
      return html + `<div class="tree-row${panel.path === f.path ? ' selected' : ''}" data-open="${esc(f.id)}" style="padding-left:${10 + folders.length * 14}px" title="${esc(f.path)}">
        <span class="tree-icon file">▧</span>${esc(parts[parts.length - 1])}${withSteps.has(f.path) ? '<span class="tree-count" title="Has plain-English blocks">◆</span>' : ''}</div>`
    }).join('')
    return `<div class="side-section-title">PROJECT FILES <button data-action="open-file" title="Open any file (⌘P)">⌕</button></div>${rows}`
  }

  window.PlovaneCodeView = { focus, draw, maxScale, open, close, show, filesTree, highlight, quickOpen }
  if (wb.board) wb.render()
})()
