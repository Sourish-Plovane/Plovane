/*
Input: The verified library (/library, /library show, /rank) and the whiteboard's focus
Output: A searchable browser of every verified function, with its code and an insert button
Processing: Filters the whole library as you type, by language and category; shows one function's code; inserts it
Changed by: Claude Code
Date: 2026-11-15
*/

// Library browser: "Verified library" in the sidebar (or ⌘K) opens a search menu over every
// function in the library. Typing filters by name, description, repo and category at once; chips
// narrow by language and category; ↑ ↓ move, Enter inserts. "Search by meaning" asks the ranking
// model instead (slower the first time, while it loads).
(() => {
  const wb = window.PlovaneWhiteboard
  if (!wb) return
  const $ = (s, root = document) => root.querySelector(s)
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  const op = (name, args) => wb.request('op', { method: 'POST', body: JSON.stringify({ op: name, args }) }).then(d => d.result || {})
  const highlight = (code, lang) => {
    const hl = window.PlovaneCodeView?.highlight, state = { block: false }
    const language = ['python', 'javascript', 'typescript', 'cpp'].includes(lang) ? lang : 'text'
    return hl ? code.split('\n').map(line => hl(line, language, state)).join('\n') : esc(code)
  }

  const LANG = { python: ['Python', '#3572a5'], javascript: ['JavaScript', '#c9a400'], typescript: ['TypeScript', '#3178c6'], cpp: ['C++', '#00599c'], css: ['CSS', '#563d7c'], html: ['HTML', '#e34c26'] }
  const CATEGORY = { 'data-processing': 'Lists, objects & text', 'validation-parsing': 'Validation & parsing', 'api-http': 'HTTP & APIs', 'file-io': 'Files', 'math-numeric': 'Math & numbers',
    'styling-layout': 'Styling', 'auth-security': 'Security', 'build-tooling': 'Tooling', 'testing': 'Testing', 'concurrency-async': 'Async & timing', 'logging-observability': 'Logging', 'ui-components': 'UI components' }
  const PAGE = 120
  const st = { all: null, rows: [], shown: PAGE, query: '', language: '', category: '', selected: 0, meaning: null, detail: null, timer: 0 }

  const sheet = document.createElement('div')
  sheet.className = 'lib-sheet'
  sheet.hidden = true
  sheet.innerHTML = `
    <section class="lib-card" role="dialog" aria-modal="true" aria-label="Verified library">
      <header class="lib-head">
        <div class="lib-search"><span aria-hidden="true">⌕</span><input type="search" placeholder="Search verified functions… e.g. debounce, email, chunk a list" aria-label="Search the verified library" autocomplete="off" spellcheck="false"><kbd>esc</kbd></div>
        <div class="lib-filters"><div class="lib-chips" data-filter="language"></div><div class="lib-chips" data-filter="category"></div></div>
      </header>
      <div class="lib-body">
        <div class="lib-list-wrap"><div class="lib-count"></div><ul class="lib-list" role="listbox"></ul></div>
        <aside class="lib-detail"></aside>
      </div>
    </section>`
  document.body.append(sheet)
  const input = $('input', sheet), list = $('.lib-list', sheet), detail = $('.lib-detail', sheet), count = $('.lib-count', sheet)

  // -- Data -------------------------------------------------------------------------------------
  async function load() {
    if (st.all) return
    list.innerHTML = '<li class="lib-empty">Loading the library…</li>'
    try {
      st.all = await op('library.browse', {})
      for (const r of st.all.entries) r.hay = `${r.name} ${r.description} ${r.repo} ${r.category} ${r.signature}`.toLowerCase()
    } catch (e) { list.innerHTML = `<li class="lib-empty">${esc(e.message)}</li>`; throw e }
  }
  // Rank: exact name first, then name starts with, then name contains, then elsewhere.
  function filter() {
    const words = st.query.toLowerCase().split(/\s+/).filter(Boolean), q = words.join(' ')
    const score = r => { const n = r.name.toLowerCase().replace(/^_+/, ''); return n === q ? 0 : n.startsWith(q) ? 1 : n.includes(q) ? 2 : 3 }
    let rows = st.meaning || st.all.entries
    rows = rows.filter(r => (!st.language || r.language === st.language) && (!st.category || r.category === st.category))
    if (words.length && !st.meaning) rows = rows.filter(r => words.every(w => r.hay.includes(w))).sort((a, b) => score(a) - score(b))
    st.rows = rows; st.shown = PAGE; st.selected = Math.min(st.selected, Math.max(0, rows.length - 1))
  }

  // -- Rendering --------------------------------------------------------------------------------
  function chips() {
    const counts = key => { const c = {}; for (const r of st.all.entries) if ((key === 'language' || !st.language || r.language === st.language) && (key === 'category' || !st.category || r.category === st.category)) c[r[key]] = (c[r[key]] || 0) + 1; return c }
    const render = (key, names) => {
      const c = counts(key), all = key === 'language' ? 'All languages' : 'All kinds'
      const items = Object.keys(st.all[key === 'language' ? 'languages' : 'categories']).filter(k => c[k] || st[key] === k)
      $(`[data-filter=${key}]`, sheet).innerHTML = `<button class="${st[key] ? '' : 'on'}" data-${key}="">${all}</button>` +
        items.map(k => `<button class="${st[key] === k ? 'on' : ''}" data-${key}="${esc(k)}">${key === 'language' ? `<i style="--c:${(LANG[k] || [0, '#8a919d'])[1]}"></i>` : ''}${esc((Array.isArray(names[k]) ? names[k][0] : names[k]) || k)}<small>${c[k] || 0}</small></button>`).join('')
    }
    render('language', LANG); render('category', CATEGORY)
  }
  function row(r, i) {
    const [label, color] = LANG[r.language] || [r.language, '#8a919d']
    return `<li role="option" class="lib-row${i === st.selected ? ' sel' : ''}" data-i="${i}" aria-selected="${i === st.selected}">
      <div class="lib-row-top"><b>${esc(r.name)}</b><span class="lib-lang" style="--c:${color}">${esc(label)}</span></div>
      <p>${esc(r.description || r.signature || 'No description')}</p>
      <small>${esc(CATEGORY[r.category] || r.category)}${r.repo ? ` · ${esc(r.repo)}` : ''}${r.score != null ? ` · match ${Math.round(r.score * 100)}%` : ''}${r.needs?.length ? ' · <em class="lib-needs">needs project files</em>' : ''}</small></li>`
  }
  function renderList() {
    const total = st.all.total, n = st.rows.length
    count.innerHTML = st.meaning ? `<span>${n} closest by meaning</span><button data-lib="clear-meaning">Back to all ${total}</button>`
      : `<span>${n === total ? `All ${total} verified functions` : `${n} of ${total}`}</span>${st.query.trim() ? '<button data-lib="meaning" title="Ask the ranking model which functions do what you described">✦ Search by meaning</button>' : ''}`
    list.innerHTML = n ? st.rows.slice(0, st.shown).map(row).join('') + (n > st.shown ? `<li class="lib-more"><button data-lib="more">Show ${Math.min(PAGE, n - st.shown)} more</button></li>` : '')
      : `<li class="lib-empty">Nothing matches “${esc(st.query)}”.${st.query.trim() ? '<br><button data-lib="meaning">✦ Search by meaning instead</button>' : ''}</li>`
    showDetail()
  }
  async function showDetail() {
    const r = st.rows[st.selected]
    if (!r) { detail.innerHTML = '<div class="lib-empty">Pick a function to see its code.</div>'; return }
    if (st.detail?.block_id === r.block_id) return
    st.detail = { block_id: r.block_id }
    const [label, color] = LANG[r.language] || [r.language, '#8a919d']
    detail.innerHTML = `<div class="lib-d-head"><h3>${esc(r.name)}</h3><span class="lib-lang" style="--c:${color}">${esc(label)}</span></div>
      <p class="lib-d-desc">${esc(r.description || 'No description in the source.')}</p>
      <dl><dt>Kind</dt><dd>${esc(CATEGORY[r.category] || r.category)}</dd><dt>Licence</dt><dd>${esc(r.license || 'unknown')}</dd>${r.repo ? `<dt>From</dt><dd>${esc(r.repo)}</dd>` : ''}</dl>
      ${r.needs?.length ? `<div class="lib-warn"><b>Needs code from ${esc(r.repo || 'its project')}</b> It imports other files of its original project, so a copy alone will not run until you bring those in or rewrite these lines:<code>${r.needs.map(esc).join('<br>')}</code></div>` : ''}
      <pre class="lib-code"><code>Loading…</code></pre>
      <div class="lib-insert"><label>Insert into <input name="into" value="${esc(defaultInto())}" placeholder="folder, e.g. src/utils" spellcheck="false"></label>
        <div class="lib-actions"><button data-lib="copy" class="secondary">Copy code</button><button data-lib="insert">Insert ↵</button></div>
        <small>Copies the exact tested code into the project, with its licence and source.</small></div>`
    try {
      const full = await op('library.show', { entry: r.block_id })
      if (st.detail?.block_id !== r.block_id) return
      st.detail = full
      $('.lib-code', detail).innerHTML = `<code>${highlight(full.code || '', r.language)}</code>`
      if (full.source_permalink) $('dl', detail).insertAdjacentHTML('beforeend', `<dt>Source</dt><dd><a href="${esc(full.source_permalink)}" target="_blank" rel="noopener">View on GitHub ↗</a></dd>`)
    } catch (e) { $('.lib-code', detail).textContent = e.message }
  }
  const select = i => {
    if (!st.rows.length) return
    st.selected = Math.max(0, Math.min(st.rows.length - 1, i))
    if (st.selected >= st.shown) { st.shown = st.selected + PAGE; renderList() }
    list.querySelectorAll('.lib-row').forEach(el => { const on = +el.dataset.i === st.selected; el.classList.toggle('sel', on); el.setAttribute('aria-selected', on) })
    list.querySelector('.lib-row.sel')?.scrollIntoView({ block: 'nearest' })
    showDetail()
  }

  // -- Actions ----------------------------------------------------------------------------------
  function defaultInto() {
    const blocks = wb.board?.board?.blocks || {}, focus = blocks[wb.focus]
    if (focus?.kind === 'folder' && focus.path) return focus.path
    if (focus?.kind === 'file' && focus.path) return focus.path.split('/').slice(0, -1).join('/') || ''
    return localStorage.getItem('plovane-lib-into') || 'lib'
  }
  async function insert() {
    const r = st.rows[st.selected]; if (!r) return
    const into = ($('input[name=into]', detail)?.value || '').trim().replace(/^\/+/, '') || 'lib'
    localStorage.setItem('plovane-lib-into', into)
    try {
      const done = await op('library.insert', { entry: r.block_id, into })
      wb.toast(`Inserted ${r.name} into ${done.file}`)
      close(); await wb.refresh?.()
    } catch (e) { wb.toast(e.message, true) }
  }
  async function copy() {
    const code = st.detail?.code; if (!code) return
    try { await navigator.clipboard.writeText(code); wb.toast(`Copied ${st.detail.name}`) } catch { wb.toast('Could not copy; select the code instead', true) }
  }
  async function meaning() {
    const q = st.query.trim(); if (!q) return
    count.innerHTML = '<span>✦ Asking the ranking model… (the first search loads it, which can take a minute)</span>'
    try {
      const found = await op('library.rank', { query: q, k: 25, language: st.language || undefined })
      const byId = new Map(st.all.entries.map(r => [r.block_id, r]))
      st.meaning = (found.recommendations || []).map(c => ({ ...(byId.get(c.block_id) || { block_id: c.block_id, name: c.name, language: c.language, category: '', description: c.description || '' }), score: c.score }))
      st.selected = 0; filter(); renderList()
    } catch (e) { wb.toast(e.message, true); renderList() }
  }

  // -- Events -----------------------------------------------------------------------------------
  input.addEventListener('input', () => { clearTimeout(st.timer); st.timer = setTimeout(() => { st.query = input.value; st.meaning = null; st.selected = 0; filter(); renderList() }, 60) })
  sheet.addEventListener('keydown', e => {
    if (e.key === 'Escape') { e.preventDefault(); close() }
    else if (e.key === 'ArrowDown') { e.preventDefault(); select(st.selected + 1) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); select(st.selected - 1) }
    else if (e.key === 'Enter' && e.target === input) { e.preventDefault(); if (st.rows.length) insert(); else meaning() }
    e.stopPropagation()
  })
  sheet.addEventListener('click', e => {
    if (e.target === sheet) return close()
    const lang = e.target.closest('[data-language]'), cat = e.target.closest('[data-category]')
    if (lang || cat) { if (lang) st.language = lang.dataset.language; if (cat) st.category = cat.dataset.category; st.selected = 0; filter(); chips(); renderList(); input.focus(); return }
    const r = e.target.closest('.lib-row'); if (r) { select(+r.dataset.i); return }
    const a = e.target.closest('[data-lib]')?.dataset.lib
    if (a === 'more') { st.shown += PAGE; renderList() }
    else if (a === 'insert') insert()
    else if (a === 'copy') copy()
    else if (a === 'meaning') meaning()
    else if (a === 'clear-meaning') { st.meaning = null; filter(); renderList() }
  })
  document.addEventListener('keydown', e => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k' && !e.shiftKey) { e.preventDefault(); sheet.hidden ? open() : close() } })

  async function open(query) {
    sheet.hidden = false
    if (query != null) input.value = query
    input.focus(); input.select()
    try { await load() } catch { return }
    st.query = input.value; filter(); chips(); renderList()
  }
  function close() { sheet.hidden = true }

  window.PlovaneLibrary = { open, close }
})()
