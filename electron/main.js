const { app, BrowserWindow } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

let mainWindow;
let nextProcess;
let apiProcess;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    title: "NoSlip Quant",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    }
  });

  mainWindow.loadURL('http://localhost:3000');

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function startServices() {
  console.log("Starting backend API server...");
  const apiPath = path.resolve(__dirname, '..', 'server', 'index.js');
  apiProcess = spawn(process.execPath, [apiPath], {
    env: { ...process.env, PORT: '8787' }
  });

  apiProcess.stdout.on('data', (data) => {
    console.log(`[API]: ${data}`);
  });

  apiProcess.stderr.on('data', (data) => {
    console.error(`[API Error]: ${data}`);
  });

  console.log("Starting Next.js production server...");
  const nextBinPath = path.resolve(__dirname, '..', 'node_modules', 'next', 'dist', 'bin', 'next');
  nextProcess = spawn(process.execPath, [nextBinPath, 'start', '-p', '3000'], {
    env: { ...process.env }
  });

  nextProcess.stdout.on('data', (data) => {
    console.log(`[Next]: ${data}`);
  });

  nextProcess.stderr.on('data', (data) => {
    console.error(`[Next Error]: ${data}`);
  });
}

app.on('ready', () => {
  startServices();
  
  setTimeout(() => {
    createWindow();
  }, 4000);
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('quit', () => {
  console.log("Stopping servers...");
  if (apiProcess) apiProcess.kill();
  if (nextProcess) nextProcess.kill();
});

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});
