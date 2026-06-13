// 터미널 통합 수집·제어 모듈 (macOS).
// 대상: tmux, cmux, Terminal.app, iTerm2.
//  - 수집: 세션/창/탭/페인 목록 + 마지막 줄 미리보기
//  - 제어: 명령 전송(send), 포커스(focus), 종료(kill), 새 세션(newSession)
//
// 안전: 모든 외부 호출은 execFile(셸 미사용)로 인자 배열 전달 → 인젝션 차단.
//      Terminal/iTerm 제어는 macOS 자동화(Automation) 권한이 필요하다(최초 1회 동의).
const { execFile } = require('child_process');

const RS = '\x1e'; // record separator
const US = '\x1f'; // unit separator

function run(file, args, { timeout = 4000, input } = {}) {
  return new Promise((resolve) => {
    const child = execFile(file, args, { timeout, maxBuffer: 1024 * 1024 }, (err, stdout, stderr) => {
      resolve({ ok: !err, stdout: (stdout || '').toString(), stderr: (stderr || '').toString() });
    });
    if (input != null && child.stdin) {
      child.stdin.end(input);
    }
  });
}

async function which(bin) {
  const r = await run('/usr/bin/which', [bin], { timeout: 1500 });
  return r.ok && r.stdout.trim() ? r.stdout.trim() : null;
}

async function isRunning(procName) {
  const r = await run('/usr/bin/pgrep', ['-x', procName], { timeout: 1500 });
  return r.ok && r.stdout.trim().length > 0;
}

function lastLine(text) {
  const lines = (text || '').split('\n').map((l) => l.replace(/\s+$/, '')).filter((l) => l.trim() !== '');
  return lines.length ? lines[lines.length - 1].slice(0, 160) : '';
}

