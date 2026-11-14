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
let currentProject = null;
let whiteboardUrl = null;

/**
 * Input: argv (string[])
 * Output: string | null
 * Processing: The folder after --project (or --project=dir) in a launch command line, if any.
 * Changed by: Claude Code
 * Date: 2026-11-09
 */
function projectFromArgs(argv) {
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--project' && argv[i + 1]) return path.resolve(argv[i + 1]);
    if (argv[i].startsWith('--project=')) return path.resolve(argv[i].slice('--project='.length));
  }
  return null;
}

// The CLI (`plovane open`, `plovane wb`) launches the app like `docker` starts
// Docker Desktop. A second launch must not start a second app fighting over
// port 8700: it hands its --project to this one and exits.
if (!app.requestSingleInstanceLock()) {
  app.quit();
}

/**
 * Input: none
 * Output: {command: string, args: string[]}
 * Processing: The Python to run the server with: the repo .venv, then PLOVANE_PYTHON, then python3.
 * Changed by: Claude Code
 * Date: 2026-11-09
 */
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

/**
 * Input: none
 * Output: Promise<object | null>
 * Processing: The /healthz data of whatever answers on port 8700, or null.
 * Changed by: Claude Code
 * Date: 2026-11-09
 */
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

/**
 * Input: child (ChildProcess), project (string), timeoutMs (number)
 * Output: Promise<object>
 * Processing: Resolve once the started server answers for this project; reject on exit, timeout or a foreign project.
 * Changed by: Claude Code
 * Date: 2026-11-09
 */
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

/**
 * Input: project (string)
 * Output: Promise<{project: string, url: string, reused: boolean}>
 * Processing: Start (or reuse) the whiteboard server for a folder; switches project when this app owns the running server.
 * Changed by: Claude Code
 * Date: 2026-11-09
 */
async function connectProject(project) {
  if (!project || !fs.existsSync(project) || !fs.statSync(project).isDirectory()) {
    throw new Error('Choose an existing project folder.');
  }
  let existing = await health();
  if (existing && path.resolve(existing.project) !== path.resolve(project) && serverProcess && !serverProcess.killed) {
    // This app started the server for another folder: stop it and start one for the new folder.
    await stopServer();
    existing = await health();
  }
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
    '--exit-with', String(process.pid),
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

/**
 * Input: none
 * Output: Promise<void>
 * Processing: Stop the server this app started and wait until the port is free.
 * Changed by: Claude Code
 * Date: 2026-11-09
 */
async function stopServer() {
  if (!serverProcess || serverProcess.killed) return;
  const child = serverProcess;
  serverProcess = null;
  child.kill();
  for (let i = 0; i < 40 && await health(); i += 1) await new Promise(r => setTimeout(r, 100));
}

/**
 * Input: project (string)
 * Output: Promise<void>
 * Processing: Connect a folder and show its whiteboard in the window; show the error on the start page otherwise.
 * Changed by: Claude Code
 * Date: 2026-11-09
 */
async function openProject(project) {
  if (!windowRef || windowRef.isDestroyed()) createWindow(false);
  focusWindow();
  try {
    const connection = await connectProject(project);
    currentProject = connection.project;
    whiteboardUrl = connection.url;
    await windowRef.loadURL(whiteboardUrl);
  } catch (error) {
    await windowRef.loadFile(path.join(__dirname, 'index.html'), { query: { error: error.message, project } });
  }
}

/**
 * Input: none
 * Output: void
 * Processing: Bring the window to the front, restoring it if minimised.
 * Changed by: Claude Code
 * Date: 2026-11-09
 */
function focusWindow() {
  if (!windowRef || windowRef.isDestroyed()) return;
  if (windowRef.isMinimized()) windowRef.restore();
  windowRef.show();
  windowRef.focus();
  if (process.platform === 'darwin') app.focus({ steal: true });
}

/**
 * Input: showStart (boolean)
 * Output: void
 * Processing: Open the main window, on the start page or the open project's whiteboard.
 * Changed by: Claude Code
 * Date: 2026-11-09
 */
function createWindow(showStart = true) {
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
  if (!showStart) return;
  if (whiteboardUrl) windowRef.loadURL(whiteboardUrl);
  else windowRef.loadFile(path.join(__dirname, 'index.html'));
}

ipcMain.handle('select-project', async () => {
  const result = await dialog.showOpenDialog(windowRef, {
    title: 'Choose a project folder',
    properties: ['openDirectory'],
  });
  return result.canceled ? null : result.filePaths[0];
});
ipcMain.handle('connect-project', async (_event, project) => {
  const connection = await connectProject(project);
  currentProject = connection.project;
  whiteboardUrl = connection.url;
  return connection;
});
ipcMain.handle('open-whiteboard', (_event, url) => windowRef.loadURL(url));
ipcMain.handle('go-home', () => windowRef.loadFile(path.join(__dirname, 'index.html')));
ipcMain.handle('current-project', () => currentProject);

app.on('second-instance', (_event, argv) => {
  const project = projectFromArgs(argv);
  if (project && project !== currentProject) openProject(project);
  else {
    if (!windowRef || windowRef.isDestroyed()) createWindow();
    focusWindow();
  }
});

app.whenReady().then(() => {
  const project = projectFromArgs(process.argv) || (process.env.PLOVANE_PROJECT ? path.resolve(process.env.PLOVANE_PROJECT) : null);
  if (project) openProject(project);
  else createWindow();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on('before-quit', () => {
  if (serverProcess && !serverProcess.killed) serverProcess.kill();
});
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
