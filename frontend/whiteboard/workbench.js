// Workbench: a VS Code-style editor over the canvas. The Files tab is its Explorer (every project
// file, collapsible folders); files open in tabs with line numbers, live colours, auto-indent and ⌘S
// saving through /file save (undoable, refuses to overwrite newer changes on disk). The first tab
// returns to the whiteboard; the prompt stays at the bottom the whole time.
(() => {
  const wb = window.PlovaneWhiteboard
  if (!wb) return
  const $ = (s, root = document) => root.querySelector(s)
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  const op = (name, args) => wb.request('op', { method: 'POST', body: JSON.stringify({ op: name, args }) })
  const highlight = (...a) => window.PlovaneCodeView?.highlight(...a) ?? esc(a[0])

  const LANG = { py: 'python', js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript', ts: 'typescript', tsx: 'typescript', c: 'cpp', cc: 'cpp', cpp: 'cpp', h: 'cpp', hpp: 'cpp', ino: 'cpp' }
  const LABEL = { python: 'Python', javascript: 'JavaScript', typescript: 'TypeScript', cpp: 'C / C++', text: 'Plain text' }
  const ext = p => (p.split('/').pop().split('.').pop() || '').toLowerCase()
  const languageOf = p => LANG[ext(p)] || 'text'
  const ICONS = { py: ['Py', '#3572a5'], js: ['JS', '#c9a400'], jsx: ['JS', '#c9a400'], mjs: ['JS', '#c9a400'], ts: ['TS', '#3178c6'], tsx: ['TS', '#3178c6'],
    c: ['C', '#5c6bc0'], cpp: ['C+', '#00599c'], h: ['H', '#7b61c9'], hpp: ['H', '#7b61c9'], ino: ['∞', '#00979d'], md: ['M', '#6b7280'], json: ['{}', '#b58900'],
    html: ['<>', '#e34c26'], css: ['#', '#563d7c'], yml: ['Y', '#cb171e'], yaml: ['Y', '#cb171e'], toml: ['T', '#9c4221'], sh: ['$', '#4eaa25'], txt: ['≡', '#8a919d'] }
  const icon = p => { const [t, c] = ICONS[ext(p)] || ['▧', '#8a919d']; return `<span class="wb-ficon" style="--c:${c}">${t}</span>` }
  const indentUnit = p => languageOf(p) === 'python' ? '    ' : '  '

  // -- State ------------------------------------------------------------------------------------
  const st = { files: null, loading: false, open: new Set(JSON.parse(localStorage.getItem('plovane-wb-open') || '[]')), tabs: [], active: null, shown: false, explorer: '' }
  const tabOf = path => st.tabs.find(t => t.path === path)
  const dirty = t => t && t.draft != null && t.draft !== t.text
  const saveOpenState = () => localStorage.setItem('plovane-wb-open', JSON.stringify([...st.open]))

  // -- Explorer (the Files tab) -------------------------------------------------------------------
  async function loadFiles(force = false) {
    if (st.loading || (st.files && !force)) return
    st.loading = true
    try { st.files = ((await op('file.list', {})).result || {}).files || [] } catch (err) { st.files = []; st.error = err.message }
    st.loading = false
    paintExplorer()
  }
  function tree() {
    const root = { dirs: new Map(), files: [] }
    for (const f of st.files || []) {
      const parts = f.path.split('/')
      let node = root
      parts.slice(0, -1).forEach((name, i) => {
        if (!node.dirs.has(name)) node.dirs.set(name, { dirs: new Map(), files: [], path: parts.slice(0, i + 1).join('/') })
        node = node.dirs.get(name)
      })
      node.files.push(f)
    }
    return root
  }
  function rows(node, depth) {
    let html = ''
    for (const [name, dir] of [...node.dirs].sort((a, b) => a[0].localeCompare(b[0]))) {
      const open = st.open.has(dir.path)
      html += `<div class="wb-row wb-dir" data-wb="toggle" data-path="${esc(dir.path)}" style="--d:${depth}" role="treeitem" aria-expanded="${open}">
        <span class="wb-chev${open ? ' open' : ''}">›</span><span class="wb-folder">${open ? '▱' : '▰'}</span>${esc(name)}</div>`
      if (open) html += rows(dir, depth + 1)
    }
    for (const f of node.files.sort((a, b) => a.path.localeCompare(b.path))) {
      const t = tabOf(f.path)
      html += `<div class="wb-row wb-file${st.active === f.path && st.shown ? ' active' : ''}" data-wb="open" data-path="${esc(f.path)}" style="--d:${depth}" role="treeitem" title="${esc(f.path)}">
        <span class="wb-chev"></span>${icon(f.path)}<span class="wb-fname">${esc(f.path.split('/').pop())}</span>${dirty(t) ? '<i class="wb-dot"></i>' : f.on_board ? '<i class="wb-onboard" title="On the board">◆</i>' : ''}</div>`
    }
    return html
  }
  function explorerHTML() {
    if (!st.files) { loadFiles(); return explorerFrame('<div class="wb-empty">Loading files…</div>') }
    if (!st.files.length) return explorerFrame(`<div class="wb-empty">${esc(st.error || 'No files in this project yet.')}</div>`)
    return explorerFrame(rows(tree(), 0))
  }
  function explorerFrame(body) {
    const project = ((wb.board || {}).project || '').split('/').filter(Boolean).pop() || 'Project'
    st.explorer = `<div class="wb-explorer"><div class="wb-ehead"><span>EXPLORER</span><div>
      <button data-wb="quick" title="Open a file (⌘P)" aria-label="Open a file">⌕</button>
      <button data-wb="new-file" title="New file" aria-label="New file">＋</button>
      <button data-wb="refresh" title="Refresh" aria-label="Refresh files">↻</button>
      <button data-wb="collapse" title="Collapse folders" aria-label="Collapse folders">⊟</button></div></div>
      <div class="wb-project">${esc(project)}</div><div class="wb-tree" role="tree">${body}</div></div>`
    return st.explorer
  }
  // Repaint the Explorer in place when the Files tab is showing (the page also asks for it on every render).
  function paintExplorer() {
    const host = $('#sidebar-content')
    if (host && $('.side-tab.active')?.dataset.tab === 'files') {
      const scroll = $('.wb-tree', host)?.scrollTop || 0
      host.innerHTML = explorerHTML()
      const t = $('.wb-tree', host); if (t) t.scrollTop = scroll
    }
  }

  // -- Workbench shell ----------------------------------------------------------------------------
  const shell = document.createElement('section')
  shell.className = 'workbench'
  shell.hidden = true
  shell.setAttribute('aria-label', 'Code editor')
  shell.innerHTML = `<nav class="wb-tabs" role="tablist"></nav><div class="wb-crumbs"></div><div class="wb-notice" hidden></div>
    <div class="wb-editor"><div class="wb-gutter" aria-hidden="true"></div><div class="wb-scroll"><div class="wb-lines"><div class="wb-current" aria-hidden="true"></div>
    <pre class="wb-hl" aria-hidden="true"></pre><textarea class="wb-input" spellcheck="false" autocapitalize="off" autocomplete="off" autocorrect="off" wrap="off"></textarea></div></div></div>
    <footer class="wb-status"></footer>`
  $('.canvas-area').append(shell)
  const input = $('.wb-input', shell), hl = $('.wb-hl', shell), gutter = $('.wb-gutter', shell), scroller = $('.wb-scroll', shell)
  const LINE = 21 // px, matches --wb-line in the CSS

  function paintTabs() {
    $('.wb-tabs', shell).innerHTML = `<button class="wb-tab wb-board" data-wb="board" role="tab" title="Back to the whiteboard"><span>◇</span>Whiteboard</button>` +
      st.tabs.map(t => `<div class="wb-tab${t.path === st.active ? ' active' : ''}" role="tab" aria-selected="${t.path === st.active}" data-wb="tab" data-path="${esc(t.path)}" title="${esc(t.path)}">
        ${icon(t.path)}<span>${esc(t.path.split('/').pop())}</span><button class="wb-close${dirty(t) ? ' dirty' : ''}" data-wb="close" data-path="${esc(t.path)}" aria-label="Close ${esc(t.path)}">${dirty(t) ? '●' : '×'}</button></div>`).join('')
    $('.wb-tabs .active', shell)?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }
  function paintCrumbs(t) {
    const parts = t.path.split('/')
    $('.wb-crumbs', shell).innerHTML = parts.map((p, i) => i === parts.length - 1 ? `${icon(t.path)}<b>${esc(p)}</b>` : `<span>${esc(p)}</span><i>›</i>`).join('')
  }
  function paintStatus() {
    const t = tabOf(st.active)
    if (!t) return
    const before = input.value.slice(0, input.selectionStart), line = before.split('\n').length, col = before.length - before.lastIndexOf('\n')
    const selected = Math.abs(input.selectionEnd - input.selectionStart)
    $('.wb-status', shell).innerHTML = `<span>${dirty(t) ? '<b class="wb-unsaved">● Unsaved</b>' : t.saved ? '✓ Saved' : 'No changes'}</span>
      <span class="wb-spacer"></span><span>Ln ${line}, Col ${col}${selected ? ` (${selected} selected)` : ''}</span><span>Spaces: ${indentUnit(t.path).length}</span><span>UTF-8</span><span>${LABEL[languageOf(t.path)]}</span>`
    $('.wb-current', shell).style.transform = `translateY(${(line - 1) * LINE}px)`
  }
  let painting = 0
  function paintCode() {
    const t = tabOf(st.active)
    if (!t) return
    const state = { block: false }, lines = input.value.split('\n')
    hl.innerHTML = lines.map(l => highlight(l, languageOf(t.path), state)).join('\n') + '\n'
    if (gutter.childElementCount !== lines.length) gutter.innerHTML = lines.map((_, i) => `<span>${i + 1}</span>`).join('')
    input.style.height = '0px'
    input.style.width = '0px'
    input.style.height = `${Math.max(input.scrollHeight, scroller.clientHeight)}px`
    input.style.width = `${Math.max(input.scrollWidth, scroller.clientWidth)}px`
  }
  const schedulePaint = () => { cancelAnimationFrame(painting); painting = requestAnimationFrame(() => { paintCode(); paintStatus() }) }
  scroller.addEventListener('scroll', () => { gutter.scrollTop = scroller.scrollTop })

  function showTab(path) {
    const prev = tabOf(st.active)
    if (prev && prev.path !== path) { prev.draft = input.value; prev.scroll = scroller.scrollTop; prev.sel = [input.selectionStart, input.selectionEnd] }
    const t = tabOf(path)
    if (!t) return
    st.active = path
    input.value = t.draft ?? t.text ?? ''
    input.readOnly = t.text == null
    $('.wb-notice', shell).hidden = !t.notice
    $('.wb-notice', shell).innerHTML = t.notice ? `<span>${esc(t.notice)}</span><button data-wb="reload">Reload from disk</button>` : ''
    gutter.innerHTML = ''
    paintTabs(); paintCrumbs(t); paintCode()
    requestAnimationFrame(() => {
      scroller.scrollTop = t.scroll || (t.line ? Math.max(0, (t.line - 4) * LINE) : 0)
      if (t.line && !t.sel) { const at = input.value.split('\n').slice(0, t.line - 1).join('\n').length + (t.line > 1 ? 1 : 0); t.sel = [at, at] }
      input.focus({ preventScroll: true })
      if (t.sel) input.setSelectionRange(...t.sel)
      paintStatus()
    })
    paintExplorer()
  }

  async function open(path, line) {
    let t = tabOf(path)
    if (!t) {
      t = { path, text: null, draft: null, version: null, line: Number(line) || 0 }
      st.tabs.push(t)
      try {
        const r = (await op('code.open', { target: path })).result || {}
        t.text = r.content ?? ''
        t.version = r.version
      } catch (err) { t.text = ''; t.notice = err.message }
    } else if (line) { t.line = Number(line); t.sel = null; t.scroll = 0 }
    path.split('/').slice(0, -1).forEach((_, i, a) => st.open.add(a.slice(0, i + 1).join('/')))
    saveOpenState()
    show(true)
    showTab(path)
  }
  function show(on) {
    st.shown = on
    shell.hidden = !on
    document.body.classList.toggle('wb-editing', on)
    if (!on) { const t = tabOf(st.active); if (t) { t.draft = input.value; t.scroll = scroller.scrollTop } }
    paintExplorer()
  }
  function close(path) {
    const t = tabOf(path)
    if (!t) return
    if (path === st.active) t.draft = input.value
    if (dirty(t) && !window.confirm(`${path} has unsaved changes. Close it and lose them?`)) return
    const i = st.tabs.indexOf(t)
    st.tabs.splice(i, 1)
    if (path === st.active) {
      const next = st.tabs[i] || st.tabs[i - 1]
      if (next) showTab(next.path)
      else { st.active = null; show(false) }
    }
    paintTabs()
    paintExplorer()
  }
  async function save() {
    const t = tabOf(st.active)
    if (!t) return
    t.draft = input.value
    if (!dirty(t)) return
    try {
      const r = (await op('file.save', { path: t.path, content: t.draft, base: t.version })).result || {}
      Object.assign(t, { text: t.draft, version: r.version, saved: true, notice: '' })
      $('.wb-notice', shell).hidden = true
      wb.toast(r.blocks_refreshed ? `Saved ${t.path} and updated its blocks` : `Saved ${t.path}`)
      wb.refresh()
    } catch (err) {
      t.notice = /changed on disk/.test(err.message) ? 'This file changed on disk since you opened it, so it was not saved. Copy your edits, then reload.' : err.message
      $('.wb-notice', shell).hidden = false
      $('.wb-notice', shell).innerHTML = `<span>${esc(t.notice)}</span><button data-wb="reload">Reload from disk</button>`
    }
    paintTabs(); paintStatus(); paintExplorer()
  }
  async function reload() {
    const t = tabOf(st.active)
    if (!t || (dirty(t) && !window.confirm('Reload from disk? Your unsaved edits will be lost.'))) return
    const r = (await op('code.open', { target: t.path })).result || {}
    Object.assign(t, { text: r.content ?? '', draft: null, version: r.version, notice: '' })
    showTab(t.path)
  }

  // -- Editing: indentation like VS Code ----------------------------------------------------------
  input.addEventListener('input', () => {
    const t = tabOf(st.active); if (!t) return
    const was = dirty(t)
    t.draft = input.value
    schedulePaint()
    if (was !== dirty(t)) { paintTabs(); paintExplorer() }
  })
  for (const ev of ['keyup', 'click', 'select']) input.addEventListener(ev, () => paintStatus())
  input.addEventListener('keydown', e => {
    const t = tabOf(st.active); if (!t) return
    const unit = indentUnit(t.path), { selectionStart: a, selectionEnd: b, value } = input
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); return }
    if (e.key === 'Tab') {
      e.preventDefault()
      const lineStart = value.lastIndexOf('\n', a - 1) + 1
      if (e.shiftKey) { // outdent the current line
        const line = value.slice(lineStart), drop = line.startsWith(unit) ? unit.length : (line.match(/^ +/)?.[0].length || 0)
        if (drop) { input.setSelectionRange(lineStart, lineStart + drop); document.execCommand('delete'); input.setSelectionRange(Math.max(lineStart, a - drop), Math.max(lineStart, b - drop)) }
      } else document.execCommand('insertText', false, unit)
      return
    }
    if (e.key === 'Enter' && !e.metaKey && !e.ctrlKey && a === b) { // keep the indentation, and go one deeper after : { [ (
      e.preventDefault()
      const line = value.slice(value.lastIndexOf('\n', a - 1) + 1, a), lead = line.match(/^[ \t]*/)[0]
      const deeper = /[:{[(]\s*$/.test(line) ? unit : ''
      document.execCommand('insertText', false, '\n' + lead + deeper)
    }
  })

  // -- Clicks: Explorer, tabs, notices ------------------------------------------------------------
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-wb]')
    if (!el) return
    const action = el.dataset.wb, path = el.dataset.path
    if (action === 'open') open(path)
    else if (action === 'toggle') { st.open.has(path) ? st.open.delete(path) : st.open.add(path); saveOpenState(); paintExplorer() }
    else if (action === 'collapse') { st.open.clear(); saveOpenState(); paintExplorer() }
    else if (action === 'refresh') { st.files = null; loadFiles(true) }
    else if (action === 'quick') window.PlovaneCodeView?.quickOpen()
    else if (action === 'new-file') {
      const name = window.prompt('New file path (from the project root)', 'src/new_file.py')
      if (name && name.trim()) wb.runCommand(`/file create ${name.trim()}`).then(() => { st.files = null; loadFiles(true).then(() => open(name.trim().replace(/^\//, ''))) })
    }
    else if (action === 'board') show(false)
    else if (action === 'tab') { if (!e.target.closest('[data-wb=close]')) showTab(path) }
    else if (action === 'close') { e.stopPropagation(); close(path) }
    else if (action === 'reload') reload()
  })
  $('.wb-tabs', shell).addEventListener('auxclick', e => { const t = e.target.closest('[data-wb=tab]'); if (t && e.button === 1) close(t.dataset.path) })
  window.addEventListener('beforeunload', e => { if (st.tabs.some(t => dirty(t) || (t.path === st.active && input.value !== t.text && t.text != null))) { e.preventDefault(); e.returnValue = '' } })
  new ResizeObserver(() => { if (st.shown) paintCode() }).observe(scroller)

  window.PlovaneWorkbench = { open, show, explorerHTML, refreshFiles: () => { st.files = null; loadFiles(true) }, get shown() { return st.shown } }
})()
