/*
Input: The board reply (block text styles, short labels, and the person's readability targets and theme neutrals)
Output: Readable labels on cards and tiles, and a report of what is still hard to read (/readable report)
Processing: After every render, applies each block's text scale, weight, wrap, colour and short label; where the colour
  is automatic, picks a neutral that keeps WCAG contrast on whatever is actually behind the text in this theme; then
  measures every label on screen (size, contrast, cut off) and tells the server, which /readable check and fix use.
  It also keeps the server told which theme colour and mode the person uses, so text is neutralised for it.
Changed by: Claude Code
Date: 2026-11-26
*/

(() => {
  const wb = window.PlovaneWhiteboard
  if (!wb) return
  const CONTRAST = { AA: 4.5, AAA: 7 }
  const REPORT_DELAY = 1500
  let reportTimer = null, pending = [], lastReport = '', lastTheme = ''

  const style = document.createElement('style')
  style.textContent = `
    .rd-text .nest-name, .rd-text > b { font-size: calc(1em * var(--rd-scale, 1)); }
    .rd-bold .nest-name, .rd-bold > b { font-weight: 800; }
    .rd-wrap .nest-name, .rd-wrap > b { white-space: normal; display: -webkit-box; -webkit-box-orient: vertical;
      -webkit-line-clamp: var(--rd-lines, 2); overflow: hidden; }`
  document.head.append(style)

  /**
   * Input: text (string, a CSS colour)
   * Output: number[] | null
   * Processing: [r, g, b, a] (0-255, alpha 0-1) from rgb()/rgba() or #RRGGBB; null when it cannot be read.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function parse(text) {
    const s = String(text || '').trim()
    let m = s.match(/^#([0-9a-f]{6})$/i)
    if (m) return [0, 2, 4].map(i => parseInt(m[1].slice(i, i + 2), 16)).concat(1)
    m = s.match(/rgba?\(([^)]+)\)/)
    if (!m) return null
    const p = m[1].split(/[\s,/]+/).filter(Boolean).map(Number)
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]
  }

  /**
   * Input: c (number[])
   * Output: number
   * Processing: WCAG relative luminance.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function luminance(c) {
    const [r, g, b] = c.slice(0, 3).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 })
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
  }

  const ratio = (a, b) => { const [h, l] = [luminance(a), luminance(b)].sort((x, y) => y - x); return (h + 0.05) / (l + 0.05) }

  /**
   * Input: el (Element)
   * Output: number[]
   * Processing: The colour actually behind an element: the nearest ancestor with a solid background, else the page.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function behind(el) {
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor)
      if (c && c[3] > 0.5) return c
    }
    return parse(wb.board?.readable?.appearance?.page) || [255, 255, 255, 1]
  }

  /**
   * Input: bg (number[]), target (number)
   * Output: string
   * Processing: The theme's neutral that reads best on this background: its ink on light, its inverse on dark,
   *   falling back to pure black or white when neither reaches the target.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function neutralFor(bg, target) {
    const a = wb.board?.readable?.appearance || {}
    const light = luminance(bg) > 0.4
    const order = light ? [a.text, a.muted, '#000000'] : [a.inverse, '#FFFFFF']
    for (const c of order) { const p = parse(c); if (p && ratio(p, bg) >= target) return c }
    return light ? '#000000' : '#FFFFFF'
  }

  /**
   * Input: el (Element), b (object)
   * Output: Element | null
   * Processing: The element holding a block's name in a card (.nest-name) or a tile (its <b>).
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  const nameOf = el => el.querySelector(':scope > header .nest-name') || el.querySelector(':scope > b')

  /**
   * Input: none
   * Output: void
   * Processing: Apply every block's text settings, auto-colour for contrast, measure what is still hard to read and report it.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function apply() {
    const blocks = wb.blocks?.() || {}, settings = wb.board?.readable || { min_size: 12, contrast: 'AA' }
    const target = CONTRAST[settings.contrast] || 4.5, view = document.getElementById('canvas-viewport')?.getBoundingClientRect()
    const issues = []
    document.querySelectorAll('[data-block],[data-tile]').forEach(el => {
      const id = el.dataset.block || el.dataset.tile, b = blocks[id], name = nameOf(el)
      if (!b || !name) return
      const st = b.style || {}
      el.classList.toggle('rd-text', !!st.text_scale)
      el.style.setProperty('--rd-scale', st.text_scale || 1)
      el.classList.toggle('rd-bold', st.text_weight === 'bold')
      el.classList.toggle('rd-wrap', !!st.text_wrap)
      el.style.setProperty('--rd-lines', st.text_wrap || 2)
      const short = b.meta?.short_name
      if (short && b.name && name.textContent.includes(b.name)) {
        name.textContent = name.textContent.replace(b.name, short)
        name.title = b.name
      }
      name.style.color = ''
      const bg = behind(name)
      if (st.text_color) name.style.color = st.text_color
      else if (ratio(parse(getComputedStyle(name).color) || [0, 0, 0, 1], bg) < target) name.style.color = neutralFor(bg, target)
      const r = name.getBoundingClientRect()
      if (!view || !r.width || r.right < view.left || r.left > view.right || r.bottom < view.top || r.top > view.bottom) return
      const scale = name.offsetWidth ? r.width / name.offsetWidth : 1
      const px = parseFloat(getComputedStyle(name).fontSize) * scale
      if (px < settings.min_size) issues.push({ block: id, kind: 'small', px: Math.round(px * 10) / 10 })
      const shown = ratio(parse(getComputedStyle(name).color) || [0, 0, 0, 1], bg)
      if (shown < target) issues.push({ block: id, kind: 'contrast', ratio: Math.round(shown * 100) / 100 })
      const wrapped = !!st.text_wrap
      if (name.scrollWidth > name.clientWidth + 1 || (wrapped && name.scrollHeight > name.clientHeight + 2)) {
        issues.push({ block: id, kind: 'overflow', overflow: Math.round((name.scrollWidth / Math.max(name.clientWidth, 1)) * 100) / 100 })
      }
    })
    // The board redraws often (live updates), so send the latest measurement at most once per delay rather
    // than waiting for a quiet moment that may never come.
    pending = issues
    if (!reportTimer) reportTimer = setTimeout(() => { reportTimer = null; report(pending) }, REPORT_DELAY)
    syncTheme()
  }

  /**
   * Input: issues (object[])
   * Output: void
   * Processing: Send the measurement to the server when it changed, without redrawing the board.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function report(issues) {
    const text = JSON.stringify({ issues })
    if (text === lastReport || !wb.board?.readable) return
    lastReport = text
    wb.request('command', { method: 'POST', body: JSON.stringify({ command: `/readable report --data ${wb.quoteArg(text)}` }) })
      .catch(() => { lastReport = '' })
  }

  /**
   * Input: none
   * Output: void
   * Processing: Tell the server the person's theme colour and mode when they change, then redraw with the new neutrals.
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function syncTheme() {
    const now = wb.board?.readable?.appearance
    if (!now) return
    const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim().toUpperCase()
    const mode = document.body.dataset.theme === 'dark' ? 'dark' : 'light'
    const key = `${accent}|${mode}`
    if (!/^#[0-9A-F]{6}$/.test(accent) || key === lastTheme) return
    lastTheme = key
    if (now.accent === accent && now.mode === mode) return
    wb.request('command', { method: 'POST', body: JSON.stringify({ command: `/theme accent ${accent} --mode ${mode}` }) })
      .then(() => wb.refresh()).catch(() => { lastTheme = '' })
  }

  // Theme switches happen outside a board render, so watch for them directly.
  new MutationObserver(() => syncTheme()).observe(document.body, { attributes: true, attributeFilter: ['data-theme'] })
  new MutationObserver(() => syncTheme()).observe(document.documentElement, { attributes: true, attributeFilter: ['style'] })

  /**
   * Input: none
   * Output: void
   * Processing: Apply now and again once the browser has laid the cards out (a first render can measure zero sizes).
   * Changed by: Claude Code
   * Date: 2026-11-26
   */
  function applySettled() {
    apply()
    requestAnimationFrame(() => requestAnimationFrame(apply))
  }

  // Zooming changes how big text is on screen without a board render, so measure again every few seconds.
  setInterval(() => { if (document.visibilityState === 'visible') apply() }, 3000)

  window.PlovaneReadable = { apply: applySettled, contrast: ratio, neutralFor }
  applySettled()
})()
