#!/usr/bin/env node
/**
 * noslip setup  — Interactive broker API key registration wizard
 *
 * Security design
 * ───────────────
 * • All secret inputs use terminal raw-mode so characters are never echoed.
 * • Secrets are NEVER printed, logged, or stored anywhere except the target
 *   .env file on disk (mode 0600).
 * • Non-secret fields (client IDs, URLs, flags) are echoed normally.
 * • The wizard reads the existing .env, merges only the fields the user
 *   provided, and writes back atomically (temp-file rename).
 * • .gitignore is checked; if .env is not ignored, the user is warned.
 */

'use strict';

const fs   = require('fs');
const path = require('path');
const os   = require('os');
const tty  = require('tty');
const readline = require('readline');

const ROOT    = path.resolve(__dirname, '..');
const ENV_FILE = path.join(ROOT, '.env');

// ─────────────────────────── colour helpers ──────────────────────────────────
const C = {
  reset  : '\x1b[0m',
  bold   : '\x1b[1m',
  dim    : '\x1b[2m',
  cyan   : '\x1b[36m',
  green  : '\x1b[32m',
  yellow : '\x1b[33m',
  red    : '\x1b[31m',
  magenta: '\x1b[35m',
  blue   : '\x1b[34m',
};
const c  = (code, s) => `${code}${s}${C.reset}`;
const bold= s => c(C.bold,    s);
const dim = s => c(C.dim,     s);
const ok  = s => c(C.green,   s);
const warn= s => c(C.yellow,  s);
const err = s => c(C.red,     s);
const hi  = s => c(C.cyan,    s);
const sec = s => c(C.magenta, s);

// ────────────────────────── broker definitions ───────────────────────────────
// Each broker has:
//   id          – internal key & env prefix
//   label       – display name
//   apiUrl      – official Open API doc URL shown to the user
//   fields      – ordered list of env vars to collect
//     name      – env var name (without prefix)
//     label     – human label
//     secret    – if true → hidden input
//     optional  – if true → user can skip
//     default   – pre-filled default value (shown in prompt)
//     hint      – one-line hint shown below the prompt

