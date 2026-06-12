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

test.describe('live websocket error handling', () => {
  test('surfaces malformed websocket messages in status and log', async ({ page }) => {
    await page.addInitScript(() => {
      class FakeWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSED = 3;

        constructor(url) {
          this.url = url;
          this.readyState = FakeWebSocket.CONNECTING;
          setTimeout(() => {
            this.readyState = FakeWebSocket.OPEN;
            this.onopen?.({});
            setTimeout(() => {
              this.onmessage?.({ data: '{"type":' });
            }, 0);
          }, 0);
        }

        send() {}

        close() {
          this.readyState = FakeWebSocket.CLOSED;
          this.onclose?.({});
        }
      }

      window.WebSocket = FakeWebSocket;
    });

    await page.goto('/index.html');
    await page.evaluate(() => {
      window.atcLiveEndpoint = 'ws://127.0.0.1:18080/live';
    });

    await page.getByRole('switch', { name: 'Live mode' }).check();
    await page.getByRole('button', { name: 'Start' }).click();

    await expect(page.getByRole('status').first()).toContainText('Malformed live message');
    await page.getByRole('tab', { name: 'Log' }).click();
    await expect(page.locator('.live-event-log')).toContainText('Malformed live message');
  });
});

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

    await page.getByRole('switch', { name: 'Live mode' }).check();
    await expect(page.getByRole('button', { name: 'Start' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Disconnect' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Pause' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Reset' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Start' }).click();

    await expect(page.getByRole('status').first()).toContainText('Live connected');
    await expect(page.getByLabel('Live game dashboard')).toBeVisible();
    await expect(page.locator('.live-run-state')).toContainText('Running');
    await expect(page.getByRole('button', { name: 'Start' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Disconnect' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Reset' })).toHaveCount(0);
    await expect(page.getByLabel('Time')).toHaveCount(0);

    // Flight strips are the default sidebar tab; clicking one prefills the command line.
    await expect(page.locator('.flight-strip').first()).toBeVisible();
    await page.locator('.flight-strip', { hasText: 'ARR1' }).first().click();
    await expect(page.getByLabel('Command text')).toHaveValue('ARR1 ');

    // Running score HUD appears once ticks carry a score.
    await expect(page.locator('.score-hud')).toBeVisible();

    await page.getByRole('button', { name: 'Set scope to 80 nautical miles' }).click();
    await page.getByRole('button', { name: 'Set scope to 40 nautical miles' }).click();

    await page.getByLabel('Command text').fill('ARR1 HDG 090');
    await page.getByRole('button', { name: 'Send' }).click();
    await expect(page.locator('.command-feedback')).toContainText(/Accepted|Rejected/);
    await page.getByRole('tab', { name: 'Log' }).click();
    await expect(page.locator('.live-event-log')).toContainText(/Accepted|Rejected/);

    await page.getByRole('button', { name: 'Pause' }).click();
    await expect(page.getByRole('button', { name: 'Resume' })).toBeVisible();
    await expect(page.locator('.live-run-state')).toContainText(/Paused/);
    await page.waitForTimeout(450);

    await page.getByRole('button', { name: 'Resume' }).click();
    await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible();
    await expect(page.locator('.live-run-state')).toContainText(/Running/);

    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: 'More' }).click();
    await page.getByRole('button', { name: 'Reset' }).click();
    await expect(page.locator('.live-event-log')).toContainText(/reset/i);

    await page.getByRole('button', { name: 'Disconnect' }).click();
    await expect(page.getByRole('button', { name: 'Start' })).toBeEnabled();
    await expect(page.getByRole('button', { name: 'Disconnect' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Send' })).toBeDisabled();
  });
});
