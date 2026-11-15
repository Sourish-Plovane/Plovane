// Agent studio: an icon palette you drag onto the board, AI agents as bubbles above the canvas,
// a setup sheet (MCP or API key), and a permissions sheet (instructions, files and folders, who can
// approve access). Drag an agent's arrow onto any card to give it access to that folder or file.
(() => {
  const wb = window.PlovaneWhiteboard
  if (!wb) return
  const $ = (s, root = document) => root.querySelector(s)
  const $$ = (s, root = document) => [...root.querySelectorAll(s)]
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  const quote = v => /^[\w./:-]+$/.test(v) ? v : `"${String(v).replaceAll('"', '\\"')}"`

  const board = () => { const d = wb.board || {}; return d.board || d }
  const agents = () => Object.values(board().agents || {})
  const blockById = id => (board().blocks || {})[id]
  const op = (name, args, confirm = false) => wb.request('op', { method: 'POST', body: JSON.stringify({ op: name, args, confirm }) })
  const command = (text, confirm = false) => wb.request('command', { method: 'POST', body: JSON.stringify({ command: text, confirm }) })

  const PROVIDERS = [
    { id: 'anthropic', label: 'Claude', hint: 'Anthropic' },
    { id: 'deepseek', label: 'DeepSeek', hint: 'DeepSeek' },
    { id: 'openai', label: 'OpenAI', hint: 'GPT' },
  ]
  const providerLabel = id => PROVIDERS.find(p => p.id === id)?.label || id
  const HUES = [262, 200, 152, 28, 330, 12, 182, 290]
  const hueOf = name => HUES[[...name].reduce((n, c) => n + c.charCodeAt(0), 0) % HUES.length]
  const initials = name => name.replace(/[-_]+/g, ' ').trim().split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase() || '✦'

  // -- Where a path is reachable for an agent: the most specific grant wins ------------------
  function accessOf(agent, path) {
    let best = null
    for (const g of agent.grants || []) {
      const p = g.path === '.' ? '' : String(g.path).replace(/\/$/, '')
      if (p === '' || path === p || path.startsWith(p + '/')) if (!best || p.length >= best.p.length) best = { p, mode: g.mode }
    }
    return best ? best.mode : 'none'
  }
  const pathOf = b => b ? (b.path || b.meta?.file || '') : ''
  const grantsSummary = a => {
    const g = a.grants || []
    if (!g.length) return 'No access yet'
    return g.map(x => `${x.path === '.' ? 'Whole project' : x.path} · ${x.mode === 'read-write' ? 'can edit' : x.mode === 'read' ? 'can read' : 'blocked'}`).join(', ')
  }

  // ======================================================================== Palette =====
  const ITEMS = [
    { kind: 'agent', icon: '✦', label: 'AI agent', tip: 'Drag onto the board to add an AI agent' },
    { kind: 'service', icon: '⬡', label: 'Service' },
    { kind: 'database', icon: '▤', label: 'Database' },
    { kind: 'queue', icon: '⇢', label: 'Queue' },
    { kind: 'external', icon: '↗', label: 'External API' },
    { kind: 'folder', icon: '▰', label: 'Folder' },
    { kind: 'file', icon: '▧', label: 'File' },
    { kind: 'note', icon: '✎', label: 'Note' },
  ]
  const viewport = $('#canvas-viewport')
  const palette = document.createElement('div')
  palette.className = 'studio-palette'
  palette.setAttribute('role', 'toolbar')
  palette.setAttribute('aria-label', 'Add to the board')
  palette.innerHTML = ITEMS.map((it, i) => `${i === 1 ? '<span class="sp-sep"></span>' : ''}<button class="sp-item${it.kind === 'agent' ? ' sp-agent' : ''}" data-kind="${it.kind}"
    title="${esc(it.tip || `Drag onto the board to add a ${it.label.toLowerCase()}`)}" aria-label="Add ${esc(it.label)}"><span>${it.icon}</span><small>${esc(it.label)}</small></button>`).join('')
  viewport.append(palette)

  palette.addEventListener('pointerdown', e => {
    const item = e.target.closest('.sp-item')
    if (!item || e.button !== 0) return
    e.preventDefault()
    e.stopPropagation()
    const kind = item.dataset.kind, spec = ITEMS.find(x => x.kind === kind)
    const start = { x: e.clientX, y: e.clientY }
    let ghost = null
    const move = ev => {
      if (!ghost && Math.hypot(ev.clientX - start.x, ev.clientY - start.y) < 4) return
      if (!ghost) {
        ghost = document.createElement('div')
        ghost.className = `sp-ghost${kind === 'agent' ? ' agent' : ''}`
        ghost.innerHTML = `<span>${spec.icon}</span>${esc(spec.label)}`
        document.body.append(ghost)
        viewport.classList.add('studio-dropping')
      }
      ghost.style.transform = `translate(${ev.clientX + 12}px, ${ev.clientY + 12}px)`
    }
    const up = ev => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      viewport.classList.remove('studio-dropping')
      const v = viewport.getBoundingClientRect()
      let x = ev.clientX, y = ev.clientY
      if (ghost) {
        ghost.remove()
        if (x < v.left || x > v.right || y < v.top || y > v.bottom) return
      } else { x = v.left + v.width / 2; y = v.top + v.height / 2 } // a click drops in the middle
      drop(kind, x, y)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  })

  function drop(kind, x, y) {
    const v = viewport.getBoundingClientRect()
    const place = [Math.min(.92, Math.max(.02, (x - v.left) / v.width)), Math.min(.9, Math.max(.04, (y - v.top) / v.height))]
    if (kind === 'agent') return newAgent(place)
    namePopover(kind, x - v.left, y - v.top)
  }

  // A small name box where something was dropped; Enter creates it inside the block you are looking at.
  function namePopover(kind, x, y) {
    $('.sp-name')?.remove()
    const spec = ITEMS.find(i => i.kind === kind)
    const box = document.createElement('form')
    box.className = 'sp-name'
    box.style.left = `${Math.min(x, viewport.clientWidth - 280)}px`
    box.style.top = `${Math.min(y, viewport.clientHeight - 120)}px`
    const placeholder = { folder: 'folder name', file: 'file name, e.g. app.py', note: 'what the note says' }[kind] || `${spec.label.toLowerCase()} name`
    box.innerHTML = `<span class="sp-name-icon">${spec.icon}</span><input aria-label="${esc(spec.label)} name" placeholder="${esc(placeholder)}" autocomplete="off" spellcheck="false">
      <button class="sp-name-ok" type="submit">Add</button>`
    viewport.append(box)
    const input = $('input', box)
    input.focus()
    const close = () => box.remove()
    input.addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); close() } })
    input.addEventListener('blur', () => setTimeout(() => { if (!box.contains(document.activeElement)) close() }, 150))
    box.addEventListener('submit', async e => {
      e.preventDefault()
      const name = input.value.trim()
      if (!name) return close()
      close()
      const focus = blockById(wb.focus)
      const folder = focus && ['folder', 'service', 'server'].includes(focus.kind) && focus.path ? focus.path.replace(/\/$/, '') : ''
      const inside = focus && ['folder', 'group', 'service', 'server'].includes(focus.kind) ? ` --inside ${quote(focus.id)}` : ''
      const text = kind === 'folder' ? `/folder create ${quote(folder ? `${folder}/${name}` : name)}`
        : kind === 'file' ? `/file create ${quote(folder ? `${folder}/${name}` : name)}`
          : `/block add ${kind} ${quote(name)}${inside}`
      await wb.runCommand(text)
    })
  }

  // ======================================================================== Agent bubbles =====
  const layer = document.createElement('div')
  layer.className = 'agent-layer'
  viewport.append(layer)
  const link = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  link.classList.add('agent-link')
  link.innerHTML = '<defs><marker id="agent-link-head" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z"/></marker></defs><path/>'
  viewport.append(link)
  let selected = null
  let lastKey = ''

  function renderBubbles() {
    const list = agents()
    const key = JSON.stringify(list.map(a => [a.name, a.place, a.connection, a.provider, a.grants, a.instructions && 1])) + selected
    if (key === lastKey) return
    lastKey = key
    layer.innerHTML = list.map((a, i) => {
      const place = a.place && a.place.x != null ? a.place : { x: .9, y: .1 + i * .13 }
      const via = a.connection === 'mcp' ? 'MCP' : a.connection === 'api' ? providerLabel(a.provider) || 'API key' : 'Not connected'
      const reach = (a.grants || []).length
      return `<div class="agent-bubble${selected === a.name ? ' selected' : ''}" data-agent="${esc(a.name)}" style="left:${(place.x * 100).toFixed(2)}%;top:${(place.y * 100).toFixed(2)}%;--hue:${hueOf(a.name)}">
        <button class="ab-avatar" data-ab="select" aria-label="${esc(a.name)}: ${esc(via)}, ${esc(grantsSummary(a))}">${esc(initials(a.name))}<i class="ab-dot ${a.connection ? 'on' : ''}"></i></button>
        <div class="ab-label"><b>${esc(a.name)}</b><small>${esc(via)} · ${reach ? `${reach} ${reach === 1 ? 'place' : 'places'}` : 'no access'}</small></div>
        <button class="ab-plus" data-ab="permissions" title="Permissions, instructions and access" aria-label="Edit ${esc(a.name)}'s permissions">+</button>
        <button class="ab-arrow" data-ab="arrow" title="Drag onto a folder or file to give access" aria-label="Drag to give ${esc(a.name)} access">↗</button>
      </div>`
    }).join('')
  }

  // Highlight the cards an agent can reach while you hover or select it.
  function showReach(name) {
    $$('#blocks [data-block]').forEach(n => n.classList.remove('reach-read', 'reach-write'))
    const a = name && board().agents?.[name]
    if (!a) return
    $$('#blocks [data-block]').forEach(n => {
      const path = pathOf(blockById(n.dataset.block))
      if (!path) return
      const mode = accessOf(a, path)
      if (mode === 'read-write') n.classList.add('reach-write')
      else if (mode === 'read') n.classList.add('reach-read')
    })
  }
  layer.addEventListener('pointerover', e => { const b = e.target.closest('.agent-bubble'); if (b) showReach(b.dataset.agent) })
  layer.addEventListener('pointerout', e => { if (!e.relatedTarget?.closest?.('.agent-bubble')) showReach(selected) })

  layer.addEventListener('pointerdown', e => {
    const bubble = e.target.closest('.agent-bubble'), part = e.target.closest('[data-ab]')?.dataset.ab
    if (!bubble || e.button !== 0) return
    e.stopPropagation()
    const name = bubble.dataset.agent
    if (part === 'arrow') return dragArrow(e, name, bubble)
    if (part === 'permissions') return
    // Drag the bubble to move it; a click selects it.
    e.preventDefault()
    const start = { x: e.clientX, y: e.clientY }, v = viewport.getBoundingClientRect(), r = bubble.getBoundingClientRect()
    const offset = { x: e.clientX - r.left, y: e.clientY - r.top }
    let moved = false
    const move = ev => {
      if (!moved && Math.hypot(ev.clientX - start.x, ev.clientY - start.y) < 4) return
      moved = true
      bubble.classList.add('dragging')
      bubble.style.left = `${((ev.clientX - offset.x - v.left) / v.width * 100).toFixed(2)}%`
      bubble.style.top = `${((ev.clientY - offset.y - v.top) / v.height * 100).toFixed(2)}%`
    }
    const up = ev => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      bubble.classList.remove('dragging')
      if (!moved) { selected = selected === name ? null : name; lastKey = ''; renderBubbles(); showReach(selected); return }
      const x = (ev.clientX - offset.x - v.left) / v.width, y = (ev.clientY - offset.y - v.top) / v.height
      op('agent.configure', { agent: name, place: [Math.min(.95, Math.max(0, x)), Math.min(.95, Math.max(0, y))] }).then(() => wb.refresh()).catch(err => wb.toast(err.message, true))
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  })
  layer.addEventListener('click', e => {
    const part = e.target.closest('[data-ab]')?.dataset.ab, bubble = e.target.closest('.agent-bubble')
    if (part === 'permissions' && bubble) permissions(bubble.dataset.agent)
  })
  layer.addEventListener('dblclick', e => { const b = e.target.closest('.agent-bubble'); if (b) permissions(b.dataset.agent) })

  // The card under a point. Tiles drawn on top of a card don't carry its id, so go by geometry:
  // the smallest card whose box holds the point.
  function cardAt(x, y) {
    let best = null, area = Infinity
    for (const n of $$('#blocks [data-block]')) {
      const r = n.getBoundingClientRect()
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom && r.width * r.height < area) { best = n; area = r.width * r.height }
    }
    return best
  }

  // Drag a line from the agent onto a card; dropping offers read or edit access to its folder or file.
  function dragArrow(e, name, bubble) {
    e.preventDefault()
    const v = viewport.getBoundingClientRect(), r = $('.ab-arrow', bubble).getBoundingClientRect()
    const from = { x: r.left + r.width / 2 - v.left, y: r.top + r.height / 2 - v.top }
    const path = $('path', link)
    link.classList.add('show')
    showReach(name)
    let target = null
    const move = ev => {
      const x = ev.clientX - v.left, y = ev.clientY - v.top, mid = (from.x + x) / 2
      path.setAttribute('d', `M${from.x},${from.y} C${mid},${from.y} ${mid},${y} ${x},${y}`)
      const hit = cardAt(ev.clientX, ev.clientY)
      if (hit !== target) { target?.classList.remove('link-target'); target = hit; target?.classList.add('link-target') }
    }
    const up = ev => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      link.classList.remove('show')
      target?.classList.remove('link-target')
      showReach(selected)
      if (!target) return
      const block = blockById(target.dataset.block), filePath = pathOf(block)
      if (!filePath) { wb.toast(`${block?.name || 'That block'} has no files behind it. Drop the arrow on a folder or file.`, true); return }
      grantPopover(name, filePath, ev.clientX - v.left, ev.clientY - v.top)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    move(e)
  }

  function grantPopover(agent, path, x, y) {
    $('.grant-pop')?.remove()
    const current = accessOf(board().agents?.[agent] || {}, path)
    const pop = document.createElement('div')
    pop.className = 'grant-pop'
    pop.style.left = `${Math.min(x, viewport.clientWidth - 300)}px`
    pop.style.top = `${Math.min(y, viewport.clientHeight - 170)}px`
    pop.innerHTML = `<p>Give <b>${esc(agent)}</b> access to</p><code>${esc(path)}</code>
      <div class="gp-choices"><button data-mode="read" class="${current === 'read' ? 'current' : ''}"><b>Can read</b><small>See the code</small></button>
      <button data-mode="read-write" class="${current === 'read-write' ? 'current' : ''}"><b>Can edit</b><small>Read and change it</small></button></div>
      <button class="gp-cancel">Cancel</button>`
    viewport.append(pop)
    const close = () => { pop.remove(); document.removeEventListener('pointerdown', outside, true) }
    const outside = e => { if (!pop.contains(e.target)) close() }
    setTimeout(() => document.addEventListener('pointerdown', outside, true))
    pop.addEventListener('click', async e => {
      if (e.target.closest('.gp-cancel')) return close()
      const mode = e.target.closest('[data-mode]')?.dataset.mode
      if (!mode) return
      close()
      try {
        await op('agent.grant', { agent, path, mode }, true)
        await wb.refresh()
        lastKey = ''
        wb.toast(`${agent} ${mode === 'read-write' ? 'can now edit' : 'can now read'} ${path}`)
      } catch (err) { wb.toast(err.message, true) }
    })
  }

  // ======================================================================== Sheets =====
  const sheet = document.createElement('div')
  sheet.className = 'studio-sheet'
  sheet.hidden = true
  document.body.append(sheet)
  const closeSheet = () => { sheet.hidden = true; sheet.innerHTML = '' }
  sheet.addEventListener('pointerdown', e => { if (e.target === sheet) closeSheet() })
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !sheet.hidden && !e.defaultPrevented) closeSheet() })

  let providerCache = null
  async function providers() {
    try { providerCache = (await wb.request('providers')).providers || [] } catch { providerCache = providerCache || [] }
    return providerCache
  }

  // -- New agent: name, then MCP or an API key -------------------------------------------------
  async function newAgent(place = null) {
    const list = await providers()
    const used = new Set(agents().map(a => a.name))
    let n = 1
    while (used.has(`agent-${n}`)) n++
    const state = { name: `agent-${n}`, connection: 'mcp', provider: list.find(p => p.configured)?.provider || 'anthropic', place }
    sheet.hidden = false
    const draw = () => {
      const p = list.find(x => x.provider === state.provider) || {}
      sheet.innerHTML = `<form class="ss-card ss-new" aria-label="Add an AI agent">
        <header><span class="ss-badge">✦</span><div><h2>Add an AI agent</h2><p>It works on this board within the access you give it.</p></div>
          <button type="button" class="ss-close" data-ss="close" aria-label="Close">×</button></header>
        <label class="ss-field"><span>Name</span><input name="name" value="${esc(state.name)}" autocomplete="off" spellcheck="false" maxlength="40" required>
          <small>Letters, numbers and dashes. You will see it on the board and in its activity.</small></label>
        <div class="ss-field"><span>How does it connect?</span><div class="ss-choices">
          <button type="button" class="ss-choice${state.connection === 'mcp' ? ' on' : ''}" data-connection="mcp"><b>MCP</b><small>Claude Code, Cursor or any MCP app works on the board itself.</small></button>
          <button type="button" class="ss-choice${state.connection === 'api' ? ' on' : ''}" data-connection="api"><b>API key</b><small>Plovane runs the AI for you with your Claude, DeepSeek or OpenAI key.</small></button>
        </div></div>
        ${state.connection === 'api' ? `<div class="ss-field"><span>Provider</span><div class="ss-seg">${PROVIDERS.map(x => `<button type="button" data-provider="${x.id}" class="${state.provider === x.id ? 'on' : ''}">${esc(x.label)}</button>`).join('')}</div></div>
        <label class="ss-field"><span>${esc(providerLabel(state.provider))} API key</span><input name="key" type="password" autocomplete="off" spellcheck="false"
          placeholder="${p.configured ? 'Saved on this Mac. Paste a new key to replace it.' : 'Paste your key'}">
          <small>Kept in ~/.plovane/keys.json on this computer, readable only by you. Never shown again.</small></label>` : ''}
        <footer><button type="button" class="ss-ghost" data-ss="close">Cancel</button><button type="submit" class="ss-primary">Add agent</button></footer>
      </form>`
      $('input[name=name]', sheet).focus()
    }
    draw()
    sheet.onclick = e => {
      const t = e.target.closest('button')
      if (!t) return
      if (t.dataset.ss === 'close') return closeSheet()
      const nameInput = $('input[name=name]', sheet)
      if (nameInput) state.name = nameInput.value
      if (t.dataset.connection) { state.connection = t.dataset.connection; draw() }
      if (t.dataset.provider) { state.provider = t.dataset.provider; draw() }
    }
    sheet.onsubmit = async e => {
      e.preventDefault()
      const form = e.target, name = form.name.value.trim().replace(/\s+/g, '-')
      if (!/^[A-Za-z0-9][\w-]{0,39}$/.test(name)) { wb.toast('Use letters, numbers and dashes for the name.', true); return }
      if (used.has(name)) { wb.toast(`There is already an agent called ${name}.`, true); return }
      const key = form.key?.value.trim(), submit = $('.ss-primary', form)
      submit.disabled = true
      try {
        await command(`/agent add ${quote(name)}`)
        if (state.connection === 'api' && key) await wb.request(`providers/${state.provider}/key`, { method: 'POST', body: JSON.stringify({ key }) })
        await op('agent.configure', { agent: name, connection: state.connection, provider: state.connection === 'api' ? state.provider : '', ...(state.place ? { place: state.place } : {}) })
        await wb.refresh()
        selected = name
        lastKey = ''
        if (state.connection === 'mcp') connectStep(name)
        else permissions(name, { created: true })
      } catch (err) { submit.disabled = false; wb.toast(err.message, true) }
    }
  }

  // -- After an MCP agent is added: how to connect the AI app ----------------------------------
  function connectStep(name) {
    const project = (wb.board || {}).project || '<project folder>'
    const cli = `claude mcp add plovane -- plovane mcp --dir ${quote(project)} --agent ${quote(name)}`
    const json = JSON.stringify({ mcpServers: { plovane: { command: 'plovane', args: ['mcp', '--dir', project, '--agent', name] } } }, null, 2)
    sheet.hidden = false
    sheet.innerHTML = `<div class="ss-card ss-new" aria-label="Connect ${esc(name)}">
      <header><span class="ss-badge ok">✓</span><div><h2>${esc(name)} is on the board</h2><p>Connect your AI app with one of these. Plovane creates the agent's key when it first connects.</p></div>
        <button type="button" class="ss-close" data-ss="close" aria-label="Close">×</button></header>
      <div class="ss-field"><span>Claude Code: run in a terminal</span><div class="ss-code"><pre>${esc(cli)}</pre><button data-copy="${esc(cli)}">Copy</button></div></div>
      <div class="ss-field"><span>Cursor, Claude Desktop or another MCP app: add to its config</span><div class="ss-code"><pre>${esc(json)}</pre><button data-copy="${esc(json)}">Copy</button></div></div>
      <p class="ss-note">It can't read or change anything until you give it access.</p>
      <footer><button type="button" class="ss-ghost" data-ss="close">Later</button><button type="button" class="ss-primary" data-ss="permissions">Set permissions →</button></footer>
    </div>`
    sheet.onsubmit = null
    sheet.onclick = e => {
      const t = e.target.closest('button')
      if (!t) return
      if (t.dataset.copy) navigator.clipboard?.writeText(t.dataset.copy).then(() => { t.textContent = 'Copied'; setTimeout(() => { t.textContent = 'Copy' }, 1400) }, () => wb.toast('Select the text and copy it.', true))
      if (t.dataset.ss === 'close') closeSheet()
      if (t.dataset.ss === 'permissions') permissions(name, { created: true })
    }
  }

  // -- Permissions: instructions, files and folders, who can approve access --------------------
  const EXAMPLES = ['Only change files I gave you edit access to.', 'Ask before deleting anything.', 'Write a test for every change.', 'Follow the style already in the file.']
  let fileCache = null
  async function folderOptions() {
    if (!fileCache) {
      try { fileCache = ((await op('file.list', {})).result || {}).files?.map(f => f.path) || [] } catch { fileCache = [] }
    }
    const folders = new Set()
    fileCache.forEach(p => { const parts = p.split('/'); for (let i = 1; i < parts.length; i++) folders.add(parts.slice(0, i).join('/')) })
    return [...[...folders].sort().map(p => ({ path: p, folder: true })), ...fileCache.map(p => ({ path: p, folder: false }))]
  }

  async function permissions(name, { created = false } = {}) {
    const a = board().agents?.[name]
    if (!a) return
    const draft = {
      instructions: a.instructions || '',
      grants: (a.grants || []).map(g => ({ path: g.path, mode: g.mode })),
      approvers: [...(a.approvers || [])],
      who: (a.approvers || []).length ? 'team' : 'me',
      picking: false, query: '',
    }
    const options = await folderOptions()
    sheet.hidden = false
    const via = a.connection === 'mcp' ? 'Connected over MCP' : a.connection === 'api' ? `Runs with ${providerLabel(a.provider)}` : 'Not connected yet'
    const draw = (focusSel) => {
      const matches = options.filter(o => !draft.query || o.path.toLowerCase().includes(draft.query.toLowerCase())).slice(0, 40)
      sheet.innerHTML = `<form class="ss-card ss-perm" aria-label="${esc(name)} permissions">
        <header><span class="ss-avatar" style="--hue:${hueOf(name)}">${esc(initials(name))}</span><div><h2>${esc(name)}</h2><p>${esc(via)}</p></div>
          <button type="button" class="ss-close" data-ss="close" aria-label="Close">×</button></header>
        ${created ? '<div class="ss-hello">Tell it what to do, then choose what it may touch. You can change this any time with the + on its bubble.</div>' : ''}
        <section class="ss-section"><h3><span>1</span>Instructions</h3>
          <textarea name="instructions" rows="4" maxlength="4000" placeholder="What should ${esc(name)} work on? What should it never do?">${esc(draft.instructions)}</textarea>
          <div class="ss-chips">${EXAMPLES.map(x => `<button type="button" class="ss-chip" data-example="${esc(x)}">+ ${esc(x)}</button>`).join('')}</div></section>
        <section class="ss-section"><h3><span>2</span>Files and folders it can access</h3>
          ${draft.grants.length ? `<ul class="ss-grants">${draft.grants.map((g, i) => `<li><span class="ss-gicon">${g.path === '.' ? '◎' : options.find(o => o.path === g.path)?.folder === false ? '▧' : '▰'}</span>
            <code>${esc(g.path === '.' ? 'Whole project' : g.path)}</code>
            <div class="ss-seg small">${[['read', 'Can read'], ['read-write', 'Can edit'], ['none', 'Blocked']].map(([m, l]) => `<button type="button" data-grant="${i}" data-mode="${m}" class="${g.mode === m ? 'on' : ''}">${l}</button>`).join('')}</div>
            <button type="button" class="ss-x" data-remove="${i}" aria-label="Remove ${esc(g.path)}">×</button></li>`).join('')}</ul>`
            : '<p class="ss-empty">Nothing yet. It can\'t read or change any file.</p>'}
          ${draft.picking ? `<div class="ss-picker"><input name="query" placeholder="Search folders and files" value="${esc(draft.query)}" autocomplete="off">
            <div class="ss-options"><button type="button" data-pick="."><span>◎</span>Whole project</button>${matches.map(o => `<button type="button" data-pick="${esc(o.path)}"><span>${o.folder ? '▰' : '▧'}</span>${esc(o.path)}</button>`).join('')}</div></div>`
            : '<button type="button" class="ss-add" data-ss="pick">+ Add a folder or file</button>'}
          <p class="ss-tip">Tip: drag the ↗ arrow on its bubble onto any folder or file on the board.</p></section>
        <section class="ss-section"><h3><span>3</span>Who can give it access</h3>
          <div class="ss-radio"><label><input type="radio" name="who" value="me" ${draft.who === 'me' ? 'checked' : ''}><span><b>Only me</b><small>Only you can change what it may touch.</small></span></label>
          <label><input type="radio" name="who" value="team" ${draft.who === 'team' ? 'checked' : ''}><span><b>Me and teammates I choose</b><small>Teammates can approve once this board is shared. Agents never can.</small></span></label></div>
          ${draft.who === 'team' ? `<div class="ss-people">${draft.approvers.map((p, i) => `<span class="ss-person">${esc(p)}<button type="button" data-unperson="${i}" aria-label="Remove ${esc(p)}">×</button></span>`).join('')}
            <input name="person" placeholder="Name or email, then Enter" autocomplete="off"></div>` : ''}</section>
        <footer><button type="button" class="ss-ghost" data-ss="close">Cancel</button><button type="submit" class="ss-primary">Save</button></footer>
      </form>`
      if (focusSel) { const el = $(focusSel, sheet); if (el) { el.focus(); if (el.setSelectionRange && el.value) el.setSelectionRange(el.value.length, el.value.length) } }
    }
    const keep = () => { const t = $('textarea[name=instructions]', sheet); if (t) draft.instructions = t.value }
    draw()
    sheet.oninput = e => {
      if (e.target.name === 'query') { draft.query = e.target.value; keep(); draw('input[name=query]') }
      if (e.target.name === 'who') { keep(); draft.who = e.target.value; draw() }
    }
    sheet.onkeydown = e => {
      if (e.target.name === 'person' && e.key === 'Enter') {
        e.preventDefault()
        const v = e.target.value.trim()
        if (v && !draft.approvers.some(x => x.toLowerCase() === v.toLowerCase())) draft.approvers.push(v)
        keep(); draw('input[name=person]')
      }
      if (e.target.name === 'query' && e.key === 'Escape') { e.preventDefault(); draft.picking = false; draft.query = ''; keep(); draw() }
    }
    sheet.onclick = e => {
      const t = e.target.closest('button')
      if (!t) return
      keep()
      const d = t.dataset
      if (d.ss === 'close') return closeSheet()
      if (d.example) { draft.instructions = (draft.instructions.trim() ? draft.instructions.trim() + '\n' : '') + d.example; draw('textarea[name=instructions]') }
      if (d.ss === 'pick') { draft.picking = true; draw('input[name=query]') }
      if (d.pick) { if (!draft.grants.some(g => g.path === d.pick)) draft.grants.push({ path: d.pick, mode: 'read' }); draft.picking = false; draft.query = ''; draw() }
      if (d.grant) { draft.grants[Number(d.grant)].mode = d.mode; draw() }
      if (d.remove) { draft.grants.splice(Number(d.remove), 1); draw() }
      if (d.unperson) { draft.approvers.splice(Number(d.unperson), 1); draw() }
    }
    sheet.onsubmit = async e => {
      e.preventDefault()
      keep()
      const submit = $('.ss-primary', sheet)
      submit.disabled = true
      const before = new Map((a.grants || []).map(g => [g.path, g.mode])), after = new Map(draft.grants.map(g => [g.path, g.mode]))
      try {
        await op('agent.configure', { agent: name, instructions: draft.instructions, approvers: draft.who === 'team' ? draft.approvers : [] })
        for (const [path] of before) if (!after.has(path)) await op('agent.revoke', { agent: name, path }, true)
        for (const [path, mode] of after) if (before.get(path) !== mode) await op('agent.grant', { agent: name, path, mode }, true)
        await wb.refresh()
        lastKey = ''
        closeSheet()
        wb.toast(`${name}'s permissions saved`)
      } catch (err) { submit.disabled = false; wb.toast(err.message, true) }
    }
  }

  // ======================================================================== Wiring =====
  const tick = () => { renderBubbles(); requestAnimationFrame(() => setTimeout(tick, 400)) }
  tick()
  window.PlovaneStudio = { newAgent, permissions, connect: connectStep, refresh: () => { lastKey = ''; renderBubbles() } }
})()