function esc(s) {
  return String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

// ───────────────────────────── tmux ─────────────────────────────
async function collectTmux() {
  if (!(await which('tmux'))) return [];
  const fmt = ['#{session_name}', '#{window_index}', '#{window_name}',
    '#{pane_index}', '#{pane_id}', '#{pane_active}', '#{pane_current_command}'].join(US);
  const r = await run('tmux', ['list-panes', '-a', '-F', fmt], { timeout: 3000 });
  if (!r.ok || !r.stdout.trim()) return [];
  const items = [];
  const rows = r.stdout.trim().split('\n').slice(0, 40);
  for (const row of rows) {
    const [session, win, winName, pane, paneId, active, cmd] = row.split(US);
    let preview = '';
    const cap = await run('tmux', ['capture-pane', '-p', '-t', paneId, '-S', '-2'], { timeout: 2000 });
    if (cap.ok) preview = lastLine(cap.stdout);
    items.push({
      source: 'tmux',
      key: `tmux:${paneId}`,
      title: `${session}:${win}.${pane} ${winName || ''}`.trim(),
      subtitle: cmd || '',
      active: active === '1',
      preview,
      ref: { paneId, session, window: win },
    });
  }
  return items;
}

// ───────────────────────────── cmux ─────────────────────────────
async function collectCmux() {
  const bin = await which('cmux');
  if (!bin) return [];
  // cmux는 tmux 호환 list-sessions를 지원하는 경우가 있어 best-effort로 시도.
  const r = await run('cmux', ['list-sessions', '-F', `#{session_name}${US}#{windows}`], { timeout: 2500 });
  if (r.ok && r.stdout.trim()) {
    return r.stdout.trim().split('\n').slice(0, 30).map((line, i) => {
      const [name, windows] = line.split(US);
      return {
        source: 'cmux',
        key: `cmux:${name || i}`,
        title: name || `session ${i}`,
        subtitle: windows ? `${windows} windows` : '',
        active: false,
        preview: '',
        ref: { session: name },
      };
    });
  }
  // CLI 형식 미상 → 감지만 표시(제어는 추후).
  return [{
    source: 'cmux',
    key: 'cmux:detected',
    title: 'cmux 감지됨',
    subtitle: 'CLI 목록 형식 미상 — 제어 미연동',
    active: false,
    preview: '',
    ref: null,
    readOnly: true,
  }];
}

// ───────────────────────── Terminal.app ─────────────────────────
const TERMINAL_LIST_AS = `
on lastLine(t)
  set ls to ""
  repeat with p in paragraphs of t
    if (contents of p) is not "" then set ls to (contents of p)
  end repeat
  return ls
end lastLine
tell application "Terminal"
  set out to ""
  repeat with w in windows
    set wid to id of w
    set tlist to tabs of w
    repeat with i from 1 to count of tlist
      set t to item i of tlist
      set procName to ""
      try
        set procName to (item -1 of (processes of t))
      end try
      set ln to ""
      try
        set ln to my lastLine(contents of t)
      end try
      set isBusy to "0"
      try
        if busy of t then set isBusy to "1"
      end try
      set out to out & wid & "${US}" & i & "${US}" & procName & "${US}" & isBusy & "${US}" & ln & "${RS}"
    end repeat
  end repeat
  return out
end tell`;

async function collectTerminalApp() {
  if (!(await isRunning('Terminal'))) return [];
  const r = await run('osascript', ['-e', TERMINAL_LIST_AS], { timeout: 5000 });
  if (!r.ok || !r.stdout.trim()) return [];
  return r.stdout.split(RS).filter((x) => x.trim()).slice(0, 40).map((rec) => {
    const [wid, tab, proc, busy, ln] = rec.split(US);
    return {
      source: 'Terminal',
      key: `terminal:${wid}:${tab}`,
      title: `Window ${wid} · Tab ${tab}`,
      subtitle: proc || 'shell',
      active: busy === '1',
      preview: (ln || '').slice(0, 160),
      ref: { windowId: wid, tab: parseInt(tab, 10) },
    };
  });
}

// ───────────────────────────── iTerm2 ─────────────────────────────
const ITERM_LIST_AS = `
on lastLine(t)
  set ls to ""
  repeat with p in paragraphs of t
    if (contents of p) is not "" then set ls to (contents of p)
  end repeat
  return ls
end lastLine
tell application "iTerm"
  set out to ""
  repeat with w in windows
    set wid to id of w
    set tabIndex to 0
    repeat with tb in tabs of w
      set tabIndex to tabIndex + 1
      repeat with s in sessions of tb
        set sid to id of s
        set sname to ""
        try
          set sname to name of s
        end try
        set ln to ""
        try
          set ln to my lastLine(text of s)
        end try
        set out to out & wid & "${US}" & tabIndex & "${US}" & sid & "${US}" & sname & "${US}" & ln & "${RS}"
      end repeat
    end repeat
  end repeat
  return out
end tell`;

async function collectITerm() {
  if (!(await isRunning('iTerm2'))) return [];
  const r = await run('osascript', ['-e', ITERM_LIST_AS], { timeout: 5000 });
  if (!r.ok || !r.stdout.trim()) return [];
  return r.stdout.split(RS).filter((x) => x.trim()).slice(0, 40).map((rec) => {
    const [wid, tab, sid, sname, ln] = rec.split(US);
    return {
      source: 'iTerm2',
      key: `iterm:${sid}`,
      title: sname || `Window ${wid} · Tab ${tab}`,
      subtitle: `tab ${tab}`,
      active: false,
      preview: (ln || '').slice(0, 160),
      ref: { windowId: wid, tab: parseInt(tab, 10), sessionId: sid },
    };
  });
}

// ───────────────────────────── 집계 ─────────────────────────────
async function listAll() {
  const [tmux, cmux, term, iterm] = await Promise.all([
    collectTmux().catch(() => []),
    collectCmux().catch(() => []),
    collectTerminalApp().catch(() => []),
    collectITerm().catch(() => []),
  ]);
  const groups = [
    { source: 'tmux', items: tmux },
    { source: 'cmux', items: cmux },
    { source: 'Terminal', items: term },
    { source: 'iTerm2', items: iterm },
  ];
  return { groups, total: tmux.length + cmux.length + term.length + iterm.length, ts: Date.now() };
}

// ───────────────────────────── 제어 ─────────────────────────────
async function sendCommand(item, command) {
  const cmd = String(command || '');
  if (!cmd) return { ok: false, error: '빈 명령' };
  switch (item.source) {
    case 'tmux':
    case 'cmux': {
      const bin = item.source === 'cmux' ? 'cmux' : 'tmux';
      const target = item.ref.paneId || item.ref.session;
      await run(bin, ['send-keys', '-t', target, '-l', cmd]);
      const r = await run(bin, ['send-keys', '-t', target, 'Enter']);
      return { ok: r.ok, error: r.stderr };
    }
    case 'Terminal': {
      const as = `tell application "Terminal" to do script "${esc(cmd)}" in tab ${item.ref.tab} of window id ${item.ref.windowId}`;
      const r = await run('osascript', ['-e', as]);
      return { ok: r.ok, error: r.stderr };
    }
    case 'iTerm2': {
      const as = `tell application "iTerm"
  repeat with w in windows
    repeat with tb in tabs of w
      repeat with s in sessions of tb
        if (id of s) is "${esc(item.ref.sessionId)}" then
          tell s to write text "${esc(cmd)}"
        end if
      end repeat
    end repeat
  end repeat
end tell`;
      const r = await run('osascript', ['-e', as]);
      return { ok: r.ok, error: r.stderr };
    }
    default:
      return { ok: false, error: '미지원 소스' };
  }
}

async function focus(item) {
  switch (item.source) {
    case 'tmux':
    case 'cmux': {
      const bin = item.source === 'cmux' ? 'cmux' : 'tmux';
      if (item.ref.session && item.ref.window != null) {
        await run(bin, ['select-window', '-t', `${item.ref.session}:${item.ref.window}`]);
      }
      if (item.ref.paneId) await run(bin, ['select-pane', '-t', item.ref.paneId]);
      return { ok: true };
    }
    case 'Terminal': {
      const as = `tell application "Terminal"
  activate
  set frontmost of window id ${item.ref.windowId} to true
  set selected of tab ${item.ref.tab} of window id ${item.ref.windowId} to true
end tell`;
      const r = await run('osascript', ['-e', as]);
      return { ok: r.ok, error: r.stderr };
    }
    case 'iTerm2': {
      const as = `tell application "iTerm"
  activate
  repeat with w in windows
    repeat with tb in tabs of w
      repeat with s in sessions of tb
        if (id of s) is "${esc(item.ref.sessionId)}" then
          select tb
          tell w to select
        end if
      end repeat
    end repeat
  end repeat
end tell`;
      const r = await run('osascript', ['-e', as]);
      return { ok: r.ok, error: r.stderr };
    }
    default:
      return { ok: false, error: '미지원 소스' };
  }
}

async function kill(item) {
  switch (item.source) {
    case 'tmux':
    case 'cmux': {
      const bin = item.source === 'cmux' ? 'cmux' : 'tmux';
      const r = await run(bin, ['kill-pane', '-t', item.ref.paneId || item.ref.session]);
      return { ok: r.ok, error: r.stderr };
    }
    case 'Terminal': {
      const as = `tell application "Terminal" to close tab ${item.ref.tab} of window id ${item.ref.windowId}`;
      const r = await run('osascript', ['-e', as]);
      return { ok: r.ok, error: r.stderr };
    }
    case 'iTerm2': {
      const as = `tell application "iTerm"
  repeat with w in windows
    repeat with tb in tabs of w
      repeat with s in sessions of tb
        if (id of s) is "${esc(item.ref.sessionId)}" then close s
      end repeat
    end repeat
  end repeat
end tell`;
      const r = await run('osascript', ['-e', as]);
      return { ok: r.ok, error: r.stderr };
    }
    default:
      return { ok: false, error: '미지원 소스' };
  }
}

async function newSession(kind, name) {
  const n = (name || `noslip-${Date.now().toString().slice(-4)}`).replace(/[^a-zA-Z0-9_-]/g, '');
  if (kind === 'tmux' || kind === 'cmux') {
    const bin = kind === 'cmux' ? 'cmux' : 'tmux';
    if (!(await which(bin))) return { ok: false, error: `${bin} 미설치` };
    const r = await run(bin, ['new-session', '-d', '-s', n]);
    return { ok: r.ok, error: r.stderr, name: n };
  }
  if (kind === 'Terminal') {
    const r = await run('osascript', ['-e', 'tell application "Terminal" to do script ""']);
    return { ok: r.ok, error: r.stderr };
  }
  if (kind === 'iTerm2') {
    const r = await run('osascript', ['-e', 'tell application "iTerm" to create window with default profile']);
    return { ok: r.ok, error: r.stderr };
  }
  return { ok: false, error: '미지원' };
}

module.exports = { listAll, sendCommand, focus, kill, newSession };
