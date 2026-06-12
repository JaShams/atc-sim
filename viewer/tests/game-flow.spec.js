const { test, expect } = require('@playwright/test');
const { spawn } = require('child_process');
const path = require('path');

const repoRoot = path.resolve(__dirname, '../..');
const benchmarkDir = path.join(repoRoot, 'atc-benchmark');
const livePort = 18081;

const pythonPath = process.platform === 'win32'
  ? path.join(repoRoot, '.venv', 'Scripts', 'python.exe')
  : path.join(repoRoot, '.venv', 'bin', 'python');

function startLobbyServer() {
  const child = spawn(
    pythonPath,
    [
      '-m',
      'atc_benchmark.runner.live_server',
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

test.describe('game flow: lobby, debrief, replay restore', () => {
  let liveServer;

  test.beforeEach(async () => {
    liveServer = startLobbyServer();
    await liveServer.ready;
  });

  test.afterEach(async () => {
    if (liveServer?.child && !liveServer.child.killed) {
      liveServer.child.kill();
    }
  });

  test('plays a level from the lobby through debrief and replay', async ({ page }) => {
    await page.goto('/index.html');
    await page.evaluate((port) => {
      window.atcLiveEndpoint = `ws://127.0.0.1:${port}/live`;
    }, livePort);

    await page.getByRole('switch', { name: 'Live mode' }).check();
    await page.getByRole('button', { name: 'Start' }).click();
    await expect(page.getByRole('status').first()).toContainText('Live connected');

    // Lobby: level catalog visible with playable cards.
    const lobby = page.getByLabel('Level select');
    await expect(lobby).toBeVisible();
    await expect(page.locator('.level-card')).toHaveCount(16);
    await expect(page.locator('.level-card').first()).toContainText('Not played yet');
    await expect(page.locator('.level-card', { hasText: 'Tutorial First Contact' })).toContainText('Learn the controls');

    // Start a level: lobby hides, ticks flow.
    await page.getByRole('button', { name: /Crossing Conflict 001/ }).click();
    await expect(page.locator('.live-run-state')).toContainText('Running');
    await expect(lobby).toHaveCount(0);

    // Abandon the level via the danger menu.
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: 'More' }).click();
    await page.getByRole('button', { name: 'End', exact: true }).click();

    // Debrief overlay: abandoned outcome forfeits stars.
    const debrief = page.getByRole('dialog', { name: 'Mission debrief' });
    await expect(debrief).toBeVisible();
    await expect(debrief.locator('h2')).toContainText('Abandoned');
    await expect(debrief.locator('.stars').first()).toHaveAttribute('aria-label', '0 of 3 stars');

    // Watch replay switches to replay mode with the run loaded.
    await debrief.getByRole('button', { name: 'Watch replay' }).click();
    await expect(page.getByLabel('Replay detail sidebar')).toBeVisible();
    await expect(page.locator('.tick-readout')).not.toContainText('0 / 0');

    // The finished run also persisted: Load last run is available.
    const loadLastRun = page.getByRole('button', { name: 'Load last run' });
    await expect(loadLastRun).toBeEnabled();
    await loadLastRun.click();
    await expect(page.getByRole('status').first()).toContainText('Loaded last saved run');

    // Back to live: lobby reappears with the played level recorded.
    await page.getByRole('switch', { name: 'Live mode' }).check();
    await page.getByRole('button', { name: 'Start' }).click();
    await expect(page.getByLabel('Level select')).toBeVisible();
    await expect(page.getByRole('button', { name: /Crossing Conflict 001/ })).toContainText('Best');
  });
});
