/*
Input: Chat prompts, selected agent, and server updates
Output: Agent conversation messages and refreshed project state
Processing: Sends agent chat requests, polls for replies, and updates the whiteboard
Changed by: Aarav
Date: 2026-11-14
*/

/* Chat with a whiteboard agent from the prompt panel.
 *
 * A "Talk to" picker in the prompt panel chooses who a plain-English prompt
 * goes to: the Plovane planner (the panel's original behaviour) or any agent
 * on the board. With an agent picked, prompts open a chat thread above the
 * panel; the server runs Claude as that agent, held to its grants, and this
 * file polls for the reply and refreshes the board when the agent changes it.
 * Slash commands still run directly, whoever is picked.
 *
 * Server side: backend/whiteboard/agent_chat.py. Loaded after whiteboard.js,
 * whose window.PlovaneWhiteboard gives us request, refresh and runCommand.
 */
(() => {
  const wb = window.PlovaneWhiteboard;
  if (!wb) return;
  const $ = (s, r = document) => r.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const input = $('#prompt-input');
  const dock = $('.prompt-dock');
  if (!input || !dock) return;
  const plannerPlaceholder = input.placeholder;
  const chat = { agent: '', next: 0, running: false, timer: null, refreshTimer: null };
  const store = {
    get() { try { return localStorage.getItem('plovane-chat-agent') || ''; } catch (_) { return ''; } },
    set(v) { try { localStorage.setItem('plovane-chat-agent', v); } catch (_) { /* per-viewer convenience only */ } },
  };

  // -- Picker -------------------------------------------------------------------
  const picker = document.createElement('label');
  picker.className = 'agent-picker';
  picker.innerHTML = '<span>Talk to</span><select aria-label="Who your prompt goes to"><option value="">Plovane planner</option></select>';
  $('.prompt-title').append(picker);
  const select = $('select', picker);

  // -- Chat panel (floats above the prompt panel, over the canvas) -------------
  const panel = document.createElement('section');
  panel.className = 'agent-chat';
  panel.hidden = true;
  panel.setAttribute('aria-label', 'Agent chat');
  panel.innerHTML = `
    <header class="agent-chat-head">
      <span class="agent-avatar">◉</span>
      <div class="agent-chat-who"><b class="agent-chat-name"></b><small class="agent-chat-access"></small></div>
      <div class="agent-chat-actions">
        <button type="button" data-chat="clear" title="Forget this conversation">New chat</button>
        <button type="button" data-chat="collapse" class="agent-chat-collapse" aria-expanded="true" title="Hide or show the conversation">⌄</button>
      </div>
    </header>
    <div class="agent-chat-log" aria-live="polite"></div>`;
  dock.append(panel);
  const log = $('.agent-chat-log', panel);

  // Input: none
  // Output: string[]
  // Processing: The agents on the board right now.
  function agentNames() {
    return Object.keys(wb.board?.board?.agents || {}).sort();
  }

  // Input: none
  // Output: void
  // Processing: Keep the picker's options in step with the board's agents.
  function syncAgents() {
    const names = agentNames();
    const have = [...select.options].slice(1).map(o => o.value);
    if (names.join('\n') !== have.join('\n')) {
      select.innerHTML = '<option value="">Plovane planner</option>' +
        names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
    }
    if (chat.agent && !names.includes(chat.agent) && wb.board) setAgent('');
    select.value = chat.agent;
  }

  // Input: name (string)
  // Output: void
  // Processing: Switch who prompts go to and load that agent's conversation.
  function setAgent(name) {
    chat.agent = name;
    chat.next = 0;
    chat.running = false;
    clearTimeout(chat.timer);
    store.set(name);
    select.value = name;
    log.innerHTML = '';
    dock.classList.toggle('chatting', Boolean(name));
    panel.hidden = !name;
    if (!name) { input.placeholder = plannerPlaceholder; return; }
    $('.agent-chat-name', panel).textContent = name;
    input.placeholder = `Message ${name}…  (type / for commands)`;
    poll();
  }

  // -- Rendering ----------------------------------------------------------------

  // Input: text (string)
  // Output: string
  // Processing: Escape text, then allow `code`, **bold** and line breaks.
  function format(text) {
    return esc(text)
      .replace(/`([^`\n]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>')
      .replace(/\n/g, '<br>');
  }

  // Input: grants (object[])
  // Output: void
  // Processing: Show what the agent may touch, with a shortcut to grant access.
  function showAccess(grants, model) {
    const host = $('.agent-chat-access', panel);
    const live = (grants || []).filter(g => g.mode !== 'none');
    const via = model ? ` · ${esc(model)}` : '';
    if (live.length) {
      host.innerHTML = esc(live.map(g => `${g.path || '.'} · ${g.mode}`).join(', ')) + via;
      return;
    }
    host.innerHTML = `No access yet <button type="button" data-chat="grant">Grant access…</button>${via}`;
  }

  // Input: event (object)
  // Output: string
  // Processing: One chat event as HTML.
  function eventHtml(event) {
    if (event.role === 'user') return `<div class="chat-msg chat-user">${format(event.text)}</div>`;
    if (event.role === 'assistant') return `<div class="chat-msg chat-agent">${format(event.text)}</div>`;
    if (event.role === 'error') return `<div class="chat-note chat-error">${format(event.text)}</div>`;
    if (event.role === 'tool') {
      const state = event.needs_confirmation ? 'wait' : event.ok ? 'ok' : 'fail';
      const mark = { ok: '✓', fail: '✕', wait: '⏸' }[state];
      const detail = event.summary ? `<small>${esc(event.summary)}</small>` : '';
      const review = event.needs_confirmation
        ? `<button type="button" data-chat-run="${esc(event.command)}">Review &amp; run</button>` : '';
      return `<div class="chat-tool chat-tool-${state}"><span class="chat-tool-mark">${mark}</span><code>${esc(event.command)}</code>${review}${detail}</div>`;
    }
    return '';
  }

  // Input: events (object[])
  // Output: boolean
  // Processing: Append events to the log; true when one of them changed the board.
  function append(events) {
    const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    $('.chat-working', log)?.remove();
    log.insertAdjacentHTML('beforeend', events.map(eventHtml).join(''));
    if (chat.running) log.insertAdjacentHTML('beforeend', `<div class="chat-working">${esc(chat.agent)} is working<span>…</span></div>`);
    if (!log.children.length) log.innerHTML = `<div class="chat-empty">Ask ${esc(chat.agent)} to build or change something on this board. It can only touch what it has been granted.</div>`;
    else $('.chat-empty', log)?.remove();
    if (stick || events.some(e => e.role === 'user')) log.scrollTop = log.scrollHeight;
    return events.some(e => e.role === 'tool' && e.changed);
  }

  // Input: none
  // Output: void
  // Processing: Redraw the board soon, once, after the agent changes it.
  function refreshSoon() {
    clearTimeout(chat.refreshTimer);
    chat.refreshTimer = setTimeout(() => wb.refresh().then(syncAgents), 150);
  }

  // Input: data (object)
  // Output: void
  // Processing: Apply one server snapshot: events, grants, and whether to keep polling.
  function apply(data) {
    if (data.agent !== chat.agent) return;
    chat.running = data.running;
    chat.next = data.next;
    showAccess(data.grants, data.model);
    if (append(data.events || [])) refreshSoon();
    clearTimeout(chat.timer);
    if (chat.running) chat.timer = setTimeout(poll, 700);
  }

  // -- Server calls -------------------------------------------------------------

  // Input: none
  // Output: Promise<void>
  // Processing: Fetch events since the last one seen.
  async function poll() {
    const agent = chat.agent;
    if (!agent) return;
    try {
      apply(await wb.request(`agents/${encodeURIComponent(agent)}/chat?since=${chat.next}`));
    } catch (error) {
      if (agent !== chat.agent) return;
      append([{ role: 'error', text: error.message }]);
      if (chat.running) chat.timer = setTimeout(poll, 2000);
    }
  }

  // Input: text (string)
  // Output: Promise<void>
  // Processing: Send the agent a message and start polling for its reply.
  async function send(text) {
    const agent = chat.agent;
    if (chat.running) { append([{ role: 'error', text: `${agent} is still answering. Wait for it to finish.` }]); return; }
    panel.classList.remove('collapsed');
    try {
      apply(await wb.request(`agents/${encodeURIComponent(agent)}/chat`, { method: 'POST', body: JSON.stringify({ message: text }) }));
    } catch (error) {
      append([{ role: 'user', text }, { role: 'error', text: error.message }]);
    }
  }

  // -- Wiring -------------------------------------------------------------------

  // Input: event (Event)
  // Output: void
  // Processing: Take over the prompt when an agent is picked and the text is not a slash command.
  function intercept(event) {
    const text = input.value.trim();
    if (!chat.agent || !text || text.startsWith('/')) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    input.value = '';
    input.dispatchEvent(new Event('input'));
    send(text);
  }
  // Capture phase, so this runs before whiteboard.js sends the prompt to the planner.
  document.addEventListener('keydown', e => {
    if (e.target === input && e.key === 'Enter' && !e.shiftKey && !e.isComposing) intercept(e);
  }, true);
  document.addEventListener('click', e => { if (e.target.closest('#send-prompt')) intercept(e); }, true);

  select.addEventListener('change', () => setAgent(select.value));
  select.addEventListener('focus', syncAgents);

  document.addEventListener('click', e => {
    const run = e.target.closest('[data-chat-run]');
    if (run) { wb.runCommand(run.dataset.chatRun); return; }
    const action = e.target.closest('[data-chat]')?.dataset.chat;
    if (action === 'collapse') {
      const collapsed = panel.classList.toggle('collapsed');
      $('.agent-chat-collapse', panel).setAttribute('aria-expanded', String(!collapsed));
    } else if (action === 'clear' && chat.agent) {
      wb.request(`agents/${encodeURIComponent(chat.agent)}/chat`, { method: 'DELETE' })
        .then(() => setAgent(chat.agent))
        .catch(error => append([{ role: 'error', text: error.message }]));
    } else if (action === 'grant' && chat.agent) {
      // Runs as the person, so whiteboard.js shows its "Review before applying" box first.
      wb.runCommand(`/agent grant ${chat.agent} . --mode read-write`);
    }
    // Clicking an agent in the side bar's Agents tab opens a chat with it.
    const card = e.target.closest('.agent-card');
    const name = card && $('b', card)?.textContent;
    if (name && agentNames().includes(name)) { syncAgents(); setAgent(name); input.focus(); }
  });

  // The board loads asynchronously; restore the last agent once it has.
  const restore = store.get();
  const boot = setInterval(() => {
    if (!wb.board) return;
    clearInterval(boot);
    syncAgents();
    if (restore && agentNames().includes(restore)) setAgent(restore);
  }, 200);
  // Keep the picker and the header's access line current when grants change elsewhere.
  setInterval(() => { syncAgents(); if (chat.agent && !chat.running) poll(); }, 3000);
})();
