// Sections and arrow routing for the whiteboard (whiteboard.js calls in here).
//
// The top of a project is split into separate sections instead of one grid in one frame:
//   - "How it fits together": the services, databases and outside systems, in tiers that follow the
//     arrows (who calls whom reads left to right);
//   - one section per big top-level folder (Backend, Frontend, ...), showing what is inside it as cards;
//   - "More": small folders and loose files.
// Arrows run in the gaps between cards, never under or over one: an orthogonal route on a grid of
// lines through the gaps, found with A*, with parallel arrows spread into lanes and a label on each.
(() => {
  const ARCH = new Set(['service', 'server', 'database', 'queue', 'external', 'group', 'note'])
  const OWN_SECTION = 6 // a top-level folder with at least this many files under it gets its own section
  const SPREAD_TO = 5   // a section with fewer cards opens its biggest folder card into the cards inside it
  const MAX_CARDS = 12  // ...but never into more cards than this

  // ---------------------------------------------------------------------------- layout -----

  /**
   * Input: blocks (object[]), edges (Array<[string, string]>)
   * Output: object[][]
   * Processing: Columns for the architecture: each block one column right of what points at it (longest path), rows ordered to cut crossings.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  function tiers(blocks, edges) {
    const ids = new Set(blocks.map(b => b.id)), rank = Object.fromEntries(blocks.map(b => [b.id, 0]))
    const inner = edges.filter(([s, t]) => ids.has(s) && ids.has(t) && s !== t)
    for (let i = 0; i < blocks.length; i++) {
      let changed = false
      for (const [s, t] of inner) if (rank[t] < rank[s] + 1 && rank[s] + 1 < blocks.length) { rank[t] = rank[s] + 1; changed = true }
      if (!changed) break
    }
    // Outside systems nothing points out of sit with what calls them, not in a far column of their own.
    const cols = []
    blocks.forEach(b => { (cols[rank[b.id]] ||= []).push(b) })
    const dense = cols.filter(Boolean), row = {}
    dense.forEach((col, c) => {
      const weight = b => { const feed = inner.filter(([, t]) => t === b.id).map(([s]) => row[s]).filter(v => v != null); return feed.length ? feed.reduce((a, v) => a + v, 0) / feed.length : col.indexOf(b) }
      if (c) col.sort((a, b) => weight(a) - weight(b))
      col.forEach((b, i) => { row[b.id] = i })
    })
    return dense
  }

  /**
   * Input: n (number), ratio (number)
   * Output: number
   * Processing: How many columns lay n cards out closest to the wanted width-to-height ratio.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  const columnsFor = (n, ratio) => Math.max(1, Math.min(n, Math.round(Math.sqrt(n * ratio))))

  /**
   * Input: kids (object[]), frame (object), inner (object), h (object: whiteboard helpers), edges (Array<[string, string]>)
   * Output: object
   * Processing: The top of the project as sections: the architecture across the top, then the folder sections in as many columns (1-4) as makes the whole closest to the screen's shape, so cards stay readable; returns the HTML, each card's rect, the section headers (arrows go around them) and the height used.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  function layout(kids, frame, inner, h, edges) {
    const pad = frame.w * 0.022, gap = frame.w * 0.028, headH = frame.w * 0.034, W = frame.w - 2 * pad
    const cardW = frame.w * 0.125, cardH = frame.w * 0.058 // a card's name stays readable at the overview zoom
    const files = b => { const kids = h.childrenOf(b.id); return kids.length ? kids.reduce((n, k) => n + files(k), 0) : 1 }
    const arch = kids.filter(b => ARCH.has(b.kind))
    const folders = kids.filter(b => b.kind === 'folder' && files(b) >= OWN_SECTION)
    const rest = kids.filter(b => !arch.includes(b) && !folders.includes(b))
    // Spread a folder out: while it shows few cards, open its biggest folder card into what is inside (up to MAX_CARDS).
    const spread = f => {
      let cards = h.childrenOf(f.id)
      for (let guard = 0; cards.length < SPREAD_TO && guard < 4; guard++) {
        const big = cards.filter(c => c.kind === 'folder' && h.childrenOf(c.id).length).sort((a, b) => files(b) - files(a))[0]
        if (!big || cards.length - 1 + h.childrenOf(big.id).length > MAX_CARDS) break
        const at = cards.indexOf(big)
        cards = [...cards.slice(0, at), ...h.childrenOf(big.id), ...cards.slice(at + 1)]
      }
      return cards
    }
    const archSection = arch.length ? { id: 'section-architecture', title: 'How it fits together', sub: 'The main parts and what talks to what', cards: arch, arch: true } : null
    const code = folders.map(f => ({ id: `section-${f.id}`, title: f.name, sub: h.summaryOf(f) || f.path || '', cards: spread(f), folder: f }))
    if (rest.length) code.push({ id: 'section-more', title: folders.length ? 'More' : 'Files', sub: 'Smaller folders and files', cards: rest })

    const sections = [...(archSection ? [archSection] : []), ...code]
    const cg = gap * 1.1
    // Input: s (object)
    // Output: number
    // Processing: The width a section would like: enough columns for its cards to come out a little wider than tall overall.
    const natural = s => {
      if (s.arch) { const n = tiers(s.cards, edges).length; return n * cardW * 1.05 + (n - 1) * cardW * 0.7 + 2 * pad }
      const cols = Math.max(1, Math.min(s.cards.length, Math.ceil(Math.sqrt(s.cards.length * 2.2))))
      return cols * cardW + (cols - 1) * cg + 2 * pad
    }
    // Input: s (object), width (number)
    // Output: object (where each card goes inside the section, relative to its top-left, and the height)
    // Processing: Lay a section's cards out at a width: architecture in tiers, folders in a grid.
    const arrange = (s, width) => {
      const body = width - 2 * pad, top = headH + pad * 0.6, spots = []
      if (s.arch) {
        const cols = tiers(s.cards, edges), n = cols.length, rows = Math.max(...cols.map(c => c.length))
        const cw = Math.min(cardW * 1.05, body / (n + (n - 1) * 0.45)), colGap = n > 1 ? Math.min(cw * 0.9, (body - n * cw) / (n - 1)) : 0
        const ch = cardH, rowGap = cg * 0.8, left = pad + (body - (n * cw + (n - 1) * colGap)) / 2
        cols.forEach((col, c) => col.forEach((b, r) => spots.push({ b, x: left + c * (cw + colGap), y: top + (rows - col.length) * (ch + rowGap) / 2 + r * (ch + rowGap), w: cw, h: ch })))
        return { spots, height: top + rows * ch + (rows - 1) * rowGap + pad }
      }
      const cols = Math.max(1, Math.min(s.cards.length, Math.floor((body + cg) / (cardW * 0.8 + cg))))
      const cw = (body - cg * (cols - 1)) / cols, rows = Math.ceil(s.cards.length / cols)
      s.cards.forEach((b, j) => spots.push({ b, x: pad + (j % cols) * (cw + cg), y: top + Math.floor(j / cols) * (cardH + cg), w: cw, h: cardH }))
      return { spots, height: top + rows * cardH + (rows - 1) * cg + pad }
    }
    const view = document.getElementById('canvas-viewport')
    const aspect = Math.max(1.2, Math.min(3, ((view?.clientWidth || 1400) - 80) / Math.max(200, (view?.clientHeight || 700) - 120)))
    const y0 = inner.y + pad * 0.4, between = gap * 1.2
    // Every way to cut the sections (in order) into rows; each row shares the width by what its sections would like.
    let plan = null
    const n = sections.length
    for (let mask = 0; mask < (1 << Math.max(0, n - 1)); mask++) {
      const rows = [[]]
      sections.forEach((sec, i) => { rows[rows.length - 1].push(sec); if (i < n - 1 && mask & (1 << i)) rows.push([]) })
      let total = y0
      const laid = rows.map(row => {
        const want = row.map(natural), room = W - between * (row.length - 1), k = room / want.reduce((a, v) => a + v, 0)
        const widths = want.map(v => v * k), out = row.map((sec, i) => ({ s: sec, width: widths[i], ...arrange(sec, widths[i]) }))
        const height = Math.max(...out.map(o => o.height))
        out.forEach(o => { o.height = height })
        total += height + between
        return out
      })
      // Cards squeezed too narrow to read rule a plan out (architecture names are longer, so they need more room).
      const crowd = laid.flat().some(o => o.spots.some(sp => sp.w < cardW * (o.s.arch ? 0.95 : 0.75))) ? 1 : 0
      const score = Math.abs(Math.log((frame.w / total) / aspect)) + crowd * 10
      if (!plan || score < plan.score) plan = { laid, score }
    }

    let y = y0, html = ''
    const cards = [], headers = [], frames = []
    for (const row of plan ? plan.laid : []) {
      let x = pad
      for (const o of row) {
        o.spots.forEach(sp => {
          const rect = { x: x + sp.x, y: y + sp.y, w: sp.w, h: sp.h }
          cards.push({ id: sp.b.id, rect })
          html += o.s.arch ? archCard(sp.b, rect, h) : sectionCard(sp.b, rect, h, o.s.folder)
        })
        headers.push({ x, y, w: o.width, h: headH })
        frames.push({ s: o.s, x, top: y, width: o.width, height: o.height })
        x += o.width + between
      }
      y += row[0].height + between
    }
    const framesHTML = frames.map(({ s, x, top, width, height }) => {
      const go = s.folder ? ` data-section-go="${h.esc(s.folder.id)}" title="Zoom into ${h.esc(s.title)}"` : ''
      return `<section class="wb-section ${s.arch ? 'arch' : ''}" data-section="${h.esc(s.id)}" style="left:${x}px;top:${top}px;width:${width}px;height:${height}px">
        <header style="height:${headH}px;font-size:${headH * 0.5}px"${go}><b>${s.arch ? '◇' : s.folder ? '▰' : '▤'} ${h.esc(s.title)}</b><small>${h.esc(s.sub)}${s.arch ? '' : ` · ${s.cards.length} shown · point at a card to see what it uses`}</small></header></section>`
    }).join('')
    return { html: framesHTML + html, cards, headers, height: y + pad * 0.2 }
  }

  /**
   * Input: b (object), r (object), h (object)
   * Output: string
   * Processing: A card for one part of the architecture: icon, name, kind and its one-line summary, in type big enough to read at the top level.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  function archCard(b, r, h) {
    const o = h.overlayOf(b), sub = h.summaryOf(b), kind = { external: 'outside system', server: 'server', service: 'service', database: 'database', queue: 'queue' }[b.kind] || b.kind
    return `<article class="nest-card ${h.esc(b.kind)}-card arch-card${o.cls}" data-block="${h.esc(b.id)}" title="${h.esc(o.tip || sub)}" style="${o.style}--w:${r.w};left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px;font-size:${Math.min(r.h * 0.24, r.w * 0.09)}px">
      <header><span class="nest-icon">${h.iconOf(b)}</span><span class="nest-name">${h.esc(b.name || b.id)}</span>${h.badgeOf(b)}${o.html}</header>
      <small>${h.esc(kind)}${sub && sub !== b.kind ? ` · ${h.esc(sub)}` : ''}</small></article>`
  }

  /**
   * Input: b (object), r (object), h (object), folder (object | undefined)
   * Output: string
   * Processing: A card for a folder or file in a section: a big name (with the folder it sits in when it was opened out), its one-line summary, and for a folder the first few things inside it.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  function sectionCard(b, r, h, folder) {
    const o = h.overlayOf(b), kids = h.childrenOf(b.id), sub = h.summaryOf(b)
    const path = folder && b.path && folder.path && b.path.startsWith(folder.path + '/') ? b.path.slice(folder.path.length + 1) : ''
    const where = path.includes('/') ? path.slice(0, path.lastIndexOf('/') + 1) : ''
    const inside = kids.length ? `<div class="sc-kids">${kids.slice(0, 6).map(k => `<span>${h.iconOf(k)} ${h.esc(k.name)}</span>`).join('')}${kids.length > 6 ? `<span class="more">+${kids.length - 6}</span>` : ''}</div>` : ''
    return `<article class="nest-card ${h.esc(b.kind)}-card section-card${o.cls}" data-block="${h.esc(b.id)}" title="${h.esc(o.tip || sub || b.path || '')}" style="${o.style}--w:${r.w};left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px;font-size:${Math.min(r.h * 0.2, r.w * 0.085)}px">
      <header><span class="nest-icon">${h.iconOf(b)}</span><span class="nest-name">${where ? `<i>${h.esc(where)}</i>` : ''}${h.esc(b.name || b.id)}</span>${h.badgeOf(b)}${o.html}</header>
      <small>${kids.length ? `${kids.length} inside` : h.esc(b.meta?.language || b.kind)}${sub && sub !== b.path ? ` · ${h.esc(sub)}` : ''}</small>${inside}</article>`
  }

  // ----------------------------------------------------------------------------- routing -----

  /**
   * Input: none
   * Output: object
   * Processing: A small binary min-heap of [priority, value] pairs for A*.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  function heap() {
    const a = []
    return {
      get size() { return a.length },
      push(p, v) { a.push([p, v]); let i = a.length - 1; while (i) { const j = (i - 1) >> 1; if (a[j][0] <= a[i][0]) break; [a[i], a[j]] = [a[j], a[i]]; i = j } },
      pop() {
        const top = a[0], last = a.pop()
        if (a.length) { a[0] = last; let i = 0; for (;;) { const l = 2 * i + 1, r = l + 1; let m = i; if (l < a.length && a[l][0] < a[m][0]) m = l; if (r < a.length && a[r][0] < a[m][0]) m = r; if (m === i) break; [a[i], a[m]] = [a[m], a[i]]; i = m } }
        return top[1]
      },
    }
  }

  /**
   * Input: obstacles (object[]), frame (object)
   * Output: number
   * Processing: Half the narrowest gap between boxes that face each other: how far from a box arrows run.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  function marginOf(obstacles, frame) {
    let best = frame.w * 0.03
    for (let i = 0; i < obstacles.length; i++) for (let j = i + 1; j < obstacles.length; j++) {
      const A = obstacles[i], B = obstacles[j]
      const ox = Math.min(A.x + A.w, B.x + B.w) - Math.max(A.x, B.x), oy = Math.min(A.y + A.h, B.y + B.h) - Math.max(A.y, B.y)
      if (oy > 0 && ox < 0) best = Math.min(best, -ox)
      if (ox > 0 && oy < 0) best = Math.min(best, -oy)
    }
    return Math.max(frame.w * 0.004, best / 2)
  }

  /**
   * Input: edges (object[]: {s, t, cls, tag, label}), rectOf (function), frame (object), extra (object[]: more boxes to avoid)
   * Output: string
   * Processing: SVG for every arrow, routed through the gaps: A* over a grid of lines beside the boxes (bends cost extra, a gap already used costs a little), then parallel arrows spread into lanes, rounded corners and a haloed label on the longest straight run.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  function arrows(edges, rectOf, frame, extra = []) {
    if (!edges.length) return ''
    const ids = [...new Set(edges.flatMap(e => [e.s, e.t]))]
    const boxes = new Map(ids.map(id => [id, rectOf(id)]))
    const obstacles = [...new Map([...boxes, ...(window.PlovaneWhiteboard ? [...window.PlovaneWhiteboard.items].filter(([, it]) => it.level === 1).map(([id, it]) => [id, it.rect]) : [])]).values(), ...extra]
    const m = marginOf(obstacles, frame), block = m * 0.9
    const uniq = list => [...new Set(list.map(v => Math.round(v * 10) / 10))].sort((a, b) => a - b)
    const xs = uniq([...obstacles.flatMap(r => [r.x - m, r.x + r.w + m]), ...[...boxes.values()].map(r => r.x + r.w / 2), m, frame.w - m])
    const ys = uniq([...obstacles.flatMap(r => [r.y - m, r.y + r.h + m]), ...[...boxes.values()].map(r => r.y + r.h / 2), m, frame.h - m])
    const X = xs.length, Y = ys.length, at = (i, j) => i * Y + j
    const inside = (x, y) => obstacles.some(r => x > r.x - block && x < r.x + r.w + block && y > r.y - block && y < r.y + r.h + block)
    const free = new Uint8Array(X * Y)
    for (let i = 0; i < X; i++) for (let j = 0; j < Y; j++) free[at(i, j)] = inside(xs[i], ys[j]) ? 0 : 1
    // Edge from (i,j) to (i+1,j) / (i,j+1) is open when its middle is clear too (lines are dense, so that is enough).
    const hOpen = new Uint8Array(X * Y), vOpen = new Uint8Array(X * Y)
    for (let i = 0; i < X; i++) for (let j = 0; j < Y; j++) {
      if (i + 1 < X && free[at(i, j)] && free[at(i + 1, j)] && !inside((xs[i] + xs[i + 1]) / 2, ys[j])) hOpen[at(i, j)] = 1
      if (j + 1 < Y && free[at(i, j)] && free[at(i, j + 1)] && !inside(xs[i], (ys[j] + ys[j + 1]) / 2)) vOpen[at(i, j)] = 1
    }
    const ix = v => xs.indexOf(Math.round(v * 10) / 10), iy = v => ys.indexOf(Math.round(v * 10) / 10)
    // The four doors of a box: the side's middle, and the free point just outside it.
    const doors = r => [
      { side: 0, port: [r.x + r.w, r.y + r.h / 2], node: [ix(r.x + r.w + m), iy(r.y + r.h / 2)], dir: 0 },
      { side: 1, port: [r.x + r.w / 2, r.y + r.h], node: [ix(r.x + r.w / 2), iy(r.y + r.h + m)], dir: 1 },
      { side: 2, port: [r.x, r.y + r.h / 2], node: [ix(r.x - m), iy(r.y + r.h / 2)], dir: 2 },
      { side: 3, port: [r.x + r.w / 2, r.y], node: [ix(r.x + r.w / 2), iy(r.y - m)], dir: 3 },
    ].filter(d => d.node[0] >= 0 && d.node[1] >= 0 && free[at(d.node[0], d.node[1])])
    const used = new Map(), STEP = [[1, 0], [0, 1], [-1, 0], [0, -1]], bend = m * 4
    const segKey = (a, b) => a < b ? `${a}-${b}` : `${b}-${a}`
    const routed = []
    // On the sectioned top level, import arrows wait until you point at a card (they would crowd the overview).
    const quiet = e => extra.length > 0 && /\bimport\b/.test(e.cls)
    // Shown arrows first and shortest first, so long arrows go around what is already drawn.
    const order = edges.map((e, k) => ({ e, k, A: boxes.get(e.s), B: boxes.get(e.t) }))
      .sort((p, q) => quiet(p.e) - quiet(q.e) || Math.hypot(p.A.x - p.B.x, p.A.y - p.B.y) - Math.hypot(q.A.x - q.B.x, q.A.y - q.B.y))
    for (const { e, A, B } of order) {
      const starts = doors(A), ends = doors(B)
      if (!starts.length || !ends.length) continue
      const goal = new Map(ends.map(d => [at(...d.node), d]))
      const best = new Map(), from = new Map(), open = heap()
      const tx = B.x + B.w / 2, ty = B.y + B.h / 2
      const h = (i, j) => Math.abs(xs[i] - tx) + Math.abs(ys[j] - ty)
      for (const d of starts) { const k = at(...d.node) * 4 + d.dir, g = m; best.set(k, g); from.set(k, { door: d }); open.push(g + h(...d.node), k) }
      let found = null, guard = 0
      while (open.size && guard++ < 60000) {
        const k = open.pop(), node = k >> 2, dir = k & 3, g = best.get(k), i = Math.floor(node / Y), j = node % Y
        if (goal.has(node)) { found = { k, door: goal.get(node) }; break }
        for (let nd = 0; nd < 4; nd++) {
          if (nd === (dir + 2) % 4) continue
          const [di, dj] = STEP[nd], ni = i + di, nj = j + dj
          if (ni < 0 || nj < 0 || ni >= X || nj >= Y) continue
          const ok = di ? hOpen[at(Math.min(i, ni), j)] : vOpen[at(i, Math.min(j, nj))]
          if (!ok) continue
          const nn = at(ni, nj), len = Math.abs(xs[ni] - xs[i]) + Math.abs(ys[nj] - ys[j])
          const g2 = g + len + (nd !== dir ? bend : 0) + (used.get(segKey(node, nn)) || 0) * m * 1.5
          const nk = nn * 4 + nd
          if (g2 < (best.get(nk) ?? Infinity)) { best.set(nk, g2); from.set(nk, { k }); open.push(g2 + h(ni, nj), nk) }
        }
      }
      if (!found) continue
      const nodes = []
      let k = found.k, first = null
      while (k != null) { const node = k >> 2; nodes.unshift(node); const f = from.get(k); if (f.door) { first = f.door; break } k = f.k }
      for (let n = 1; n < nodes.length; n++) used.set(segKey(nodes[n - 1], nodes[n]), (used.get(segKey(nodes[n - 1], nodes[n])) || 0) + 1)
      const pts = [first.port, ...nodes.map(n => [xs[Math.floor(n / Y)], ys[n % Y]]), found.door.port]
      routed.push({ e, pts: simplify(pts) })
    }
    repaintLabels()
    spread(routed, Math.min(m * 0.32, frame.w * 0.004))
    routed.forEach(r => { r.pts = simplify(r.pts.map(([x, y]) => [Math.round(x * 100) / 100, Math.round(y * 100) / 100])) })
    const font = frame.w / 118, r = m * 0.55
    const labels = []
    return routed.map(({ e, pts }) => {
      const d = rounded(pts, r)
      const spot = labelSpot(pts, e.label, font, labels, obstacles)
      const at = `x="${spot.x}" y="${spot.y}" text-anchor="middle" style="font-size:${font}px"`
      // A white copy behind the words keeps them readable where they cross another line.
      const text = e.label ? `<text class="wb-halo" ${at} aria-hidden="true">${escText(e.label)}</text><text ${e.tag} ${at} class="wb-label">${escText(e.label)}</text>` : ''
      return `<path class="${e.cls} routed${quiet(e) ? ' wb-quiet' : ''}" ${e.tag} d="${d}" marker-end="url(#arrowhead)"/>${text}`
    }).join('')
  }

  /**
   * Input: none
   * Output: void
   * Processing: Chrome can leave SVG text unpainted when it is written into the scaled canvas (labels exist but do not show); touching the text's paint on the next frame makes it draw.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  function repaintLabels() {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      document.querySelectorAll('#connections text.wb-label, #connections text.wb-halo').forEach(t => { t.style.fontKerning = 'none'; void t.getBBox(); t.style.fontKerning = '' })
    }))
  }
  // Labels drawn before the web font arrived are redrawn once it has.
  document.fonts?.ready?.then(() => window.PlovaneWhiteboard?.render?.())

  const escText = v => String(v ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))

  /**
   * Input: pts (number[][])
   * Output: number[][]
   * Processing: Drop points in the middle of a straight run, and repeated points.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  function simplify(pts) {
    const out = []
    for (const p of pts) {
      if (out.length && out[out.length - 1][0] === p[0] && out[out.length - 1][1] === p[1]) continue
      const same = (u, v) => Math.abs(u - v) < 0.05
      if (out.length >= 2) { const a = out[out.length - 2], b = out[out.length - 1]; if ((same(a[0], b[0]) && same(b[0], p[0])) || (same(a[1], b[1]) && same(b[1], p[1]))) out.pop() }
      out.push([p[0], p[1]])
    }
    return out
  }

  /**
   * Input: routed (object[]), lane (number)
   * Output: void
   * Processing: Arrows sharing a stretch of the same gap line are moved apart into side-by-side lanes so each can be followed.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  function spread(routed, lane) {
    const runs = new Map()
    routed.forEach((r, n) => {
      for (let i = 1; i < r.pts.length; i++) {
        const a = r.pts[i - 1], b = r.pts[i], vertical = a[0] === b[0]
        const key = vertical ? `v${a[0]}` : `h${a[1]}`, lo = Math.min(vertical ? a[1] : a[0], vertical ? b[1] : b[0]), hi = Math.max(vertical ? a[1] : a[0], vertical ? b[1] : b[0])
        ;(runs.get(key) || runs.set(key, []).get(key)).push({ n, i, lo, hi, vertical })
      }
    })
    for (const list of runs.values()) {
      if (list.length < 2) continue
      const groups = []
      list.sort((a, b) => a.lo - b.lo).forEach(s => { const g = groups.find(g => g.some(o => o.n !== s.n && o.lo < s.hi && s.lo < o.hi)); g ? g.push(s) : groups.push([s]) })
      for (const g of groups) {
        const members = [...new Set(g.map(s => s.n))]
        if (members.length < 2) continue
        members.forEach((n, idx) => {
          const off = (idx - (members.length - 1) / 2) * lane
          g.filter(s => s.n === n).forEach(s => { const p = routed[n].pts, ax = s.vertical ? 0 : 1; p[s.i - 1][ax] += off; p[s.i][ax] += off })
        })
      }
    }
  }

  /**
   * Input: pts (number[][]), r (number)
   * Output: string
   * Processing: An SVG path through the points with softly rounded corners.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  function rounded(pts, r) {
    let d = `M ${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`
    for (let i = 1; i < pts.length - 1; i++) {
      const [px, py] = pts[i - 1], [x, y] = pts[i], [nx, ny] = pts[i + 1]
      const a = Math.min(r, Math.hypot(x - px, y - py) / 2), b = Math.min(r, Math.hypot(nx - x, ny - y) / 2)
      const sign = v => Math.abs(v) < 0.05 ? 0 : Math.sign(v)
      const ux = sign(x - px), uy = sign(y - py), vx = sign(nx - x), vy = sign(ny - y)
      d += ` L ${(x - ux * a).toFixed(1)} ${(y - uy * a).toFixed(1)} Q ${x.toFixed(1)} ${y.toFixed(1)} ${(x + vx * b).toFixed(1)} ${(y + vy * b).toFixed(1)}`
    }
    const last = pts[pts.length - 1]
    return d + ` L ${last[0].toFixed(1)} ${last[1].toFixed(1)}`
  }

  /**
   * Input: pts (number[][]), label (string), font (number), taken (object[]), obstacles (object[])
   * Output: object
   * Processing: Where an arrow's label goes: beside the middle of one of its straight runs (longest level runs first, above then below the line), at the first spot that touches no card and no label already placed.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  function labelSpot(pts, label, font, taken, obstacles) {
    const width = String(label || '').length * font * 0.56, tall = font * 1.05
    const runs = []
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i], len = Math.hypot(b[0] - a[0], b[1] - a[1]), level = Math.abs(a[1] - b[1]) < 0.5
      runs.push({ x: (a[0] + b[0]) / 2, y: (a[1] + b[1]) / 2, len, level, score: len * (level && len > width ? 3 : 1) })
    }
    runs.sort((p, q) => q.score - p.score)
    // A label box: x is its middle, y its baseline.
    const box = (x, y) => ({ x: x - width / 2, y: y - font * 0.8, w: width, h: tall })
    const hits = (bx, r) => bx.x < r.x + r.w && r.x < bx.x + bx.w && bx.y < r.y + r.h && r.y < bx.y + bx.h
    const clear = (x, y) => { const bx = box(x, y); return !obstacles.some(r => hits(bx, r)) && !taken.some(t => hits(bx, t)) }
    for (const run of runs) {
      const spots = run.level ? [[run.x, run.y - font * 0.6], [run.x, run.y + font * 1.15]] : [[run.x + width / 2 + font * 0.4, run.y + font * 0.35], [run.x - width / 2 - font * 0.4, run.y + font * 0.35]]
      for (const [x, y] of spots) if (clear(x, y)) { taken.push(box(x, y)); return { x, y } }
    }
    const first = runs[0], x = first.x, y = first.level ? first.y - font * 0.6 : first.y + font * 0.35
    taken.push(box(x, y))
    return { x, y }
  }

  // --------------------------------------------------------------------------- behaviour -----

  /**
   * Input: id (string | null)
   * Output: void
   * Processing: Pointing at a card brings its own arrows forward and fades the rest, so one card's links can be followed in a busy picture.
   * Changed by: Claude Code
   * Date: 2026-11-27
   */
  function focusArrows(id) {
    const svg = document.getElementById('connections')
    if (!svg) return
    svg.classList.toggle('wb-focus', !!id)
    svg.querySelectorAll('[data-source]').forEach(el => el.classList.toggle('wb-lit', !!id && (el.dataset.source === id || el.dataset.target === id)))
  }

  document.addEventListener('pointerover', e => {
    const card = e.target.closest?.('#blocks .nest-card')
    focusArrows(card ? card.dataset.block : null)
  })
  document.addEventListener('click', e => {
    const go = e.target.closest?.('[data-section-go]')
    if (!go) return
    e.stopPropagation()
    window.PlovaneWhiteboard?.flyTo(go.dataset.sectionGo)
  }, true)

  window.PlovaneSections = { layout, arrows, tiers }
})()
