const { app, BrowserWindow, dialog, ipcMain } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..', '..');
const PORT = 8700;
const BASE_URL = `http://127.0.0.1:${PORT}`;
const HEALTH_URL = `${BASE_URL}/healthz`;
let windowRef;
let serverProcess;

function pythonCommand() {
  const localPython = process.platform === 'win32'
    ? path.join(ROOT, '.venv', 'Scripts', 'python.exe')
    : path.join(ROOT, '.venv', 'bin', 'python');
  if (fs.existsSync(localPython)) return { command: localPython, args: [] };
  return {
    command: process.env.PLOVANE_PYTHON || (process.platform === 'win32' ? 'python' : 'python3'),
    args: [],
  };
}

async function health() {
  try {
    const response = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(900) });
    if (!response.ok) return null;
    const envelope = await response.json();
    return envelope.ok ? envelope.data : null;
  } catch {
    return null;
  }
}

function waitForServer(child, project, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    let stderr = '';
    let settled = false;
    const timeout = setTimeout(() => finish(new Error('Whiteboard server did not start within 20 seconds.')), timeoutMs);
    const onError = error => finish(error);
    const onExit = code => finish(new Error(stderr.trim() || `Whiteboard server exited (${code ?? 'unknown'}).`));
    const onData = chunk => { stderr = `${stderr}${chunk}`.slice(-5000); };
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      child.removeListener('error', onError);
      child.removeListener('exit', onExit);
      child.stderr?.removeListener('data', onData);
      error ? reject(error) : resolve(value);
    };
    child.on('error', onError);
    child.on('exit', onExit);
    child.stderr?.on('data', onData);
    const poll = async () => {
      if (settled) return;
      const result = await health();
      if (result) {
        if (path.resolve(result.project) !== path.resolve(project)) {
          finish(new Error(`Port ${PORT} already serves another project: ${result.project}`));
        } else {
          finish(null, result);
        }
        return;
      }
      if (!settled && !child.killed) setTimeout(poll, 250);
    };
    poll();
  });
}

async function connectProject(project) {
  if (!project || !fs.existsSync(project) || !fs.statSync(project).isDirectory()) {
    throw new Error('Choose an existing project folder.');
  }
  const existing = await health();
  if (existing) {
    if (path.resolve(existing.project) !== path.resolve(project)) {
      throw new Error(`Port ${PORT} already serves another project: ${existing.project}`);
    }
    return { project: existing.project, url: `${BASE_URL}/whiteboard`, reused: true };
  }
  if (serverProcess && !serverProcess.killed) throw new Error('Whiteboard server is already starting.');

  const python = pythonCommand();
  serverProcess = spawn(python.command, [
    ...python.args,
    '-m', 'backend.whiteboard.server', '--project', project, '--port', String(PORT),
  ], { cwd: ROOT, env: process.env, stdio: ['ignore', 'ignore', 'pipe'] });
  try {
    await waitForServer(serverProcess, project);
  } catch (error) {
    serverProcess.kill();
    serverProcess = null;
    throw error;
  }
  return { project, url: `${BASE_URL}/whiteboard`, reused: false };
}

function createWindow() {
  windowRef = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 800,
    minHeight: 600,
    title: 'Plovane',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  windowRef.loadFile(path.join(__dirname, 'index.html'));
}

ipcMain.handle('select-project', async () => {
  const result = await dialog.showOpenDialog(windowRef, {
    title: 'Choose a project folder',
    properties: ['openDirectory'],
  });
  return result.canceled ? null : result.filePaths[0];
});
ipcMain.handle('connect-project', (_event, project) => connectProject(project));
ipcMain.handle('open-whiteboard', (_event, url) => windowRef.loadURL(url));
ipcMain.handle('go-home', () => windowRef.loadFile(path.join(__dirname, 'index.html')));

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on('before-quit', () => {
  if (serverProcess && !serverProcess.killed) serverProcess.kill();
});
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
