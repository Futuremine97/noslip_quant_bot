// macOS 메뉴바(트레이) 앱.
// 트레이 아이콘 + 드롭다운: 상태 표시, 관리 화면 바로가기, 서비스 재시작, 종료.
const { Tray, Menu, nativeImage, shell } = require('electron');
const path = require('path');

let tray = null;
let lastStatus = { dashboard: false, api: false };
let handlers = {};

const SHORTCUTS = [
  { label: '개요', route: '/manage' },
  { label: 'Purpose 전략', route: '/manage/purpose' },
  { label: '멀티봇', route: '/manage/bots' },
  { label: '연합', route: '/manage/federation' },
  { label: '채팅', route: '/manage/chat' },
  { label: 'MCP 서버', route: '/manage/mcp' },
];

function dot(ok) {
  return ok ? '🟢' : '🔴';
}

function buildMenu() {
  const { dashboard, api } = lastStatus;
  return Menu.buildFromTemplate([
    { label: 'NoSlip Quant', enabled: false },
    { type: 'separator' },
    { label: `${dot(dashboard)} 대시보드  ${dashboard ? '실행 중' : '꺼짐'} (:3000)`, enabled: false },
    { label: `${dot(api)} 컨트롤 플레인  ${api ? '실행 중' : '꺼짐'} (:8787)`, enabled: false },
    { type: 'separator' },
    {
      label: '대시보드 열기',
      accelerator: 'CmdOrCtrl+D',
      click: () => handlers.openRoute && handlers.openRoute('/manage'),
    },
    {
      label: '바로가기',
      submenu: SHORTCUTS.map((s) => ({
        label: s.label,
        click: () => handlers.openRoute && handlers.openRoute(s.route),
      })),
    },
    { type: 'separator' },
    {
      label: '서비스 재시작',
      click: () => handlers.restartServices && handlers.restartServices(),
    },
    {
      label: '상태 새로고침',
      click: () => handlers.refreshStatus && handlers.refreshStatus(),
    },
    { type: 'separator' },
    {
      label: 'GitHub 저장소',
      click: () => shell.openExternal('https://github.com/Futuremine97/noslip_quant_bot'),
    },
    { type: 'separator' },
    { label: '종료', role: 'quit' },
  ]);
}

function refreshMenu() {
  if (!tray) return;
  tray.setContextMenu(buildMenu());
  tray.setToolTip(
    `NoSlip Quant — 대시보드 ${lastStatus.dashboard ? 'ON' : 'OFF'} / API ${lastStatus.api ? 'ON' : 'OFF'}`,
  );
}

function createTray(h) {
  handlers = h || {};
  const iconPath = path.join(__dirname, 'trayTemplate.png');
  const image = nativeImage.createFromPath(iconPath);
  image.setTemplateImage(true); // macOS 다크/라이트 메뉴바 자동 대응
  tray = new Tray(image);
  // 좌클릭 시 대시보드 바로 열기(메뉴는 우클릭/클릭 모두 표시)
  tray.on('click', () => handlers.openRoute && handlers.openRoute('/manage'));
  refreshMenu();
  return tray;
}

function setStatus(status) {
  lastStatus = { ...lastStatus, ...status };
  refreshMenu();
}

module.exports = { createTray, setStatus };
