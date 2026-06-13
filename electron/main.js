const { app, BrowserWindow, Menu, shell, ipcMain, screen } = require('electron');
const path = require('path');
const http = require('http');
const { spawn } = require('child_process');
const tray = require('./tray');
const terminals = require('./terminals');

const PORT = process.env.NOSLIP_PORT || '3000';
const API_PORT = process.env.NOSLIP_API_PORT || '8787';
const DASHBOARD_URL = process.env.NOSLIP_DASHBOARD_URL || `http://localhost:${PORT}`;

let mainWindow = null;
let popover = null;
let nextProcess = null;
let apiProcess = null;
let retryTimer = null;
let statusTimer = null;
let targetRoute = '/';

function fullUrl() {
  const r = targetRoute && targetRoute !== '/' ? targetRoute : '';
  return DASHBOARD_URL + r;
}

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
    mainWindow.loadURL(fullUrl());
    return;
  }
  mainWindow.loadFile(path.join(__dirname, 'offline.html'));
  if (!retryTimer) {
    retryTimer = setInterval(async () => {
      if (mainWindow && (await probe(DASHBOARD_URL))) {
        clearInterval(retryTimer);
        retryTimer = null;
        mainWindow.loadURL(fullUrl());
      }
    }, 3000);
  }
}

// ── 메뉴바(트레이) 연동 ──
function openRoute(route) {
  targetRoute = route || '/manage';
  if (!mainWindow) {
    createWindow();
  } else {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
    connectWhenReady();
  }
}

function restartServices() {
  if (apiProcess) { try { apiProcess.kill(); } catch (_) { /* noop */ } apiProcess = null; }
  if (nextProcess) { try { nextProcess.kill(); } catch (_) { /* noop */ } nextProcess = null; }
  startServices();
  setTimeout(connectWhenReady, 1500);
}

async function refreshStatus() {
  const [dashboard, api] = await Promise.all([
    probe(DASHBOARD_URL),
    probe(`http://localhost:${API_PORT}/`),
  ]);
  tray.setStatus({ dashboard, api });
}

function startStatusPolling() {
  refreshStatus();
  if (!statusTimer) statusTimer = setInterval(refreshStatus, 5000);
}

// ── 터미널 통합 팝오버(메뉴바 클릭 시 표시) ──
function createPopover() {
  popover = new BrowserWindow({
    width: 440,
    height: 580,
    show: false,
    frame: false,
    resizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    movable: false,
    backgroundColor: '#0b0c16',
    webPreferences: {
      preload: path.join(__dirname, 'preload-terminals.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  popover.loadFile(path.join(__dirname, 'terminals.html'));
  popover.on('blur', () => { if (popover && !popover.webContents.isDevToolsOpened()) popover.hide(); });
}

function togglePopover(bounds) {
  if (!popover) createPopover();
  if (popover.isVisible()) { popover.hide(); return; }
  // 트레이 아이콘 아래 중앙에 배치
  const { width } = popover.getBounds();
  let x = Math.round((bounds ? bounds.x + bounds.width / 2 : 0) - width / 2);
  let y = Math.round(bounds ? bounds.y + bounds.height + 4 : 24);
  const area = screen.getPrimaryDisplay().workArea;
  x = Math.max(area.x + 4, Math.min(x, area.x + area.width - width - 4));
  if (!bounds) x = area.x + area.width - width - 8;
  popover.setPosition(x, y, false);
  popover.show();
  popover.focus();
  popover.webContents.send('focus'); // 표시 시 갱신 트리거(렌더러 focus 리스너)
}

function registerTerminalIpc() {
  ipcMain.handle('terminals:list', () => terminals.listAll());
  ipcMain.handle('terminals:send', (_e, { item, command }) => terminals.sendCommand(item, command));
  ipcMain.handle('terminals:focus', (_e, { item }) => terminals.focus(item));
  ipcMain.handle('terminals:kill', (_e, { item }) => terminals.kill(item));
  ipcMain.handle('terminals:new', (_e, { kind }) => terminals.newSession(kind));
  ipcMain.handle('terminals:openDashboard', () => { if (popover) popover.hide(); openRoute('/manage'); });
  ipcMain.handle('terminals:hide', () => { if (popover) popover.hide(); });
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
  registerTerminalIpc();
  createPopover();
  // 메뉴바(트레이) 앱 생성 — 좌클릭: 터미널 팝오버, 우클릭: 메뉴
  tray.createTray({
    onClick: (bounds) => togglePopover(bounds),
    openRoute,
    restartServices,
    refreshStatus,
  });
  // If a dev server is already running (dev workflow), just connect to it;
  // otherwise boot the bundled production servers.
  const alreadyRunning = await probe(DASHBOARD_URL);
  if (!alreadyRunning && !process.env.NOSLIP_DASHBOARD_URL) {
    startServices();
  }
  createWindow();
  startStatusPolling();
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
