const { spawn } = require('child_process');
const path = require('path');
const assert = require('assert').strict;

const cliScript = path.resolve(__dirname, '..', 'bin', 'noslip.js');

function runCLI(args) {
  return new Promise((resolve) => {
    const process = spawn(nodePath(), [cliScript, ...args]);
    let stdout = '';
    let stderr = '';

    process.stdout.on('data', (data) => {
      stdout += data.toString();
    });

    process.stderr.on('data', (data) => {
      stderr += data.toString();
    });

    process.on('close', (code) => {
      resolve({ code, stdout, stderr });
    });
  });
}

function nodePath() {
  return process.execPath;
}

async function runTests() {
  console.log('🧪 Running CLI Integration Tests...');

  // Test 1: No command (should show usage/help and exit 0)
  {
    console.log('- Test 1: No arguments');
    const { code, stdout } = await runCLI([]);
    assert.equal(code, 0);
    assert.match(stdout, /NoSlip Quant CLI/);
    assert.match(stdout, /USAGE:/);
  }

  // Test 2: Help command (should show usage/help and exit 0)
  {
    console.log('- Test 2: --help command');
    const { code, stdout } = await runCLI(['--help']);
    assert.equal(code, 0);
    assert.match(stdout, /NoSlip Quant CLI/);
  }

  // Test 3: Version command (should print version)
  {
    console.log('- Test 3: version command');
    const { code, stdout } = await runCLI(['version']);
    assert.equal(code, 0);
    assert.match(stdout, /^v\d+\.\d+\.\d+/);
  }

  // Test 4: Invalid command (should show error and exit 1)
  {
    console.log('- Test 4: Invalid command');
    const { code, stderr } = await runCLI(['invalidcommand']);
    assert.equal(code, 1);
    assert.match(stderr, /Unknown command: invalidcommand/);
  }

  // Test 5: Cardnews without topic (should show validation error and exit 1)
  {
    console.log('- Test 5: Cardnews validation error');
    const { code, stderr } = await runCLI(['cardnews']);
    assert.equal(code, 1);
    assert.match(stderr, /Error: --topic <주제> 인자가 필요합니다/);
  }

  // Test 6: Analyze without symbol (should show validation error and exit 1)
  {
    console.log('- Test 6: Analyze validation error');
    const { code, stderr } = await runCLI(['analyze']);
    assert.equal(code, 1);
    assert.match(stderr, /Error: 분석할 티커\(예: AAPL\)를 입력해주세요/);
  }

  console.log('✅ All CLI Integration Tests Passed Successfully!');
}

runTests().catch((err) => {
  console.error('❌ Test execution failed:', err);
  process.exit(1);
});
