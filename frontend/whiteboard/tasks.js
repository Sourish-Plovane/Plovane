// Temporary display: long-running tasks (a GPU training run, the whiteboard AI
// building the board, a test run) and how long each has left. They are drawn
// on the same canvas as the project, in their own area to its right. A chip in
// the corner takes you there. When notifications are on, every update moves the
// camera to the place it is about: the block it names, or the task's own card.
(() => {
  const NOTIFY_KEY = 'plovane.notify'
  const FLY_GAP_MS = 8000
  const SHOW_FINISHED = 4
  const st = { tasks: new Map(), seq: null, lastFly: 0, highlight: null }
  const $ = s => document.querySelector(s)
  const wb = () => window.PlovaneWhiteboard

  /**
   * Input: text (any)
   * Output: string
   * Processing: HTML-escape text for the task cards.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function esc(text) {
    return String(text ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  }

  /**
   * Input: none
   * Output: boolean
   * Processing: Whether updates may move the camera (on unless the person turned it off).
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function notifyOn() { return localStorage.getItem(NOTIFY_KEY) !== 'off' }

  /**
   * Input: seconds (number|null)
   * Output: string
   * Processing: A short duration: "45 s", "12 min", "1 h 20 min".
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function human(seconds) {
    if (seconds == null) return 'unknown'
    const s = Math.max(0, Math.round(seconds))
    if (s < 60) return `${s} s`
    if (s < 3600) return `${Math.round(s / 60)} min`
    const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60)
    return m ? `${h} h ${m} min` : `${h} h`
  }

  /**
   * Input: task (object)
   * Output: object
   * Processing: Time left and finish time now, counted down from the ETA the server gave.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  function timing(task) {
    if (task.status !== 'running') return { left: task.status, when: '' }
    if (task.waiting) return { left: 'in line', when: 'starts when the jobs ahead finish' }
    if (task.overdue) return { left: 'taking longer than expected', when: '' }
    if (!task.eta) return { left: 'no estimate yet', when: '' }
    const eta = new Date(task.eta), left = (eta - Date.now()) / 1000
    return left <= 0 ? { left: 'almost done', when: '' }
      : { left: `about ${human(left)} left`, when: `done around ${eta.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` }
  }

  /**
   * Input: none
   * Output: object
   * Processing: The size of the board drawn now, in canvas units (the connections layer matches it).
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function frame() {
    const svg = $('#connections')
    return { w: +svg?.getAttribute('width') || 1600, h: +svg?.getAttribute('height') || 1000 }
  }

  /**
   * Input: none
   * Output: HTMLElement|null
   * Processing: The Tasks area on the canvas, made once and kept beside the board.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function zone() {
    const world = $('#canvas-world')
    if (!world) return null
    let z = $('#task-zone')
    if (!z) {
      z = document.createElement('section')
      z.id = 'task-zone'
      z.className = 'task-zone'
      z.setAttribute('aria-label', 'Tasks in progress')
      z.addEventListener('click', onClick)
      z.addEventListener('dblclick', e => e.stopPropagation())
    }
    if (z.parentElement !== world) world.append(z)
    return z
  }

  /**
   * Input: none
   * Output: HTMLElement|null
   * Processing: The corner chip that counts tasks and takes you to them.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function chip() {
    const view = $('#canvas-viewport')
    if (!view) return null
    let c = $('#task-chip')
    if (!c) {
      c = document.createElement('div')
      c.id = 'task-chip'
      c.className = 'task-chip'
      c.addEventListener('click', onClick)
      view.append(c)
    }
    return c
  }

  /**
   * Input: task (object)
   * Output: string
   * Processing: One task card: name, time left, progress, latest news and where it is happening.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  function card(task) {
    const t = timing(task), pct = task.progress != null ? Math.round(task.progress * 100) : null
    const place = task.at ? (wb()?.blocks()?.[task.at]?.name || task.at) : null
    const news = (task.updates || []).slice(-3).reverse()
      .map(u => `<li><time>${esc(new Date(u.t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))}</time> ${esc(u.message)}</li>`).join('')
    return `<article class="task-card ${esc(task.status)}${task.waiting ? ' waiting' : ''}${st.highlight === task.id ? ' flash' : ''}" data-task="${esc(task.id)}" title="${place ? `Go to ${esc(place)}` : 'Show this task'}">
      <header><span class="task-name">${esc(task.name)}</span><span class="task-state">${esc(task.status === 'running' ? (task.waiting ? 'waiting' : pct != null ? `${pct}%` : 'running') : task.status)}</span></header>
      <div class="task-time" data-time="${esc(task.id)}"><b>${esc(t.left)}</b>${t.when ? ` · ${esc(t.when)}` : ''}</div>
      <div class="task-bar"><i style="width:${pct ?? (task.status === 'running' ? 8 : 100)}%"></i></div>
      ${task.message ? `<p class="task-msg">${esc(task.message)}</p>` : ''}
      <ul class="task-news">${news}</ul>
      <footer><span>${esc(task.by || '')} · ${esc(task.elapsed_text || '')} ${task.waiting ? 'in line' : 'so far'}${task.basis && task.status === 'running' && !task.waiting ? ` · estimate from ${esc(task.basis)}` : ''}</span>${place ? `<button data-goto="${esc(task.at)}">Go to ${esc(place)} →</button>` : ''}</footer>
    </article>`
  }

  /**
   * Input: none
   * Output: void
   * Processing: Draw the Tasks area beside the board and the corner chip; both hide when nothing is running or recently finished.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  function render() {
    const z = zone(), c = chip()
    if (!z || !c) return
    const tasks = [...st.tasks.values()].sort((a, b) => (a.status !== 'running') - (b.status !== 'running') || !!a.waiting - !!b.waiting || b.started - a.started)
    const running = tasks.filter(t => t.status === 'running')
    z.hidden = c.hidden = !tasks.length
    if (!tasks.length) return
    const f = frame()
    Object.assign(z.style, { left: `${f.w * 1.06}px`, top: '0px', width: `${f.w * 0.3}px`, fontSize: `${f.w / 105}px` })
    // Running tasks grouped by who runs them (several agents at once); only the latest few finished ones.
    const finished = tasks.filter(t => t.status !== 'running'), owners = [...new Set(running.map(t => t.by || ''))]
    const groups = owners.map(by => `${owners.length > 1 ? `<h3 class="task-owner">${esc(by || 'unknown')} <small>${running.filter(t => (t.by || '') === by).length}</small></h3>` : ''}${running.filter(t => (t.by || '') === by).map(card).join('')}`).join('')
    const recent = finished.slice(0, SHOW_FINISHED).map(card).join('')
    const more = finished.length > SHOW_FINISHED ? `<p class="task-more">+${finished.length - SHOW_FINISHED} more finished in the last 10 min</p>` : ''
    z.innerHTML = `<h2>⏱ Tasks <small>${running.length} running</small></h2>${groups}${finished.length ? `<h3 class="task-owner">Finished</h3>${recent}${more}` : ''}`
    const next = running.map(t => t.eta ? new Date(t.eta) - Date.now() : Infinity).sort((a, b) => a - b)[0]
    c.innerHTML = `<button data-zone title="Go to the Tasks area">⏱ ${running.length ? `${running.length} running${Number.isFinite(next) ? ` · ${human(next / 1000)}` : ''}` : 'Tasks done'}</button>`
      + `<button data-notify class="${notifyOn() ? 'on' : ''}" title="${notifyOn() ? 'Updates take you to what changed. Click to stop.' : 'Updates only show a message. Click to be taken to them.'}">${notifyOn() ? '🔔' : '🔕'}</button>`
  }

  /**
   * Input: none
   * Output: void
   * Processing: Count the time left down once a second without redrawing the cards.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function tick() {
    for (const el of document.querySelectorAll('#task-zone [data-time]')) {
      const task = st.tasks.get(el.dataset.time)
      if (!task) continue
      const t = timing(task)
      el.innerHTML = `<b>${esc(t.left)}</b>${t.when ? ` · ${esc(t.when)}` : ''}`
    }
  }

  /**
   * Input: id (string|null)
   * Output: void
   * Processing: Move the camera onto the Tasks area (onto one card when given) and make the card flash.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function goToZone(id = null) {
    const z = zone(), view = $('#canvas-viewport')
    if (!z || z.hidden || !view || !wb()) return
    const target = (id && z.querySelector(`[data-task="${CSS.escape(id)}"]`)) || z
    const x = z.offsetLeft + (target === z ? 0 : target.offsetLeft), y = z.offsetTop + (target === z ? 0 : target.offsetTop)
    const w = target.offsetWidth, h = target.offsetHeight, vw = view.clientWidth, vh = view.clientHeight
    // Fit the whole thing when it is short; when many tasks make it tall, keep the text readable and start at the top.
    const fits = Math.min(vw * 0.8 / w, vh * 0.8 / h), readable = vw * 0.42 / w
    const scale = Math.max(fits, Math.min(readable, vw * 0.8 / w))
    const top = h * scale > vh * 0.85 ? 24 - y * scale : vh / 2 - (y + h / 2) * scale
    wb().setView(vw / 2 - (x + w / 2) * scale, top, scale)
    if (id) {
      st.highlight = id
      render()
      setTimeout(() => { if (st.highlight === id) { st.highlight = null; render() } }, 2400)
    }
  }

  /**
   * Input: id (string)
   * Output: void
   * Processing: Take the person to a block: open the zoom path down to it, then frame the block itself and make it flash.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function goToBlock(id) {
    const w = wb(), view = $('#canvas-viewport')
    if (!w?.blocks()?.[id]) { goToZone(); return }
    w.flyTo(id)
    // flyTo zooms into blocks that hold others; a plain block is only selected, so frame it here.
    requestAnimationFrame(() => {
      const item = w.items?.get(id)
      if (item?.rect && w.focus !== id && view) {
        const r = item.rect, vw = view.clientWidth, vh = view.clientHeight
        const scale = Math.min(vw * 0.6 / r.w, vh * 0.6 / r.h)
        w.setView(vw / 2 - (r.x + r.w / 2) * scale, vh / 2 - (r.y + r.h / 2) * scale, scale)
      }
      const el = document.querySelector(`#blocks [data-block="${CSS.escape(id)}"], #blocks [data-tile="${CSS.escape(id)}"]`)
      if (el) { el.classList.add('task-ping'); setTimeout(() => el.classList.remove('task-ping'), 2600) }
    })
  }

  /**
   * Input: event (Event)
   * Output: void
   * Processing: Clicks in the Tasks area and chip: go to a task's block or card, open the area, or switch notifications.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function onClick(event) {
    event.stopPropagation()
    const el = event.target.closest('[data-goto],[data-zone],[data-notify],[data-task]')
    if (!el) return
    if (el.dataset.goto) goToBlock(el.dataset.goto)
    else if ('zone' in el.dataset) goToZone()
    else if ('notify' in el.dataset) {
      localStorage.setItem(NOTIFY_KEY, notifyOn() ? 'off' : 'on')
      wb()?.toast(notifyOn() ? 'Task updates will take you to what changed.' : 'Task updates will only show a message.')
      render()
    } else {
      const task = st.tasks.get(el.dataset.task)
      if (task?.at) goToBlock(task.at)
      else goToZone(el.dataset.task)
    }
  }

  /**
   * Input: e (object)
   * Output: void
   * Processing: Take in one live task event: update the card, say what happened and, with notifications on, go there.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function onTask(e) {
    const task = e.task
    if (!task?.id) return
    const before = st.tasks.get(task.id)
    st.tasks.set(task.id, task)
    render()
    const t = timing(task), last = task.updates?.[task.updates.length - 1]?.message
    const moved = e.type !== 'update' || (e.go_to && e.go_to !== before?.at)
    // A bare progress tick only refreshes the card; starts, ends, messages and moves get a message.
    if (moved || !/^\d+%$/.test(last || '')) wb()?.toast(`⏱ ${task.name}: ${e.type === 'finished' ? task.status : (last || 'updated')}${task.status === 'running' ? ` · ${t.left}` : ''}`, task.status === 'failed')
    // Never pull the camera away while the person is typing, and not more than once every few seconds.
    const typing = document.activeElement?.matches?.('input, textarea, [contenteditable="true"]')
    // Move only for a change of place: a task starting or ending, or moving to another block.
    // Progress alone only shows the message, so many agents at once do not keep pulling the camera.
    if (!notifyOn() || typing || !moved || Date.now() - st.lastFly < FLY_GAP_MS) return
    st.lastFly = Date.now()
    if (e.go_to && wb()?.blocks()?.[e.go_to]) goToBlock(e.go_to)
    else goToZone(task.id)
  }

  /**
   * Input: none
   * Output: Promise<void>
   * Processing: Load the tasks already running, then follow new task events every few seconds.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  async function poll() {
    if (!wb()?.request) return
    try {
      if (st.seq == null) {
        const recent = await wb().request('events/recent?after=999999999')
        st.seq = recent.seq || 0
        const listed = await wb().request('command', { method: 'POST', body: JSON.stringify({ command: '/task list' }) })
        for (const task of listed?.result?.tasks || []) st.tasks.set(task.id, task)
        render()
      } else if (!document.hidden) {
        const recent = await wb().request(`events/recent?after=${st.seq}`)
        st.seq = recent.seq ?? st.seq
        for (const e of recent.events || []) if (e.kind === 'task') onTask(e)
      }
    } catch (_) { /* the server may be restarting; the next poll tries again */ }
    // Finished tasks leave the display after a while; drop them here too.
    for (const [id, task] of st.tasks) if (task.status !== 'running' && task.finished && Date.now() / 1000 - task.finished > 600) st.tasks.delete(id)
  }

  /**
   * Input: none
   * Output: void
   * Processing: Keep the Tasks area beside the board whenever the board is redrawn or zoomed into.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function follow() {
    const blocks = $('#blocks')
    if (!blocks) { setTimeout(follow, 300); return }
    let queued = false
    new MutationObserver(() => {
      if (queued) return
      queued = true
      requestAnimationFrame(() => { queued = false; if (!$('#task-zone') || $('#task-zone').parentElement !== $('#canvas-world')) render(); else if (st.tasks.size) render() })
    }).observe(blocks, { childList: true })
  }

  follow()
  poll()
  setInterval(poll, 3000)
  setInterval(tick, 1000)
  window.PlovaneTasks = { goToZone, goToBlock, render, get tasks() { return [...st.tasks.values()] } }
})()
