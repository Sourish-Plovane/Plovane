(() => {
  const API = '/api/v1/whiteboard';
  const output = document.querySelector('#terminal-output');
  const input = document.querySelector('#terminal-input');
  const status = document.querySelector('#connection-status');
  const completions = document.querySelector('#completion-list');
  const history = JSON.parse(sessionStorage.getItem('plovane-cli-history') || '[]');
  let historyIndex = history.length;
  let commands = [];
  let matches = [];
  let matchIndex = -1;
  let pendingConfirmation = null;

  function line(text, kind = '') {
    const element = document.createElement('div');
    element.className = `terminal-line ${kind}`.trim();
    element.textContent = text;
    output.append(element);
    output.scrollTop = output.scrollHeight;
  }

  async function request(path, options) {
    const response = await fetch(`${API}/${path}`, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) },
    });
    const envelope = await response.json();
    if (!response.ok || !envelope.ok) {
      throw new Error(envelope.error?.message || `Request failed (${response.status})`);
    }
    return envelope.data;
  }

  function updateTheme() {
    try {
      document.body.dataset.theme = parent.document.body.dataset.theme || 'light';
      const accent = getComputedStyle(parent.document.documentElement).getPropertyValue('--accent').trim();
      if (accent) document.documentElement.style.setProperty('--terminal-accent', accent);
    } catch (_) {
      document.body.dataset.theme = 'light';
    }
  }

  function showCompletions() {
    const value = input.value.trimStart();
    matches = value.startsWith('/')
      ? commands.filter(command => command.command.startsWith(value) || command.example.startsWith(value)).slice(0, 8)
      : [];
    matchIndex = matches.length ? 0 : -1;
    completions.replaceChildren();
    for (const [index, command] of matches.entries()) {
      const button = document.createElement('button');
      button.type = 'button';
      button.setAttribute('aria-selected', String(index === matchIndex));
      button.append(document.createTextNode(command.command));
      const summary = document.createElement('small');
      summary.textContent = command.summary || '';
      button.append(summary);
      button.addEventListener('click', () => {
        input.value = command.example || command.command;
        completions.hidden = true;
        input.focus();
      });
      completions.append(button);
    }
    completions.hidden = matches.length === 0;
  }

  function selectCompletion(direction) {
    if (!matches.length) return false;
    matchIndex = (matchIndex + direction + matches.length) % matches.length;
    [...completions.children].forEach((button, index) => {
      button.setAttribute('aria-selected', String(index === matchIndex));
    });
    return true;
  }

  function useCompletion() {
    if (matchIndex < 0 || !matches[matchIndex]) return false;
    input.value = matches[matchIndex].example || matches[matchIndex].command;
    completions.hidden = true;
    return true;
  }

  function normalizeGitCommand(value) {
    const command = value.trim();
    if (/^git\s+status$/i.test(command)) return '/git status';
    if (/^git\s+pull$/i.test(command)) return '/git sync --pull';
    if (/^git\s+push$/i.test(command)) return '/git sync --push';
    return command;
  }

  async function execute(command, confirm = false) {
    const result = await request('command', {
      method: 'POST',
      body: JSON.stringify({ command, confirm }),
    });
    if (result.status === 'needs_confirmation') return { confirmation: result };
    if (parent.PlovaneWhiteboard?.refresh) await parent.PlovaneWhiteboard.refresh();
    return { result };
  }

  function printResult(result) {
    const display = result?.result ?? result;
    line(typeof display === 'string' ? display : JSON.stringify(display, null, 2));
  }

  async function run(raw) {
    let command = normalizeGitCommand(raw);
    if (!command) return;
    line(`$ ${raw}`, 'command');

    if (pendingConfirmation) {
      const pending = pendingConfirmation;
      pendingConfirmation = null;
      if (/^(y|yes)$/i.test(command)) {
        try {
          printResult(await execute(pending.command, true));
        } catch (error) {
          line(error.message, 'error');
        }
      } else {
        line(/^(n|no)$/i.test(command) ? 'Cancelled.' : 'Confirmation cancelled.');
      }
      return;
    }

    if (command === 'help' || command === '/help') {
      line('Plovane CLI: enter any slash command from the MCP command set.');
      line('Built-ins: /help, clear, history. Git aliases: git status, git pull, git push.');
      line(`${commands.length} backend commands loaded. Use / to browse commands.`);
      return;
    }
    if (command === 'clear') {
      output.replaceChildren();
      return;
    }
    if (command === 'history') {
      history.forEach((entry, index) => line(`${index + 1}  ${entry}`));
      return;
    }
    if (!command.startsWith('/')) {
      line('Unknown command. Use /help, a slash command, or git status/pull/push.', 'error');
      return;
    }

    try {
      const response = await execute(command);
      if (response.confirmation) {
        pendingConfirmation = { command };
        line(JSON.stringify(response.confirmation.preview || response.confirmation, null, 2));
        line(`${response.confirmation.message || 'This command needs confirmation.'} Type y to apply or n to cancel.`, 'muted');
      } else {
        printResult(response.result);
      }
    } catch (error) {
      line(error.message, 'error');
    }
  }

  document.querySelector('#terminal-form').addEventListener('submit', async event => {
    event.preventDefault();
    const command = input.value.trim();
    if (!command) return;
    input.value = '';
    completions.hidden = true;
    if (!['clear', 'history', 'help', '/help'].includes(command)) {
      history.push(command);
      if (history.length > 100) history.shift();
      sessionStorage.setItem('plovane-cli-history', JSON.stringify(history));
    }
    historyIndex = history.length;
    await run(command);
  });

  input.addEventListener('input', showCompletions);
  input.addEventListener('keydown', event => {
    if (event.key === 'Tab' && useCompletion()) {
      event.preventDefault();
    } else if (event.key === 'ArrowDown' && !completions.hidden) {
      event.preventDefault();
      selectCompletion(1);
    } else if (event.key === 'ArrowUp' && !completions.hidden) {
      event.preventDefault();
      selectCompletion(-1);
    } else if (event.key === 'ArrowUp' && completions.hidden) {
      event.preventDefault();
      if (historyIndex > 0) input.value = history[--historyIndex];
    } else if (event.key === 'ArrowDown' && completions.hidden) {
      event.preventDefault();
      if (historyIndex < history.length - 1) input.value = history[++historyIndex];
      else { historyIndex = history.length; input.value = ''; }
    } else if (event.key === 'Escape') {
      completions.hidden = true;
    }
  });

  document.querySelector('#clear-terminal').addEventListener('click', () => output.replaceChildren());
  window.addEventListener('message', event => {
    if (event.origin !== location.origin || event.data?.type !== 'plovane-cli-focus') return;
    input.focus();
  });
  new MutationObserver(updateTheme).observe(parent.document.body, { attributes: true, attributeFilter: ['data-theme'] });
  updateTheme();
  if (parent.document.body.classList.contains('cli-open')) input.focus();

  request('commands')
    .then(data => {
      commands = data.commands || [];
      status.textContent = 'Backend connected';
      line(`Plovane CLI connected. ${commands.length} commands available. Type /help for usage.`, 'muted');
    })
    .catch(error => {
      status.textContent = 'Backend unavailable';
      line(error.message, 'error');
      line('Start the selected project whiteboard server, then reopen the CLI.', 'muted');
    });
})();
