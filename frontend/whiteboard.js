/* Shared local whiteboard client for the five layout prototypes. */
(() => {
    const API = `${window.PLOVANE_API_ORIGIN || 'http://127.0.0.1:8700'}/api/v1/whiteboard`;
    const $ = (selector, root = document) => root.querySelector(selector);
    let commandList = [];
    let lastBoard;

    async function request(path, options = {}) {
        let response;
        try {
            response = await fetch(`${API}/${path}`, {
                ...options,
                headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
            });
        } catch (_) {
            throw new Error('Whiteboard backend unavailable. Start it with: python -m backend.whiteboard.server --project <path>');
        }
        const envelope = await response.json();
        if (!response.ok || !envelope.ok) throw new Error(envelope.error?.message || `Request failed (${response.status})`);
        return envelope.data;
    }

    function message(text, isError = false) {
        let status = $('#whiteboard-status');
        if (!status) {
            status = document.createElement('div');
            status.id = 'whiteboard-status';
            status.setAttribute('role', 'status');
            status.style.cssText = 'position:fixed;right:16px;top:16px;z-index:10000;padding:10px 14px;background:#1a1d23;border:1px solid #334155;border-radius:6px;color:#e2e8f0;max-width:min(520px,90vw);font:13px sans-serif';
            document.body.append(status);
        }
        status.textContent = text;
        status.style.borderColor = isError ? '#ef4444' : '#03dac6';
    }

    function canvas() { return $('#canvas'); }

    function draw(data) {
        lastBoard = data;
        const root = canvas();
        if (!root) return;
        const board = data.board || data;
        const blocks = Object.values(board.blocks || {});
        root.querySelectorAll('[data-plovane-block]').forEach(node => node.remove());
        blocks.forEach(block => {
            const node = document.createElement('button');
            node.type = 'button';
            node.dataset.plovaneBlock = block.id;
            node.className = 'block';
            const position = data.absolute?.[block.id] || block;
            node.style.left = `${position.x ?? 40}px`;
            node.style.top = `${position.y ?? 40}px`;
            node.textContent = block.name || block.id;
            node.title = `${block.id}${block.path ? ` · ${block.path}` : ''}`;
            node.addEventListener('click', () => {
                document.querySelectorAll('[data-plovane-block]').forEach(item => item.style.outline = '');
                node.style.outline = '2px solid white';
                const detail = $('#detail-content') || $('#tab-info') || $('#pane-header');
                if (detail) detail.textContent = `${block.name || block.id} · ${block.id}${block.path ? ` · ${block.path}` : ''}`;
            });
            root.append(node);
        });
        const label = $('#project-chip') || $('#toolbar > div:first-child');
        if (label && data.project) label.dataset.project = data.project;
    }

    async function refresh() { draw(await request('board')); }

    async function runCommand(command, confirm = false) {
        try {
            const data = await request('command', { method: 'POST', body: JSON.stringify({ command, confirm }) });
            if (data.status === 'needs_confirmation') {
                const accepted = window.confirm(`${data.message || 'This command needs confirmation.'}\n\n${JSON.stringify(data.preview || {}, null, 2)}`);
                if (accepted) return runCommand(command, true);
                message('Command cancelled.');
                return;
            }
            const notes = data.notes || [];
            message([data.result?.message || data.result?.id || 'Command completed.', ...notes].filter(Boolean).join(' '));
            await refresh();
        } catch (error) { message(error.message, true); }
    }

    async function history(action) {
        try { await request(action, { method: 'POST', body: '{}' }); await refresh(); message(`${action} complete.`); }
        catch (error) { message(error.message, true); }
    }

    function bindControls() {
        const input = $('#command-input') || $('#console-input') || $('#command-input input');
        if (input) {
            input.setAttribute('list', 'whiteboard-command-list');
            const list = document.createElement('datalist');
            list.id = 'whiteboard-command-list';
            document.body.append(list);
            input.addEventListener('keydown', event => {
                if (event.key !== 'Enter' || !input.value.trim()) return;
                event.preventDefault();
                const command = input.value.trim();
                input.value = '';
                runCommand(command);
            });
        }
        document.querySelectorAll('button').forEach(button => {
            const action = button.textContent.trim().toLowerCase();
            if (action === 'undo') button.onclick = () => history('undo');
            if (action === 'redo') button.onclick = () => history('redo');
        });
    }

    async function init() {
        bindControls();
        try {
            const data = await request('commands');
            commandList = data.commands || [];
            const list = $('#whiteboard-command-list');
            commandList.forEach(item => {
                const option = document.createElement('option');
                option.value = item.example || item.command;
                option.label = `${item.command} — ${item.summary}`;
                list?.append(option);
            });
            await refresh();
        } catch (error) { message(error.message, true); }
    }

    window.PlovaneWhiteboard = { request, refresh, runCommand, history, get board() { return lastBoard; }, get commands() { return commandList; } };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
