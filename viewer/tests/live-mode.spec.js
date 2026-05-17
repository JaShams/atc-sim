const { test, expect } = require('@playwright/test');
const { spawn } = require('child_process');
const path = require('path');

const repoRoot = path.resolve(__dirname, '../..');
const benchmarkDir = path.join(repoRoot, 'atc-benchmark');
const livePort = 18080;

function startLiveServer() {
  const child = spawn(
    'python',
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

    await page.locator('#modeSelect').selectOption('live');
    await page.locator('#liveEndpoint').fill(`ws://127.0.0.1:${livePort}/live`);
    await page.locator('#liveConnect').click();

    await expect(page.locator('#loadStatus')).toContainText('Live connected');
    await expect(page.locator('#liveGamePanel')).toBeVisible();
    await expect(page.locator('#liveRunState')).toContainText('Running');
    await expect(page.locator('#tickSlider')).toBeEnabled();
    await expect.poll(async () => Number(await page.locator('#tickSlider').getAttribute('max'))).toBeGreaterThan(0);
    await expect.poll(async () => page.locator('#commandAircraft option').count()).toBeGreaterThan(0);
    await expect.poll(async () => page.locator('.flight-strip').count()).toBeGreaterThan(0);

    await page.locator('.flight-strip').first().click();
    await expect(page.locator('#commandAircraft')).not.toHaveValue('');

    await page.locator('#zoomIn').click();
    await page.locator('#zoomOut').click();

    await page.locator('#commandType').selectOption('assign_heading');
    await page.locator('#commandValue').fill('90');
    await page.locator('#sendCommand').click();
    await expect(page.locator('#commandFeedback')).toContainText(/Accepted|Rejected/);
    await expect(page.locator('#liveEventLog')).toContainText(/Accepted|Rejected/);

    await page.locator('#livePause').click();
    await expect(page.locator('#livePause')).toHaveText('Resume');
    await expect(page.locator('#liveRunState')).toContainText(/Paused/);
    const pausedMax = await page.locator('#tickSlider').getAttribute('max');
    await page.waitForTimeout(450);
    await expect(page.locator('#tickSlider')).toHaveAttribute('max', pausedMax);

    await page.locator('#livePause').click();
    await expect(page.locator('#livePause')).toHaveText('Pause');
    await expect(page.locator('#liveRunState')).toContainText(/Running/);
    await expect.poll(async () => Number(await page.locator('#tickSlider').getAttribute('max'))).toBeGreaterThan(Number(pausedMax));

    await page.locator('#liveReset').click();
    await expect(page.locator('#liveEventLog')).toContainText(/reset/i);
    await expect.poll(async () => Number(await page.locator('#tickSlider').inputValue())).toBeLessThanOrEqual(1);

    await page.locator('#liveDisconnect').click();
    await expect(page.locator('#liveConnect')).toBeEnabled();
    await expect(page.locator('#liveDisconnect')).toBeDisabled();
    await expect(page.locator('#sendCommand')).toBeDisabled();
  });
});
