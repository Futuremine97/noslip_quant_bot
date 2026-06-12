/**
 * No Slip Quant — Desktop Dashboard (Electron)
 *
 * Wraps the Next.js dashboard in a native window. If the local server
 * (default http://localhost:3000) is not running, shows a styled offline
 * page with start instructions and auto-retries every 5 seconds.
 *
 * Dashboard URL can be overridden:
 *   NOSLIP_DASHBOARD_URL=https://your-host  (env var)
 *   or via menu: No Slip Quant > Set Dashboard URL...
 */
const { app, BrowserWindow, Menu, shell, dialog } = require("electron");
const path = require("path");
const http = require("http");
const https = require("https");

const DEFAULT_URL = process.env.NOSLIP_DASHBOARD_URL || "http://localhost:3000";
let dashboardUrl = DEFAULT_URL;
let mainWindow = null;
let retryTimer = null;

function probe(url) {
  return new Promise((resolve) => {
    try {
      const lib = url.startsWith("https") ? https : http;
      const req = lib.get(url, { timeout: 3000 }, (res) => {
        res.resume();
        resolve(res.statusCode > 0);
      });
      req.on("error", () => resolve(false));
      req.on("timeout", () => { req.destroy(); resolve(false); });
    } catch {
      resolve(false);
    }
  });
}

async function loadDashboard() {
  if (!mainWindow) return;
  const ok = await probe(dashboardUrl);
  if (ok) {
    if (retryTimer) { clearInterval(retryTimer); retryTimer = null; }
    mainWindow.loadURL(dashboardUrl);
  } else {
    mainWindow.loadFile(path.join(__dirname, "offline.html"));
    if (!retryTimer) {
      retryTimer = setInterval(async () => {
        if (await probe(dashboardUrl)) {
          clearInterval(retryTimer);
          retryTimer = null;
          mainWindow.loadURL(dashboardUrl);
        }
      }, 5000);
    }
  }
}

async function setDashboardUrl() {
  // Simple prompt via message box + clipboard hint (Electron has no native prompt)
  const { response } = await dialog.showMessageBox(mainWindow, {
    type: "question",
    buttons: ["localhost:3000 (기본)", "취소"],
    defaultId: 0,
    title: "Dashboard URL",
    message: "대시보드 주소를 선택하세요.\n다른 주소를 쓰려면 NOSLIP_DASHBOARD_URL 환경변수로 실행하세요.",
  });
  if (response === 0) {
    dashboardUrl = "http://localhost:3000";
    loadDashboard();
  }
}

function buildMenu() {
  const isMac = process.platform === "darwin";
  const template = [
    ...(isMac ? [{
      label: app.name,
      submenu: [
        { role: "about" }, { type: "separator" },
        { role: "hide" }, { role: "quit" },
      ],
    }] : []),
    {
      label: "Dashboard",
      submenu: [
        { label: "새로고침 / 재연결", accelerator: "CmdOrCtrl+R", click: loadDashboard },
        { label: "Dashboard URL 설정...", click: setDashboardUrl },
        { type: "separator" },
        {
          label: "GitHub 저장소 열기",
          click: () => shell.openExternal("https://github.com/Futuremine97/noslip_quant_bot"),
        },
        ...(isMac ? [] : [{ type: "separator" }, { role: "quit" }]),
      ],
    },
    {
      label: "View",
      submenu: [
        { role: "zoomIn" }, { role: "zoomOut" }, { role: "resetZoom" },
        { type: "separator" }, { role: "togglefullscreen" },
        { role: "toggleDevTools" },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1380,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: "#0a0a0a",
    title: "No Slip Quant",
    icon: path.join(__dirname, "build", "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // External links -> system browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  loadDashboard();
  mainWindow.on("closed", () => { mainWindow = null; });
}

app.whenReady().then(() => {
  buildMenu();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
