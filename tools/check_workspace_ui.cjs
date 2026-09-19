// Exercise the read-only sample preview, never an installed tracker.
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const output = path.resolve(__dirname, '../../ui-refresh-preview');
fs.mkdirSync(output, {recursive: true});
(async () => {
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    for (const mode of ['dark', 'light']) {
      for (const width of [1440, 1024, 390]) {
        const context = await browser.newContext({viewport: {width, height: 960}});
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', e => errors.push(e.message));
        await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
        await page.goto('http://127.0.0.1:9893/p/today?theme=' + mode);
        await page.locator('[data-workboard]').waitFor();
        assert.equal(await page.locator('body').getAttribute('data-theme-mode'), mode);
        const layout = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth}));
        assert.ok(layout.scroll <= width + 1, JSON.stringify({mode, ...layout}));
        assert.equal(await page.locator('.workspace-clock summary').isVisible(), true);
        assert.equal(await page.locator('[data-clock-game]').isVisible(), true);
        assert.equal(await page.locator('[data-clock-seen]').isVisible(), true);
        await page.screenshot({path: path.join(output, mode + '-' + width + '.png'), animations: 'disabled'});
        await page.locator('#work-pregnancy-count > summary').click();
        assert.equal(await page.locator('#work-pregnancy-count select').isVisible(), true);
        assert.equal(await page.locator('#work-pregnancy-count button.primary').isVisible(), true);
        await page.locator('#work-pregnancy-count > summary').click();
        await page.locator('.workspace-clock summary').click();
        assert.equal(await page.locator('[data-clock-detail]').isVisible(), true);
        await page.locator('.workspace-clock summary').click();
        await page.locator('[data-refresh-work]').click();
        await page.waitForFunction(() => document.querySelector('[data-work-count="decisions"]').textContent === '3');
        assert.equal(await page.locator('#work-decisions').getAttribute('data-work-total'), '3');
        await page.locator('.workspace-jumps a').first().click();
        assert.equal(new URL(page.url()).hash, '#work-decisions');
        if (width < 800) {
          await page.locator('.mobile-menu-toggle').click();
          assert.equal(await page.locator('.mobile-menu-toggle').getAttribute('aria-expanded'), 'true');
          assert.equal(await page.locator('#navigation-filter').isVisible(), true);
          const menuWidth = await page.evaluate(() => document.documentElement.scrollWidth);
          assert.ok(menuWidth <= width + 1, 'Mobile menu overflow');
          await page.locator('.mobile-menu-toggle').click();
        }
        assert.deepEqual(errors, [], 'Browser errors');
        console.log(JSON.stringify({mode, width, overflow: false, controls: 'passed', countRefresh: 'passed'}));
        await context.close();
      }
    }
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
