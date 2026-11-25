/*
Input: The overlay state in each board reply (definitions, roles, the person's view and per-block results)
Output: Overlay marks on cards, tiles and the frame, and the Overlays panel in the side bar
Processing: Draws each switched-on overlay with its channel (fill, border, badge, port, icon, and arrow colours from
  rule overlays) at the detail its appearance gives for the block's zoom level, and switches overlays and roles with
  /overlay and /role commands
Changed by: Claude Code
Date: 2026-11-25
*/

(() => {
  const wb = window.PlovaneWhiteboard
  if (!wb) return
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  const COLORS = { red: '#d93025', orange: '#e37400', blue: '#1a73e8', green: '#188038', purple: '#9334e6', gray: '#5f6368' }
  const CHANNEL_ICON = { fill: '◧', border: '▢', badge: '●', chip: '◉', icon: '✓', port: '⇄', arrows: '→', line_highlight: '≡' }
  const MAX_BADGES = 3

  const overlayState = () => wb.board?.overlays
  const colorOf = spec => {
    const c = spec?.style?.color
    return COLORS[c] || (/^#[0-9a-f]{6}$/i.test(c || '') ? c : COLORS.gray)
  }

  /**
   * Input: id (string)
   * Output: boolean
   * Processing: Whether an overlay is switched on; true when the server sent no overlay state (offline demo).
   * Changed by: Claude Code
   * Date: 2026-11-22
   */
  function isOn(id) {
    const s = overlayState()
    return !s || s.active.includes(id)
  }

  /**
   * Input: r (object)
   * Output: number
   * Processing: How many results a block has: its own items, or the count rolled up from inside it.
   * Changed by: Claude Code
   * Date: 2026-11-22
   */
  const amount = r => r.own ? Math.max(r.items.length, r.count || 0) : (r.count || 0)

  /**
   * Input: b (object)
   * Output: object
   * Processing: What every switched-on overlay draws on one block: CSS classes, a style, mark HTML and a tooltip.
   * Changed by: Claude Code
   * Date: 2026-11-22
   */
  function decorate(b) {
    const out = { cls: '', style: '', html: '', tip: '' }
    const s = overlayState()
    if (!s || !b) return out
    const level = String(b.layer)
    const badges = [], ports = [], tips = []
    let fill = null, border = null, icon = ''
    for (const id of s.active) {
      const spec = s.defs[id], r = s.results?.[id]?.[b.id]
      if (!spec || !r || spec.channel === 'chip') continue
      const look = spec.appearance?.[level] || 'hidden'
      if (look === 'hidden') continue
      const n = amount(r)
      if (!n && !r.status) continue
      const first = r.own ? r.items?.[0] : null
      const priority = spec.priority ?? 0, color = COLORS[first?.color] || colorOf(spec)
      for (const item of (r.items || []).slice(0, look === 'detail' ? 8 : 3)) {
        tips.push(`${spec.label}: ${item.message}${item.line ? ` (line ${item.line})` : ''}`)
      }
      if (!r.own && r.count) tips.push(`${spec.label}: ${r.count} inside`)
      if (spec.channel === 'fill') {
        if (r.status && (!fill || priority > fill.priority)) fill = { status: r.status, priority }
        if (look === 'count' && r.count && r.status !== 'tested') {
          badges.push({ text: `${r.count} ${r.status === 'failing' ? 'failing' : 'untested'}`, color: r.status === 'failing' ? COLORS.red : COLORS.orange, priority })
        }
      } else if (spec.channel === 'border') {
        if (!border || priority > border.priority) border = { color, priority }
        if (look === 'count') badges.push({ text: `${n} ${spec.style?.badge || spec.label}`, color, priority })
        else if (look === 'detail' && r.items?.[0]) badges.push({ text: r.items[0].message, color, priority })
      } else if (spec.channel === 'badge') {
        const label = first?.badge || spec.style?.badge || spec.label
        badges.push({ text: look === 'count' ? `${n} ${label}` : look === 'detail' && r.items?.[0] ? r.items[0].message : label, color, priority })
      } else if (spec.channel === 'port') {
        if (look === 'detail' && r.own) ports.push(...r.items.map(i => i.message))
        else ports.push(look === 'count' ? `${n} endpoint${n === 1 ? '' : 's'}` : `${n}`)
      } else if (spec.channel === 'icon') {
        icon = `<span class="ov-icon" style="--c:${color}">${esc(spec.style?.icon || '●')}${look === 'count' && n > 1 ? `<i>${n}</i>` : ''}</span>`
      }
    }
    badges.sort((a, z) => z.priority - a.priority)
    const shown = badges.slice(0, MAX_BADGES).map(x => `<span class="ov-badge" style="--c:${x.color}">${esc(x.text)}</span>`).join('')
    const more = badges.length > MAX_BADGES ? `<span class="ov-badge more">+${badges.length - MAX_BADGES}</span>` : ''
    const portHTML = ports.slice(0, 3).map(p => `<span class="ov-port">⇄ ${esc(p)}</span>`).join('') +
      (ports.length > 3 ? `<span class="ov-port more">+${ports.length - 3}</span>` : '')
    out.cls = `${fill ? ` ov-fill ov-fill-${fill.status}` : ''}${border ? ' ov-border' : ''}`
    out.style = border ? `--ov-border:${border.color};` : ''
    out.html = icon + (shown || more || portHTML ? `<span class="ov-marks">${shown}${more}${portHTML}</span>` : '')
    out.tip = tips.join('\n')
    return out
  }

  /**
   * Input: s (object), id (string)
   * Output: string[]
   * Processing: The drawing overlays inside a group, at any depth (or the overlay itself).
   * Changed by: Claude Code
   * Date: 2026-11-22
   */
  function members(s, id) {
    if (s.defs[id]?.source !== 'group') return [id]
    return Object.keys(s.defs).filter(k => s.defs[k].parent === id).flatMap(k => members(s, k))
  }

  /**
   * Input: s (object), id (string)
   * Output: number
   * Processing: How many places an overlay found something (its own results), for the panel.
   * Changed by: Claude Code
   * Date: 2026-11-22
   */
  function found(s, id) {
    return members(s, id).reduce((sum, m) => sum + Object.keys(s.arrows?.[m] || {}).length + Object.values(s.results?.[m] || {})
      .filter(r => r.own && (r.status ? r.status !== 'tested' : r.items.length)).length, 0)
  }

  const SEVERITY = { red: 5, orange: 4, purple: 3, blue: 2, gray: 1, green: 0 }

  /**
   * Input: id (string, a board arrow id)
   * Output: { color, tip } | null
   * Processing: How switched-on rule overlays colour one arrow: the most urgent colour among them and every message, for
   *   views that draw arrows themselves (the API diagram).
   * Changed by: Claude Code
   * Date: 2026-11-25
   */
  function arrowLook(id) {
    const s = overlayState(), hits = []
    for (const oid of s?.active || []) {
      const look = s.arrows?.[oid]?.[id], spec = s.defs?.[oid]
      if (look && spec) hits.push({ ...look, label: spec.label, priority: spec.priority ?? 0 })
    }
    if (!hits.length) return null
    hits.sort((a, z) => (SEVERITY[z.color] ?? 1) - (SEVERITY[a.color] ?? 1) || z.priority - a.priority)
    return { color: COLORS[hits[0].color] || COLORS.gray,
             tip: [...new Set(hits.filter(h => h.message).map(h => `${h.label}: ${h.message}`))].join('\n') }
  }

  /**
   * Input: none
   * Output: void
   * Processing: Colour the drawn arrows from switched-on rule overlays. An arrow on screen can stand for several
   *   arrows (between two containers), so it takes the most urgent colour among them (red before green) and lists
   *   every message in its tooltip.
   * Changed by: Claude Code
   * Date: 2026-11-25
   */
  function paintArrows() {
    const s = overlayState(), svg = document.getElementById('connections')
    if (!svg) return
    const blocks = wb.board?.board?.blocks || {}
    const chain = id => {
      const out = new Set()
      for (let b = blocks[id], guard = 0; b && guard < 40; b = blocks[b.parent], guard++) out.add(b.id)
      return out
    }
    const looks = []
    for (const id of s?.active || []) {
      const spec = s.defs?.[id]
      if (!spec) continue
      for (const look of Object.values(s.arrows?.[id] || {})) {
        looks.push({ ...look, label: spec.label, priority: spec.priority ?? 0, from: chain(look.source), to: chain(look.target) })
      }
    }
    svg.querySelectorAll('path[data-source]').forEach(path => {
      const text = path.nextElementSibling?.tagName === 'text' ? path.nextElementSibling : null
      path.style.stroke = ''
      path.classList.remove('ov-arrow')
      path.querySelector('title')?.remove()
      if (text) text.style.fill = ''
      if (!looks.length) return
      const eps = (path.dataset.endpoint || '').split('|').filter(Boolean)
      const hits = looks.filter(l => (l.endpoint && eps.includes(l.endpoint)) ||
        (l.from.has(path.dataset.source) && l.to.has(path.dataset.target)))
      if (!hits.length) return
      hits.sort((a, z) => (SEVERITY[z.color] ?? 1) - (SEVERITY[a.color] ?? 1) || z.priority - a.priority)
      const color = COLORS[hits[0].color] || COLORS.gray
      path.style.stroke = color
      path.classList.add('ov-arrow')
      if (text) text.style.fill = color
      const tip = [...new Set(hits.filter(h => h.message).map(h => `${h.label}: ${h.message}`))].join('\n')
      if (tip) {
        const title = document.createElementNS('http://www.w3.org/2000/svg', 'title')
        title.textContent = tip
        path.append(title)
      }
    })
  }

  /**
   * Input: s (object), id (string), depth (number)
   * Output: string
   * Processing: One row of the overlay tree, followed by the rows of the overlays inside it.
   * Changed by: Claude Code
   * Date: 2026-11-22
   */
  function row(s, id, depth) {
    const spec = s.defs[id], inside = members(s, id), lit = inside.filter(m => s.active.includes(m)).length
    const state = !inside.length || lit === 0 ? 'off' : lit === inside.length ? 'on' : 'mixed'
    const hits = s.active.some(m => inside.includes(m)) ? found(s, id) : 0
    const swatch = spec.source === 'group' ? '▾' : CHANNEL_ICON[spec.channel] || '●'
    const why = s.unavailable?.[id] ? `<small class="ov-note">${esc(s.unavailable[id])}</small>` : ''
    const kids = Object.keys(s.defs).filter(k => s.defs[k].parent === id).map(k => row(s, k, depth + 1)).join('')
    return `<label class="ov-row" style="--depth:${depth}" title="${esc(spec.description || spec.label)}">
      <input type="checkbox" data-ov-toggle="${esc(id)}" ${state === 'on' ? 'checked' : ''} ${state === 'mixed' ? 'data-mixed="1"' : ''}>
      <span class="ov-swatch" style="--c:${colorOf(spec)}">${swatch}</span><span class="ov-label">${esc(spec.label)}</span>
      ${hits ? `<span class="ov-count">${hits}</span>` : ''}${spec.source === 'custom' ? '<span class="ov-tag">custom</span>' : ''}</label>${why}${kids}`
  }

  /**
   * Input: none
   * Output: string
   * Processing: The Overlays side-bar tab: a role picker and the tree of overlays with on, off and mixed switches.
   * Changed by: Claude Code
   * Date: 2026-11-22
   */
  function panelHTML() {
    const s = overlayState()
    if (!s) return '<div class="side-section-title">OVERLAYS</div><div class="empty-state">Overlays need the whiteboard server.</div>'
    const roles = Object.entries(s.roles || {}).map(([id, r]) => `<option value="${esc(id)}" ${s.view?.base_role === id ? 'selected' : ''}>${esc(r.label || id)}</option>`).join('')
    const top = Object.keys(s.defs).filter(id => !s.defs[id].parent || !s.defs[s.defs[id].parent])
    queueMicrotask(() => document.querySelectorAll('[data-ov-toggle][data-mixed]').forEach(x => { x.indeterminate = true }))
    return `<div class="side-section-title">OVERLAYS</div>
      <label class="ov-role">Role <select data-ov-role aria-label="Role">${roles}</select></label>
      <div class="ov-tree">${top.map(id => row(s, id, 0)).join('')}</div>
      <p class="ov-hint">Overlays add information on top of the board. They are worked out from the code, so switching them uses no AI.</p>`
  }

  /**
   * Input: command (string), message (string)
   * Output: void
   * Processing: Run an overlay or role command and redraw, with a plain message instead of the raw result.
   * Changed by: Claude Code
   * Date: 2026-11-22
   */
  async function run(command, message) {
    try {
      await wb.request('command', { method: 'POST', body: JSON.stringify({ command }) })
      await wb.refresh()
      wb.toast(message)
    } catch (e) { wb.toast(e.message, true) }
  }

  document.addEventListener('change', e => {
    const t = e.target
    if (t.matches?.('[data-ov-toggle]')) {
      const id = t.dataset.ovToggle, label = overlayState()?.defs?.[id]?.label || id
      run(`/overlay ${t.checked ? 'on' : 'off'} ${id}`, `${label} ${t.checked ? 'on' : 'off'}`)
    } else if (t.matches?.('[data-ov-role]')) {
      run(`/role use ${t.value}`, `Showing the ${t.selectedOptions[0]?.textContent || t.value} overlays`)
    }
  })

  // A top-bar button opens the same panel as a drop-down, so overlays are reachable with the side bar closed.
  const button = document.createElement('button')
  button.className = 'ov-open'
  button.type = 'button'
  button.title = 'Overlays: security, endpoints, tests and more on top of the board'
  button.innerHTML = '◫ Overlays'
  const pop = document.createElement('div')
  pop.className = 'ov-pop'
  pop.hidden = true
  document.querySelector('.top-right')?.prepend(button)
  document.body.append(pop)

  /**
   * Input: none
   * Output: void
   * Processing: Colour arrows from rule overlays, redraw the drop-down panel when it is open, and show how many overlays are on on the button.
   * Changed by: Claude Code
   * Date: 2026-11-25
   */
  function afterRender() {
    paintArrows()
    const s = overlayState()
    button.innerHTML = `◫ Overlays${s ? `<span class="ov-open-count">${s.active.length}</span>` : ''}`
    if (!pop.hidden) pop.innerHTML = panelHTML()
  }

  button.addEventListener('click', () => {
    pop.hidden = !pop.hidden
    if (!pop.hidden) {
      const r = button.getBoundingClientRect()
      pop.style.top = `${r.bottom + 6}px`
      pop.style.right = `${Math.max(8, window.innerWidth - r.right)}px`
      pop.innerHTML = panelHTML()
    }
  })
  document.addEventListener('pointerdown', e => {
    if (!pop.hidden && !pop.contains(e.target) && !button.contains(e.target)) pop.hidden = true
  })
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !pop.hidden) pop.hidden = true })

  window.PlovaneOverlays = { isOn, decorate, panelHTML, afterRender, paintArrows, arrowLook }
})()
