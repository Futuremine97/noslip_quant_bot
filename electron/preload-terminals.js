// 팝오버 렌더러 ↔ 메인 프로세스 IPC 브리지 (contextIsolation 안전).
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('terminalsAPI', {
  list: () => ipcRenderer.invoke('terminals:list'),
  send: (item, command) => ipcRenderer.invoke('terminals:send', { item, command }),
  focus: (item) => ipcRenderer.invoke('terminals:focus', { item }),
  kill: (item) => ipcRenderer.invoke('terminals:kill', { item }),
  newSession: (kind) => ipcRenderer.invoke('terminals:new', { kind }),
  openDashboard: () => ipcRenderer.invoke('terminals:openDashboard'),
  hide: () => ipcRenderer.invoke('terminals:hide'),
});
