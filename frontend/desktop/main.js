const { app, BrowserWindow, dialog, ipcMain } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..', '..');
// 8700 first (or PLOVANE_WHITEBOARD_PORT), then 8720-8729 when another project or program holds it.
const PORTS = [...new Set([Number(process.env.PLOVANE_WHITEBOARD_PORT) || 8700,
  ...Array.from({ length: 10 }, (_, i) => 8720 + i)])];
const baseUrl = port => `http://127.0.0.1:${port}`;
let windowRef;
let serverProcess;
let serverProject;
let serverPort;

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

// What a whiteboard server on this port serves, or null when nothing (or something else) answers.
async function health(port) {
  try {
    const response = await fetch(`${baseUrl(port)}/healthz`, { signal: AbortSignal.timeout(900) });
    if (!response.ok) return null;
    const envelope = await response.json();
    return envelope.ok ? envelope.data : null;
  } catch {
    return null;
  }
}

// Whether nothing is listening on the port, so a server can start there.
function portFree(port) {
  return new Promise(resolve => {
    const probe = net.createServer();
    probe.once('error', () => resolve(false));
    probe.once('listening', () => probe.close(() => resolve(true)));
    probe.listen(port, '127.0.0.1');
  });
}

function waitForServer(child, project, port, timeoutMs = 20000) {
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
      const result = await health(port);
      if (result) {
        if (path.resolve(result.project) !== path.resolve(project)) {
          finish(new Error(`Port ${port} already serves another project: ${result.project}`));
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
  if (serverProcess && !serverProcess.killed) {
    if (path.resolve(serverProject) === path.resolve(project)) {
      return { project, url: `${baseUrl(serverPort)}/whiteboard`, reused: true };
    }
    serverProcess.kill();  // switching projects: stop the server this app started
    serverProcess = null;
  }
  const busy = [];
  for (const port of PORTS) {
    const existing = await health(port);
    if (existing && path.resolve(existing.project) === path.resolve(project)) {
      return { project: existing.project, url: `${baseUrl(port)}/whiteboard`, reused: true };
    }
    if (!(await portFree(port))) { busy.push(port); continue; }

    const python = pythonCommand();
    const child = spawn(python.command, [
      ...python.args,
      '-m', 'backend.whiteboard.server', '--project', project, '--port', String(port),
    ], { cwd: ROOT, env: process.env, stdio: ['ignore', 'ignore', 'pipe'] });
    try {
      await waitForServer(child, project, port);
    } catch (error) {
      child.kill();
      throw error;
    }
    serverProcess = child;
    serverProject = project;
    serverPort = port;
    return { project, url: `${baseUrl(port)}/whiteboard`, reused: false };
  }
  throw new Error(`Ports ${busy.join(', ')} are all in use. Set PLOVANE_WHITEBOARD_PORT to a free port.`);
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

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on('before-quit', () => {
  if (serverProcess && !serverProcess.killed) serverProcess.kill();
});
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
