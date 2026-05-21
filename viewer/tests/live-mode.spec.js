const { test, expect } = require('@playwright/test');
const { spawn } = require('child_process');
const path = require('path');

const repoRoot = path.resolve(__dirname, '../..');
const benchmarkDir = path.join(repoRoot, 'atc-benchmark');
const livePort = 18080;

const pythonPath = process.platform === 'win32'
  ? path.join(repoRoot, '.venv', 'Scripts', 'python.exe')
  : path.join(repoRoot, '.venv', 'bin', 'python');

function startLiveServer() {
  const child = spawn(
    pythonPath,
    [
      '-m',
      'atc_benchmark.runner.live_server',
      'scenarios/crossing_conflict_001.json',
      '--host',
      '127.0.0.1',
      '--port',
      String(livePort),
      '--max-ticks',
      '1000',
      '--tick-interval-sec',
      '0.2'
    ],
    {
      cwd: benchmarkDir,
      stdio: ['ignore', 'pipe', 'pipe']
    }
  );

  let output = '';
  const ready = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Live server did not start:\n${output}`)), 10_000);
    const onData = (chunk) => {
      output += chunk.toString();
      if (output.includes('Uvicorn running on')) {
        clearTimeout(timer);
        resolve();
      }
      if (output.includes('could not bind')) {
        clearTimeout(timer);
        reject(new Error(output));
      }
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);
    child.on('exit', (code) => {
      if (code !== null && code !== 0) {
        clearTimeout(timer);
        reject(new Error(`Live server exited with ${code}:\n${output}`));
      }
    });
  });

  return { child, ready };
}

test.describe('live mode viewer controls', () => {
  let liveServer;

  test.beforeEach(async () => {
    liveServer = startLiveServer();
    await liveServer.ready;
  });

  test.afterEach(async () => {
    if (liveServer?.child && !liveServer.child.killed) {
      liveServer.child.kill();
    }
  });

  test('runs as a live game control surface', async ({ page }) => {
    await page.goto('/index.html');
    await page.evaluate((port) => {
      window.atcLiveEndpoint = `ws://127.0.0.1:${port}/live`;
    }, livePort);

    await page.getByLabel('Mode', { exact: true }).selectOption('live');
    await page.getByRole('button', { name: 'Start' }).click();

    await expect(page.getByRole('status').first()).toContainText('Live connected');
    await expect(page.getByLabel('Live game dashboard')).toBeVisible();
    await expect(page.locator('.live-run-state')).toContainText('Running');
    await expect(page.getByLabel('Time')).toBeEnabled();
    await expect.poll(async () => Number(await page.getByLabel('Time').getAttribute('max'))).toBeGreaterThan(0);
    await expect.poll(async () => page.locator('.flight-strip').count()).toBeGreaterThan(0);

    await page.locator('.flight-strip').first().click();
    await expect(page.locator('.flight-strip.selected .strip-selector')).toContainText('Selected');

    await page.getByRole('button', { name: 'Set scope to 80 nautical miles' }).click();
    await page.getByRole('button', { name: 'Set scope to 40 nautical miles' }).click();

    const callsign = (await page.locator('.flight-strip.selected .strip-title b').textContent()).trim();
    await page.getByLabel('Command text').fill(`${callsign} HDG 090`);
    await page.getByRole('button', { name: 'Send' }).click();
    await expect(page.locator('.command-feedback')).toContainText(/Accepted|Rejected/);
    await expect(page.locator('.live-event-log')).toContainText(/Accepted|Rejected/);

    await page.getByRole('button', { name: 'Pause' }).click();
    await expect(page.getByRole('button', { name: 'Resume' })).toBeVisible();
    await expect(page.locator('.live-run-state')).toContainText(/Paused/);
    const pausedMax = await page.getByLabel('Time').getAttribute('max');
    await page.waitForTimeout(450);
    await expect(page.getByLabel('Time')).toHaveAttribute('max', pausedMax);

    await page.getByRole('button', { name: 'Resume' }).click();
    await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible();
    await expect(page.locator('.live-run-state')).toContainText(/Running/);
    await expect.poll(async () => Number(await page.getByLabel('Time').getAttribute('max'))).toBeGreaterThan(Number(pausedMax));

    await page.getByRole('button', { name: 'Reset' }).click();
    await expect(page.locator('.live-event-log')).toContainText(/reset/i);
    await expect.poll(async () => Number(await page.getByLabel('Time').inputValue())).toBeLessThanOrEqual(1);

    await page.getByRole('button', { name: 'Disconnect' }).click();
    await expect(page.getByRole('button', { name: 'Start' })).toBeEnabled();
    await expect(page.getByRole('button', { name: 'Disconnect' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Send' })).toBeDisabled();
  });
});