const BROKERS = [
  {
    id   : 'toss',
    label: 'Toss Securities (토스증권)',
    apiUrl: 'https://openapi.tossinvest.com/docs',
    fields: [
      { name:'TOSS_SECURITIES_MODE',      label:'운용 모드',           default:'read_only',
        hint: `read_only | paper | live  (처음엔 read_only 권장)` },
      { name:'TOSS_SECURITIES_CLIENT_ID', label:'Client ID',           hint:'Open API 콘솔 → 앱 관리 → Client ID' },
      { name:'TOSS_SECURITIES_CLIENT_SECRET', label:'Client Secret',   secret:true,
        hint:'절대 타인에게 공유 금지' },
      { name:'TOSS_SECURITIES_ACCOUNT_SEQ', label:'계좌 SEQ (선택)', optional:true,
        hint:'빈값이면 첫 번째 계좌를 자동 선택' },
      { name:'TOSS_SECURITIES_ALLOW_LIVE_ORDERS', label:'실거래 주문 허용',
        default:'false', hint:'true 로 바꾸면 실거래 가능. 신중하게 설정하세요.' },
    ],
  },
  {
    id   : 'kis',
    label: 'Korea Investment & Securities (한국투자증권 KIS)',
    apiUrl: 'https://apiportal.koreainvestment.com/',
    fields: [
      { name:'KIS_SECURITIES_MODE',          label:'운용 모드',   default:'read_only',
        hint:'read_only | paper | live' },
      { name:'KIS_SECURITIES_APP_KEY',        label:'App Key',    hint:'KIS Open Trading API 포털 → 앱 등록' },
      { name:'KIS_SECURITIES_APP_SECRET',     label:'App Secret', secret:true },
      { name:'KIS_SECURITIES_ACCOUNT_NUMBER', label:'계좌번호',    hint:'8자리 앞자리 (예: 50071234)' },
      { name:'KIS_SECURITIES_ACCOUNT_SUFFIX', label:'계좌 상품코드', default:'01',
        hint:'01 = 종합계좌 (기본값)' },
    ],
  },
  {
    id   : 'kiwoom',
    label: '키움증권 (Kiwoom Securities)',
    apiUrl: 'https://openapi.kiwoom.com/',
    fields: [
      { name:'KIWOOM_SECURITIES_MODE',   label:'운용 모드',   default:'read_only' },
      { name:'KIWOOM_APP_KEY',            label:'App Key',    hint:'Open API+ 앱 등록 후 발급' },
      { name:'KIWOOM_APP_SECRET',         label:'App Secret', secret:true },
      { name:'KIWOOM_ACCOUNT_NUMBER',     label:'계좌번호',    hint:'10자리 전체 (예: 5007123401)' },
    ],
  },
  {
    id   : 'kb',
    label: 'KB증권 (KB Securities)',
    apiUrl: 'https://openapi.kbsec.com/',
    fields: [
      { name:'KB_SECURITIES_MODE',        label:'운용 모드',   default:'read_only' },
      { name:'KB_SECURITIES_APP_KEY',     label:'App Key' },
      { name:'KB_SECURITIES_APP_SECRET',  label:'App Secret', secret:true },
      { name:'KB_SECURITIES_ACCOUNT_NUMBER', label:'계좌번호', hint:'10자리' },
    ],
  },
  {
    id   : 'shinhan',
    label: '신한투자증권 (Shinhan)',
    apiUrl: 'https://openapi.shinhaninvest.com/',
    fields: [
      { name:'SHINHAN_SECURITIES_MODE',       label:'운용 모드', default:'read_only' },
      { name:'SHINHAN_SECURITIES_APP_KEY',    label:'App Key' },
      { name:'SHINHAN_SECURITIES_APP_SECRET', label:'App Secret', secret:true },
      { name:'SHINHAN_SECURITIES_ACCOUNT_NUMBER', label:'계좌번호' },
    ],
  },
  {
    id   : 'nh',
    label: 'NH투자증권 (NH Securities)',
    apiUrl: 'https://developers.nhqv.com/',
    fields: [
      { name:'NH_SECURITIES_MODE',        label:'운용 모드', default:'read_only' },
      { name:'NH_SECURITIES_APP_KEY',     label:'App Key' },
      { name:'NH_SECURITIES_APP_SECRET',  label:'App Secret', secret:true },
      { name:'NH_SECURITIES_ACCOUNT_NUMBER', label:'계좌번호' },
    ],
  },
  {
    id   : 'hana',
    label: '하나증권 (Hana Securities)',
    apiUrl: 'https://openapi.hanaw.com/',
    fields: [
      { name:'HANA_SECURITIES_MODE',        label:'운용 모드', default:'read_only' },
      { name:'HANA_SECURITIES_APP_KEY',     label:'App Key' },
      { name:'HANA_SECURITIES_APP_SECRET',  label:'App Secret', secret:true },
      { name:'HANA_SECURITIES_ACCOUNT_NUMBER', label:'계좌번호' },
    ],
  },
  {
    id   : 'yuanta',
    label: '유안타증권 (Yuanta) — Windows COM Bridge',
    apiUrl: 'https://myasset.yuantakorea.com/',
    fields: [
      { name:'YUANTA_SECURITIES_MODE',   label:'운용 모드', default:'disabled',
        hint:'disabled | mock | live  (Windows COM 브리지 필요)' },
      { name:'YUANTA_BRIDGE_URL',        label:'브리지 URL', default:'http://127.0.0.1:8765',
        hint:'Windows PC에서 bridge 서버를 먼저 실행해야 합니다' },
      { name:'YUANTA_BRIDGE_TOKEN',      label:'브리지 Bearer Token (32자+)', secret:true,
        hint:'bridge 서버 시작 시 생성한 토큰과 동일하게 입력' },
    ],
  },
];

// ───────────────────────── .env read/write ───────────────────────────────────

function readEnv(file) {
  const map = new Map();   // preserves insertion order
  const lines = [];        // raw lines for non-key content
  if (!fs.existsSync(file)) return { map, lines: [] };

  const raw = fs.readFileSync(file, 'utf8').split('\n');
  for (const line of raw) {
    const m = line.match(/^([A-Z0-9_]+)\s*=\s*(.*)/);
    if (m) {
      map.set(m[1], m[2]);
    }
    lines.push(line);
  }
  return { map, lines };
}

function writeEnv(file, map) {
  // Build .env text: keep existing non-key lines, update/append key lines.
  const existing = readEnv(file);
  const seen = new Set();

  // Update existing lines
  const updatedLines = existing.lines.map(line => {
    const m = line.match(/^([A-Z0-9_]+)\s*=(.*)/);
    if (m && map.has(m[1])) {
      seen.add(m[1]);
      return `${m[1]}=${map.get(m[1])}`;
    }
    return line;
  });

  // Append new keys that weren't in the file
  for (const [k, v] of map.entries()) {
    if (!seen.has(k)) {
      updatedLines.push(`${k}=${v}`);
    }
  }

  const content = updatedLines.join('\n');

  // Atomic write via temp file
  const tmp = file + '.setup_tmp_' + Date.now();
  fs.writeFileSync(tmp, content, { mode: 0o600, encoding: 'utf8' });
  fs.renameSync(tmp, file);
  // Ensure 0600 after rename (some OSes preserve tmp mode; double-check)
  try { fs.chmodSync(file, 0o600); } catch(_) {}
}

