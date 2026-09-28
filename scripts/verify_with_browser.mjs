// 用真的瀏覽器＋獨立的解碼器，把 qrdrop 產生的 PNG / SVG 解回原文字，交叉驗證編碼器。
//
// 為什麼要這樣做：單元測試裡的解碼器是我們自己寫的，可能跟編碼器犯同樣的錯。
// 這裡改由 Chromium 解碼圖片、再交給 jsQR（獨立實作的 QR 解碼器）讀出內容。
// 若瀏覽器支援 BarcodeDetector（瀏覽器內建解碼），會優先使用；否則用 jsQR。
//
// 用法（需要 Node 18+、playwright、jsqr，都不列入專案依賴）：
//   npm install playwright jsqr        # 在任何暫存資料夾
//   PLAYWRIGHT_PATH=/path/to/node_modules/playwright JSQR_PATH=/path/to/node_modules/jsqr/dist/jsQR.js \
//     node scripts/verify_with_browser.mjs
// 環境變數：PYTHON（預設 python3）、PLAYWRIGHT_PATH、JSQR_PATH。

import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const python = process.env.PYTHON || 'python3';

async function loadPlaywright() {
  const p = process.env.PLAYWRIGHT_PATH;
  if (p) {
    const entry = require.resolve(p, { paths: [process.cwd()] });
    return await import(pathToFileURL(entry).href);
  }
  return await import('playwright');
}
const jsqrPath = process.env.JSQR_PATH || (() => { try { return require.resolve('jsqr/dist/jsQR.js'); } catch { return null; } })();

const { chromium } = await loadPlaywright();
const dir = mkdtempSync(join(tmpdir(), 'qrdrop-verify-'));

// --- 測試案例 ----------------------------------------------------------
const rand = (() => { let s = 12345; return () => (s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff; })();
const ascii = n => Array.from({ length: n }, () => String.fromCharCode(33 + Math.floor(rand() * 94))).join('');
const cases = [];
const add = (name, text, args = []) => cases.push({ name, text, args });

add('numeric', '01234567', ['--ecc', 'M']);
add('numeric-long', '3141592653589793238462643383279502884197169399375105820974944592307816406286', ['--ecc', 'L']);
add('alphanumeric', 'HELLO WORLD $%*+-./:', ['--ecc', 'Q']);
add('url-token', 'http://192.168.0.14:51234/dCTbLFgZfXfPEypd/', ['--ecc', 'M']);
add('chinese', '掃 QR Code 就能在電腦和手機之間傳檔', ['--ecc', 'M']);
add('chinese-H', '你好，世界！這是一段繁體中文測試。', ['--ecc', 'H']);
add('emoji', '🙂👍 qrdrop 🚀', ['--ecc', 'Q']);
add('japanese-mixed', 'こんにちは — Привет — مرحبا — 안녕', ['--ecc', 'L']);
add('newline', 'line1\nline2\ttab', ['--ecc', 'M']);
add('single-char', 'A', ['--ecc', 'H']);
for (const ecc of ['L', 'M', 'Q', 'H']) {
  for (const v of [1, 2, 3, 6, 7, 10, 14, 21, 27, 32, 40]) {
    add(`v${v}-${ecc}`, ascii(v === 1 ? 5 : 12) + ' 中文', ['--ecc', ecc, '--min-version', String(v)]);
  }
}
for (const m of [0, 1, 2, 3, 4, 5, 6, 7]) add(`mask${m}`, `mask ${m} 測試 https://example.com/${m}`, ['--ecc', 'M', '--mask', String(m)]);
// 接近容量上限
add('cap-1-L', ascii(17), ['--ecc', 'L']);
add('cap-10-M', ascii(213), ['--ecc', 'M']);
add('cap-25-H', ascii(469), ['--ecc', 'H']);
add('cap-40-L', ascii(2953), ['--ecc', 'L', '--scale', '4']);
add('cap-40-H', ascii(1273), ['--ecc', 'H', '--scale', '4']);

// --- 產生圖檔 ----------------------------------------------------------
for (const [i, c] of cases.entries()) {
  c.png = join(dir, `c${i}.png`);
  c.svg = join(dir, `c${i}.svg`);
  const extra = ['--png', c.png];
  if (i % 5 === 0) extra.push('--svg', c.svg);
  execFileSync(python, ['-m', 'qrdrop', 'qr', c.text, ...c.args, ...extra], { cwd: root, env: { ...process.env, PYTHONPATH: root }, stdio: ['ignore', 'ignore', 'pipe'] });
}

// --- 瀏覽器解碼 --------------------------------------------------------
const browser = await chromium.launch({ args: ['--enable-experimental-web-platform-features'] });
const page = await browser.newPage();
await page.goto('about:blank');
const hasDetector = await page.evaluate(async () => {
  if (typeof BarcodeDetector === 'undefined') return false;
  try { return (await BarcodeDetector.getSupportedFormats()).includes('qr_code'); } catch { return false; }
});
if (!hasDetector) {
  if (!jsqrPath) { console.error('這個瀏覽器沒有 BarcodeDetector，請設定 JSQR_PATH 指向 jsQR.js'); process.exit(2); }
  await page.addScriptTag({ path: jsqrPath });
}
console.log(`解碼器：${hasDetector ? 'Chromium BarcodeDetector' : 'jsQR（在 Chromium 內執行）'}`);

async function decode(dataUrl) {
  return await page.evaluate(async ({ dataUrl, hasDetector }) => {
    const img = new Image();
    img.src = dataUrl;
    await img.decode();
    const w = img.naturalWidth || 800, h = img.naturalHeight || 800;
    if (hasDetector) {
      const r = await new BarcodeDetector({ formats: ['qr_code'] }).detect(img);
      return r.length ? { text: r[0].rawValue } : null;
    }
    const cv = document.createElement('canvas');
    cv.width = w; cv.height = h;
    const ctx = cv.getContext('2d');
    ctx.imageSmoothingEnabled = false;
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h);
    ctx.drawImage(img, 0, 0, w, h);
    const id = ctx.getImageData(0, 0, w, h);
    const r = jsQR(id.data, w, h, { inversionAttempts: 'dontInvert' });
    if (!r) return null;
    return { text: new TextDecoder('utf-8', { fatal: true }).decode(new Uint8Array(r.binaryData)) };
  }, { dataUrl, hasDetector });
}

let fail = 0, total = 0;
for (const c of cases) {
  const files = [['png', 'image/png', c.png]];
  if (c.svg && (cases.indexOf(c) % 5 === 0)) files.push(['svg', 'image/svg+xml', c.svg]);
  for (const [kind, mime, path] of files) {
    total++;
    const url = `data:${mime};base64,${readFileSync(path).toString('base64')}`;
    let got = null, err = '';
    try { got = await decode(url); } catch (e) { err = String(e.message || e).split('\n')[0]; }
    const ok = got && got.text === c.text;
    if (!ok) fail++;
    console.log(`${ok ? 'OK  ' : 'FAIL'} ${kind} ${c.name.padEnd(16)} ${c.text.length} 字元${ok ? '' : `  解出：${got ? JSON.stringify(got.text).slice(0, 60) : 'null'} ${err}`}`);
  }
}
await browser.close();
rmSync(dir, { recursive: true, force: true });
console.log(`\n${total - fail}/${total} 通過`);
process.exit(fail ? 1 : 0);
