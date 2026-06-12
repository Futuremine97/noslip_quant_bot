const { app, BrowserWindow, Menu, shell } = require('electron');
const path = require('path');
const http = require('http');
const { spawn } = require('child_process');

const PORT = process.env.NOSLIP_PORT || '3000';
const API_PORT = process.env.NOSLIP_API_PORT || '8787';
const DASHBOARD_URL = process.env.NOSLIP_DASHBOARD_URL || `http://localhost:${PORT}`;

let mainWindow = null;
let nextProcess = null;
let apiProcess = null;
let retryTimer = null;

// App root: unpacked resources when packaged, repo root in development.
function appRoot() {
  return app.isPackaged ? path.join(process.resourcesPath, 'app') : path.resolve(__dirname, '..');
}

function probe(url) {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout: 2500 }, (res) => { res.resume(); resolve(true); });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

function spawnNode(scriptArgs, extraEnv) {
  // Run a Node script using Electron's binary in Node mode.
  return spawn(process.execPath, scriptArgs, {
    cwd: appRoot(),
    env: { ...process.env, ELECTRON_RUN_AS_NODE: '1', ...extraEnv },
  });
}

function startServices() {
  const root = appRoot();

  const apiPath = path.join(root, 'server', 'index.js');
  apiProcess = spawnNode([apiPath], { PORT: API_PORT });
  apiProcess.stdout.on('data', (d) => console.log(`[API] ${d}`));
  apiProcess.stderr.on('data', (d) => console.error(`[API] ${d}`));

  const nextBin = path.join(root, 'node_modules', 'next', 'dist', 'bin', 'next');
  nextProcess = spawnNode([nextBin, 'start', '-p', PORT], {});
  nextProcess.stdout.on('data', (d) => console.log(`[Next] ${d}`));
  nextProcess.stderr.on('data', (d) => console.error(`[Next] ${d}`));
}

async function connectWhenReady() {
  if (!mainWindow) return;
  if (await probe(DASHBOARD_URL)) {
    if (retryTimer) { clearInterval(retryTimer); retryTimer = null; }
    mainWindow.loadURL(DASHBOARD_URL);
    return;
  }
  mainWindow.loadFile(path.join(__dirname, 'offline.html'));
  if (!retryTimer) {
    retryTimer = setInterval(async () => {
      if (mainWindow && (await probe(DASHBOARD_URL))) {
        clearInterval(retryTimer);
        retryTimer = null;
        mainWindow.loadURL(DASHBOARD_URL);
      }
    }, 3000);
  }
}

function buildMenu() {
  const isMac = process.platform === 'darwin';
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(isMac ? [{ label: app.name, submenu: [{ role: 'about' }, { type: 'separator' }, { role: 'hide' }, { role: 'quit' }] }] : []),
    {
      label: 'Dashboard',
      submenu: [
        { label: '새로고침 / 재연결', accelerator: 'CmdOrCtrl+R', click: connectWhenReady },
        { type: 'separator' },
        { label: 'GitHub 저장소', click: () => shell.openExternal('https://github.com/Futuremine97/noslip_quant_bot') },
        ...(isMac ? [] : [{ type: 'separator' }, { role: 'quit' }]),
      ],
    },
    { label: 'View', submenu: [{ role: 'zoomIn' }, { role: 'zoomOut' }, { role: 'resetZoom' }, { type: 'separator' }, { role: 'togglefullscreen' }, { role: 'toggleDevTools' }] },
  ]));
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1380,
    height: 880,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: '#0a0a0a',
    title: 'NoSlip Quant',
    icon: path.join(__dirname, 'icon.png'),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  connectWhenReady();
  mainWindow.on('closed', () => { mainWindow = null; });
}

app.on('ready', async () => {
  buildMenu();
  // If a dev server is already running (dev workflow), just connect to it;
  // otherwise boot the bundled production servers.
  const alreadyRunning = await probe(DASHBOARD_URL);
  if (!alreadyRunning && !process.env.NOSLIP_DASHBOARD_URL) {
    startServices();
  }
  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('quit', () => {
  if (apiProcess) apiProcess.kill();
  if (nextProcess) nextProcess.kill();
});

app.on('activate', () => {
  if (mainWindow === null) createWindow();
});