// ────────────────────────── gitignore check ──────────────────────────────────

function isEnvIgnored() {
  const gi = path.join(ROOT, '.gitignore');
  if (!fs.existsSync(gi)) return false;
  const lines = fs.readFileSync(gi, 'utf8').split('\n').map(l => l.trim());
  return lines.some(l => l === '.env' || l === '/.env');
}

// ────────────────────────── secret input ─────────────────────────────────────
// Reads a line from stdin without echoing characters (raw TTY mode).

function readSecret(promptText) {
  return new Promise(resolve => {
    const fd = fs.openSync('/dev/tty', 'r+');
    const stream = new tty.ReadStream(fd);
    stream.setRawMode(true);

    process.stdout.write(promptText);

    let buf = '';
    stream.on('data', chunk => {
      const ch = chunk.toString('utf8');
      if (ch === '\r' || ch === '\n') {
        stream.setRawMode(false);
        stream.destroy();
        fs.closeSync(fd);
        process.stdout.write('\n');
        resolve(buf);
      } else if (ch === '') { // Ctrl-C
        stream.setRawMode(false);
        stream.destroy();
        fs.closeSync(fd);
        process.stdout.write('\n');
        process.exit(130);
      } else if (ch === '' || ch === '\b') { // backspace
        if (buf.length > 0) {
          buf = buf.slice(0, -1);
          process.stdout.write('\b \b');
        }
      } else {
        buf += ch;
        process.stdout.write('*');
      }
    });
  });
}

// ────────────────────────── normal input ─────────────────────────────────────

function readLine(promptText, defaultVal) {
  return new Promise(resolve => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: true });
    const display = defaultVal ? `${promptText} ${dim(`[${defaultVal}]`)} : ` : `${promptText}: `;
    rl.question(display, answer => {
      rl.close();
      resolve(answer.trim() || defaultVal || '');
    });
  });
}

// ────────────────────────── single-key confirm ───────────────────────────────

function confirm(promptText) {
  return new Promise(resolve => {
    process.stdout.write(`${promptText} ${dim('[y/N]')}: `);
    const fd = fs.openSync('/dev/tty', 'r+');
    const stream = new tty.ReadStream(fd);
    stream.setRawMode(true);
    stream.once('data', chunk => {
      stream.setRawMode(false);
      stream.destroy();
      fs.closeSync(fd);
      const ch = chunk.toString('utf8').toLowerCase();
      process.stdout.write(ch + '\n');
      resolve(ch === 'y');
    });
  });
}

// ────────────────────────── broker menu ──────────────────────────────────────

async function selectBrokers() {
  console.log('\n' + bold('등록할 증권사를 선택하세요') + '  (숫자 입력 후 Enter, 여러 개 스페이스로 구분, 0=전체)\n');
  BROKERS.forEach((b, i) => {
    console.log(`  ${hi(String(i + 1).padStart(2))}. ${b.label}`);
  });
  console.log();

  const answer = await readLine('선택', '');
  if (!answer || answer.trim() === '0') return BROKERS;

  const indices = answer.split(/[\s,]+/).map(s => parseInt(s, 10) - 1).filter(i => i >= 0 && i < BROKERS.length);
  if (indices.length === 0) {
    console.log(warn('⚠  선택 없음 — 전체 증권사를 등록합니다.'));
    return BROKERS;
  }
  return indices.map(i => BROKERS[i]);
}

// ────────────────────────── field wizard ─────────────────────────────────────

