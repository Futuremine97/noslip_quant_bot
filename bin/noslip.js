#!/usr/bin/env node

const { spawn, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const readline = require('readline');

const rootDir = path.resolve(__dirname, '..');
const packageJson = require(path.join(rootDir, 'package.json'));
const version = packageJson.version;

// Resolve Python Binary
const venvPython = path.join(rootDir, 'services', 'trader', '.venv', 'bin', 'python');
const pythonBin = fs.existsSync(venvPython) ? venvPython : 'python3';

const { runSetup } = require('./setup_broker');

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
  \x1b[33mprophet\x1b[0m <티커>      Prophet 시계열 예측 엔진을 구동하고 시각화 차트를 출력합니다.
                    \x1b[36m--days <일수>\x1b[0m (기본값: 30)
  \x1b[33mportfolio\x1b[0m         S&P500 가상 자산 포트폴리오의 손익 및 포지션 현황을 출력합니다.
  \x1b[33mbroker\x1b[0m [브로커]     연동된 증권사(toss, kb, kis, kiwoom 등)의 API 연결 상태를 진단합니다.
  \x1b[33msetup\x1b[0m [브로커]     증권사 OPEN API 키를 안전하게 .env에 등록하는 대화형 마법사를 실행합니다.
                    \x1b[36mnoslip setup\x1b[0m          → 전체 증권사 선택 메뉴
                    \x1b[36mnoslip setup toss\x1b[0m     → 특정 증권사만 등록 (kis, kiwoom, kb, shinhan, nh, hana, yuanta)
  \x1b[33mversion, -v\x1b[0m       앱의 버전을 출력합니다.
  \x1b[33mhelp, -h\x1b[0m          이 도움말을 출력합니다.

\x1b[1mEXAMPLES:\x1b[0m
  noslip start
  noslip analyze AAPL
  noslip prophet AAPL --days 30
  noslip cardnews --topic "블록체인 전망" --lang ja
  noslip portfolio
  noslip broker kb
`;

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function extractJSON(str) {
  try {
    return JSON.parse(str.trim());
  } catch (e) {
    const lines = str.split('\n');
    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
        try {
          return JSON.parse(trimmed);
        } catch (innerErr) {
          // ignore
        }
      }
    }
    const match = str.match(/\{[\s\S]*\}/);
    if (match) {
      try {
        return JSON.parse(match[0]);
      } catch (innerErr) {
        // ignore
      }
    }
    throw new Error("Could not find or parse valid JSON in the output.");
  }
}

function splitTerminal(commandToRun) {
  // Check if inside TMUX
  if (process.env.TMUX) {
    try {
      execSync(`tmux split-window -h "${commandToRun}"`);
      return true;
    } catch (e) {
      // ignore
    }
  }

  // Check if inside iTerm2 on macOS
  const isMac = process.platform === 'darwin';
  const termProg = process.env.TERM_PROGRAM || '';
  const isIterm = termProg === 'iTerm.app' || process.env.ITERM_SESSION_ID;

  if (isMac && isIterm) {
    try {
      const escapedCmd = commandToRun.replace(/"/g, '\\"');
      const appleScript = `
        tell application "iTerm"
          tell current session of current window
            set newSession to split vertically with default profile
            tell newSession
              write text "${escapedCmd}"
            end tell
          end tell
        end tell
      `;
      execSync('osascript', { input: appleScript, encoding: 'utf-8' });
      return true;
    } catch (e) {
      // ignore
    }
  }

  // If on macOS but standard Terminal.app, open a new window/tab running the command
  if (isMac) {
    try {
      const escapedCmd = commandToRun.replace(/"/g, '\\"');
      const appleScript = `
        tell application "Terminal"
          do script "${escapedCmd}"
        end tell
      `;
      execSync('osascript', { input: appleScript, encoding: 'utf-8' });
      return true;
    } catch (e) {
      // ignore
    }
  }

  return false;
}

async function playSurfingTangerine() {
  const leaf  = "    \x1b[32m🍃\x1b[0m";
  const top   = "  \x1b[38;5;208m.----.\x1b[0m";
  const body  = " \x1b[38;5;208m/  \x1b[30m◕ ◕\x1b[38;5;208m  \\\x1b[0m";
  const btm   = " \x1b[38;5;208m\\  \x1b[31m◡\x1b[38;5;208m  /\x1b[0m";
  const board = "\x1b[33m🏄 ════════\x1b[0m";

  // Hide cursor
  process.stdout.write('\x1b[?25l');

  const totalFrames = 15;
  const height = 7;

  for (let tick = 0; tick < totalFrames; tick++) {
    const x = Math.floor(10 + Math.sin(tick * 0.6) * 7);
    const y = tick % 2;

    const waveShift = tick % 3;
    let waveStr = "";
    if (waveShift === 0) {
      waveStr = "\x1b[34m🌊 ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ 🌊\x1b[0m";
    } else if (waveShift === 1) {
      waveStr = "\x1b[34m~ 🌊 ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ 🌊\x1b[0m";
    } else {
      waveStr = "\x1b[34m~ ~ 🌊 ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ 🌊\x1b[0m";
    }

    const pad = " ".repeat(x);
    let output = "";

    if (y === 0) {
      output += pad + leaf + "\n";
      output += pad + top + "\n";
      output += pad + body + "\n";
      output += pad + btm + "\n";
      output += pad + board + "\n";
      output += "\n";
    } else {
      output += "\n";
      output += pad + leaf + "\n";
      output += pad + top + "\n";
      output += pad + body + "\n";
      output += pad + btm + "\n";
      output += pad + board + "\n";
    }
    output += waveStr + "\n";

    process.stdout.write(output);

    await sleep(120);

    if (tick < totalFrames - 1) {
      readline.moveCursor(process.stdout, 0, -height);
      for (let i = 0; i < height; i++) {
        readline.clearLine(process.stdout, 0);
      }
    }
  }

  readline.moveCursor(process.stdout, 0, -height);
  for (let i = 0; i < height; i++) {
    readline.clearLine(process.stdout, 0);
    if (i < height - 1) {
      process.stdout.write('\n');
    }
  }
  readline.moveCursor(process.stdout, 0, -(height - 1));
  process.stdout.write('\x1b[?25h');
}

async function main() {
  if (command === 'version' || command === '-v' || command === '--version') {
    console.log(`v${version}`);
    process.exit(0);
  }

  // setup wizard: skip animation, run directly
  if (command === 'setup') {
    const targetBroker = args[1] || null;
    await runSetup(targetBroker);
    process.exit(0);
  }

  await playSurfingTangerine();

  if (!command || command === 'help' || command === '-h' || command === '--help') {
    console.log(usage);
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
      
      const predictProcess = spawn(pythonBin, [predictScript, '--symbol', symbol.toUpperCase()], {
        cwd: path.join(rootDir, 'services', 'trader')
      });

      let stdoutData = '';
      let stderrData = '';

      predictProcess.stdout.on('data', (data) => {
        stdoutData += data.toString();
      });

      predictProcess.stderr.on('data', (data) => {
        stderrData += data.toString();
      });

      predictProcess.on('close', (code) => {
        if (code !== 0) {
          console.error(`❌ Analysis failed with code ${code}.`);
          console.error(stderrData);
          process.exit(code);
        }

        try {
          const result = extractJSON(stdoutData);
          if (!result.supported) {
            console.error(`❌ Analysis not supported: ${result.reason}`);
            process.exit(1);
          }

          let graphPath = '';
          if (result.wrapper && result.wrapper.consensusGraphBase64) {
            const buf = Buffer.from(result.wrapper.consensusGraphBase64, 'base64');
            graphPath = path.resolve(rootDir, 'data', `consensus_${symbol.toLowerCase()}.png`);
            fs.mkdirSync(path.dirname(graphPath), { recursive: true });
            fs.writeFileSync(graphPath, buf);
          }

          const reportLines = [
            `📊 6-Agent Consensus Analysis: ${symbol.toUpperCase()}`,
            `=========================================`,
            `• Final Action      : ${result.finalAction}`,
            `• Direction Vote    : ${result.directionVote > 0 ? '+' : ''}${result.directionVote.toFixed(4)}`,
            `• Direction Strength: ${result.directionStrength.toFixed(4)}`,
            `• Current Price     : ${result.currentPrice}`,
            `• Live Price        : ${result.livePrice}`,
            `• Analysis Date     : ${result.analysisDate}`,
            `=========================================`,
            `📝 Recommendation Summary:`,
            `  ${result.recommendation.summary}`,
          ];

          if (result.wrapper && result.wrapper.rationale) {
            reportLines.push(`\n📝 Rationale:`);
            result.wrapper.rationale.forEach(r => reportLines.push(`  - ${r}`));
          }

          const reportText = reportLines.join('\n');
          const reportPath = path.resolve(rootDir, 'data', `report_${symbol.toLowerCase()}.txt`);
          fs.writeFileSync(reportPath, reportText);

          console.log(`\n✅ Consensus analysis completed for ${symbol.toUpperCase()}!`);
          console.log(`  Final Action: \x1b[1m\x1b[32m${result.finalAction}\x1b[0m`);
          console.log(`  Summary: ${result.recommendation.summary}\n`);

          if (graphPath) {
            const splitCmd = `node ${__filename} view-result "${graphPath}" "${reportPath}"`;
            const splitSuccess = splitTerminal(splitCmd);
            if (splitSuccess) {
              console.log(`🖥️ Split terminal opened to display consensus graph.`);
            } else {
              console.log(`ℹ️ Opening side-by-side in macOS Preview...`);
              spawn('open', [graphPath]);
              spawn(pythonBin, [path.resolve(rootDir, 'services', 'trader', 'view_chart.py'), graphPath, reportPath], { stdio: 'inherit' });
            }
          } else {
            console.log(reportText);
          }

        } catch (err) {
          console.error('❌ Failed to parse analysis result:', err.message);
          console.log('--- Raw Output ---');
          console.log(stdoutData);
          process.exit(1);
        }
      });
      break;
    }

    case 'prophet': {
      const symbol = args[1];
      if (!symbol) {
        console.error('❌ Error: 분석할 티커(예: AAPL)를 입력해주세요.');
        console.log(usage);
        process.exit(1);
      }
      
      let days = 30;
      const daysIndex = args.indexOf('--days');
      if (daysIndex !== -1 && args[daysIndex + 1]) {
        days = parseInt(args[daysIndex + 1], 10);
      }

      console.log(`📈 Running Prophet ${days}-day forecast for symbol: ${symbol.toUpperCase()}...`);
      const prophetScript = path.resolve(rootDir, 'services', 'trader', 'prophet_forecast.py');
      
      const prophetProcess = spawn(pythonBin, [prophetScript, symbol.toUpperCase(), '--days', days.toString(), '--json'], {
        cwd: path.join(rootDir, 'services', 'trader')
      });

      let stdoutData = '';
      let stderrData = '';

      prophetProcess.stdout.on('data', (data) => {
        stdoutData += data.toString();
      });

      prophetProcess.stderr.on('data', (data) => {
        stderrData += data.toString();
      });

      prophetProcess.on('close', (code) => {
        if (code !== 0) {
          console.error(`❌ Prophet forecast failed with code ${code}.`);
          console.error(stderrData);
          process.exit(code);
        }

        try {
          const result = extractJSON(stdoutData);
          
          const reportPath = path.resolve(rootDir, 'data', `prophet_report_${symbol.toLowerCase()}.txt`);
          fs.mkdirSync(path.dirname(reportPath), { recursive: true });
          fs.writeFileSync(reportPath, result.report);

          console.log(`\n✅ Prophet forecast completed for ${symbol.toUpperCase()}!`);
          console.log(result.report.split('\n')[0]);
          console.log(`  Chart saved at: ${result.photo}\n`);

          const splitCmd = `node ${__filename} view-result "${result.photo}" "${reportPath}"`;
          const splitSuccess = splitTerminal(splitCmd);
          if (splitSuccess) {
            console.log(`🖥️ Split terminal opened to display forecast chart.`);
          } else {
            console.log(`ℹ️ Opening side-by-side in macOS Preview...`);
            spawn('open', [result.photo]);
            spawn(pythonBin, [path.resolve(rootDir, 'services', 'trader', 'view_chart.py'), result.photo, reportPath], { stdio: 'inherit' });
          }

        } catch (err) {
          console.error('❌ Failed to parse prophet result:', err.message);
          console.log('--- Raw Output ---');
          console.log(stdoutData);
          process.exit(1);
        }
      });
      break;
    }

    case 'view-result': {
      const imgPath = args[1];
      const reportPath = args[2] || '';
      if (!imgPath) {
        console.error('❌ Error: 이미지 경로가 필요합니다.');
        process.exit(1);
      }
      const viewScript = path.resolve(rootDir, 'services', 'trader', 'view_chart.py');
      const pythonArgs = [viewScript, imgPath];
      if (reportPath) {
        pythonArgs.push(reportPath);
      }
      const viewProcess = spawn(pythonBin, pythonArgs, { stdio: 'inherit' });
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
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
