#!/usr/bin/env node

const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const rootDir = path.resolve(__dirname, '..');
const packageJson = require(path.join(rootDir, 'package.json'));
const version = packageJson.version;

// Resolve Python Binary
const venvPython = path.join(rootDir, 'services', 'trader', '.venv', 'bin', 'python');
const pythonBin = fs.existsSync(venvPython) ? venvPython : 'python3';

const args = process.argv.slice(2);
const command = args[0];

const usage = `
\x1b[1m\x1b[36mNoSlip Quant CLI\x1b[0m - Version \x1b[32m${version}\x1b[0m

\x1b[1mUSAGE:\x1b[0m
  noslip <command> [options]

\x1b[1mCOMMANDS:\x1b[0m
  \x1b[33mstart\x1b[0m             Next.js 대시보드 및 API 프록시 서버를 시작합니다.
  \x1b[33mbot\x1b[0m               텔레그램 인터랙티브 퀀트 봇 데몬을 구동합니다.
  \x1b[33mcardnews\x1b[0m          AI 다국어 카드뉴스를 생성하고 ZIP 파일로 압축합니다.
                    \x1b[36m--topic "<주제>"\x1b[0m (필수)
                    \x1b[36m--lang <ko|ja>\x1b[0m (기본값: ko)
  \x1b[33manalyze\x1b[0m <티커>      지정된 종목에 대해 6-Agent 컨센서스 신호 분석을 구동합니다.
  \x1b[33mportfolio\x1b[0m         S&P500 가상 자산 포트폴리오의 손익 및 포지션 현황을 출력합니다.
  \x1b[33mbroker\x1b[0m [브로커]     연동된 증권사(toss, kb, kis, kiwoom 등)의 API 연결 상태를 진단합니다.
  \x1b[33mversion, -v\x1b[0m       앱의 버전을 출력합니다.
  \x1b[33mhelp, -h\x1b[0m          이 도움말을 출력합니다.

\x1b[1mEXAMPLES:\x1b[0m
  noslip start
  noslip analyze AAPL
  noslip cardnews --topic "블록체인 전망" --lang ja
  noslip portfolio
  noslip broker kb
`;

if (!command || command === 'help' || command === '-h' || command === '--help') {
  console.log(usage);
  process.exit(0);
}

if (command === 'version' || command === '-v' || command === '--version') {
  console.log(`v${version}`);
  process.exit(0);
}

switch (command) {
  case 'start': {
    console.log('🚀 Starting NoSlip dashboard and API servers...');
    const apiPath = path.resolve(rootDir, 'server', 'index.js');
    const nextBinPath = path.resolve(rootDir, 'node_modules', 'next', 'dist', 'bin', 'next');

    const apiProcess = spawn(process.execPath, [apiPath], { stdio: 'inherit', env: { ...process.env, PORT: '8787' } });
    const nextProcess = spawn(process.execPath, [nextBinPath, 'start', '-p', '3000'], { stdio: 'inherit', env: { ...process.env } });

    const cleanExit = () => {
      apiProcess.kill();
      nextProcess.kill();
      process.exit();
    };

    process.on('SIGINT', cleanExit);
    process.on('SIGTERM', cleanExit);
    break;
  }

  case 'bot': {
    console.log('🤖 Starting Telegram interactive bot daemon...');
    const botScript = path.resolve(rootDir, 'services', 'trader', 'telegram_interactive_bot.py');
    const botProcess = spawn(pythonBin, [botScript], { stdio: 'inherit', cwd: path.join(rootDir, 'services', 'trader') });
    break;
  }

  case 'cardnews': {
    const topicIndex = args.indexOf('--topic');
    if (topicIndex === -1 || !args[topicIndex + 1]) {
      console.error('❌ Error: --topic <주제> 인자가 필요합니다.');
      console.log(usage);
      process.exit(1);
    }
    const topic = args[topicIndex + 1];

    let lang = 'ko';
    const langIndex = args.indexOf('--lang');
    if (langIndex !== -1 && args[langIndex + 1]) {
      lang = args[langIndex + 1];
    }

    console.log(`🎨 Generating cardnews for topic: "${topic}" (language: ${lang})...`);
    const cardScript = path.resolve(rootDir, 'services', 'trader', 'daily_card_news.py');
    const cardProcess = spawn(pythonBin, [cardScript, '--topic', topic, '--lang', lang], { stdio: 'inherit', cwd: path.join(rootDir, 'services', 'trader') });
    break;
  }

  case 'analyze': {
    const symbol = args[1];
    if (!symbol) {
      console.error('❌ Error: 분석할 티커(예: AAPL)를 입력해주세요.');
      console.log(usage);
      process.exit(1);
    }
    console.log(`📊 Running 6-Agent consensus analysis for symbol: ${symbol.toUpperCase()}...`);
    const predictScript = path.resolve(rootDir, 'services', 'trader', 'predict_signal.py');
    const predictProcess = spawn(pythonBin, [predictScript, '--symbol', symbol.toUpperCase()], { stdio: 'inherit', cwd: path.join(rootDir, 'services', 'trader') });
    break;
  }

  case 'portfolio': {
    console.log('💼 Fetching S&P500 virtual trading portfolio summary...');
    const pyCode = `import sys; sys.path.insert(0, 'services/trader'); from telegram_interactive_bot import execute_portfolio_summary; print(execute_portfolio_summary())`;
    const portfolioProcess = spawn(pythonBin, ['-c', pyCode], { stdio: 'inherit', cwd: rootDir });
    break;
  }

  case 'broker': {
    const provider = args[1] || '';
    console.log(`🏦 Fetching broker status ${provider ? `for: ${provider}` : ''}...`);
    const pyCode = `import sys; sys.path.insert(0, 'services/trader'); from brokers.service import broker_status; import json; print(json.dumps(broker_status('${provider}'), indent=2, ensure_ascii=False))`;
    const brokerProcess = spawn(pythonBin, ['-c', pyCode], { stdio: 'inherit', cwd: rootDir });
    break;
  }

  default: {
    console.error(`❌ Unknown command: ${command}`);
    console.log(usage);
    process.exit(1);
  }
}