async function collectBroker(broker, currentMap) {
  console.log('\n' + '─'.repeat(60));
  console.log(bold(`  🏦  ${broker.label}`));
  console.log(dim(`  📄  API 문서: ${broker.apiUrl}`));
  console.log('─'.repeat(60) + '\n');

  const collected = new Map();

  for (const f of broker.fields) {
    const existing = currentMap.get(f.name) || '';
    // Don't re-prompt for already-set secrets unless user wants to update
    if (f.secret && existing && existing !== '' && !existing.startsWith('replace_with')) {
      const update = await confirm(`  ${sec('🔑')} ${f.label} 이미 설정됨. 업데이트하시겠습니까?`);
      if (!update) { collected.set(f.name, existing); continue; }
    }

    if (f.hint) console.log(dim(`     힌트: ${f.hint}`));

    let value;
    if (f.secret) {
      value = await readSecret(`  ${sec('🔒')} ${bold(f.label)} (입력 숨김): `);
    } else {
      const defaultVal = existing && !existing.startsWith('your_') && !existing.startsWith('replace_with')
        ? existing
        : (f.default || '');
      value = await readLine(`  ${hi('→')} ${f.label}`, defaultVal);
    }

    if (!value && f.optional) {
      console.log(dim('     (건너뜀)'));
      collected.set(f.name, existing || '');
    } else if (!value && !f.optional) {
      console.log(warn('     ⚠  비워둠 — 나중에 .env에서 직접 설정하세요.'));
      collected.set(f.name, '');
    } else {
      collected.set(f.name, value);
    }
  }

  return collected;
}

// ────────────────────────── summary (no secrets) ─────────────────────────────

function printSummary(broker, collected) {
  console.log('\n' + ok('  ✅  저장 미리보기 (시크릿은 마스킹):'));
  for (const f of broker.fields) {
    const v = collected.get(f.name) || '';
    const display = f.secret
      ? (v ? '••••••••  (설정됨)' : dim('(빈값)'))
      : (v || dim('(빈값)'));
    console.log(`     ${f.name.padEnd(42)} = ${display}`);
  }
}

// ────────────────────────── main wizard ──────────────────────────────────────

async function runSetup(targetBroker) {
  console.clear();
  console.log('\n' + bold(hi('  NoSlip Quant — 증권사 OPEN API 등록 마법사')));
  console.log(dim('  입력한 API 키는 .env 파일에만 저장됩니다.'));
  console.log(dim('  시크릿은 화면에 표시되지 않으며, 로그·커밋에 포함되지 않습니다.\n'));

  // .gitignore safety check
  if (!isEnvIgnored()) {
    console.log(err('  ⚠  경고: .gitignore에 .env가 없습니다!'));
    console.log(warn('  git에 커밋되지 않도록 .gitignore에 .env를 추가하세요.\n'));
    const cont = await confirm('  그래도 계속하시겠습니까?');
    if (!cont) { console.log('중단.'); process.exit(0); }
  }

  // Select brokers
  let brokersToSetup;
  if (targetBroker) {
    const found = BROKERS.find(b => b.id === targetBroker.toLowerCase());
    if (!found) {
      console.log(err(`  알 수 없는 증권사: ${targetBroker}`));
      console.log('  사용 가능:', BROKERS.map(b => b.id).join(', '));
      process.exit(1);
    }
    brokersToSetup = [found];
  } else {
    brokersToSetup = await selectBrokers();
  }

  // Read current .env
  const { map: currentMap } = readEnv(ENV_FILE);
  const updates = new Map();

  // Collect each broker
  for (const broker of brokersToSetup) {
    const collected = await collectBroker(broker, currentMap);
    printSummary(broker, collected);
    const save = await confirm('\n  이 내용을 .env에 저장하시겠습니까?');
    if (save) {
      for (const [k, v] of collected.entries()) updates.set(k, v);
      console.log(ok('  💾 저장 예약됨.'));
    } else {
      console.log(dim('  건너뜀.'));
    }
  }

  if (updates.size === 0) {
    console.log('\n' + warn('  변경 사항 없음. 종료합니다.'));
    process.exit(0);
  }

  // Merge and write
  writeEnv(ENV_FILE, updates);

  console.log('\n' + ok(`  ✅  .env 업데이트 완료  (${ENV_FILE})`));
  console.log(dim('  파일 권한: 600 (소유자만 읽기/쓰기)'));
  console.log('\n' + bold('  다음 단계:'));
  console.log('   1. ' + hi('noslip broker <증권사>') + '  로 연결 상태를 확인하세요.');
  console.log('   2. 모드를 read_only 에서 시작해 충분히 테스트 후 live 로 변경하세요.');
  console.log('   3. .env를 절대 git에 커밋하지 마세요.\n');
}

// ─────────────────────────── entry ───────────────────────────────────────────

module.exports = { runSetup };

if (require.main === module) {
  const target = process.argv[2]; // optional: specific broker id
  runSetup(target).catch(e => {
    console.error(err('\n오류: ') + e.message);
    process.exit(1);
  });
}
