'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const readline = require('node:readline/promises');
const ROOT = __dirname;
const RUNTIME = path.join(ROOT, '.runtime');
const DESTINATION = 'C:\\Users\\User\\Downloads\\New folder';
const hash = x => crypto.createHash('sha256').update(x).digest('hex').slice(0, 20);
const mediaURL = x => /\.(?:mp4|webm|mov|m4v|m3u8|mpd)(?:[?#]|$)/i.test(x);
function httpURL(value, base) {
  try {
    const u = new URL(value, base);
    if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password) return null;
    u.hash = '';
    return u.href;
  } catch { return null; }
}
function canonical(value) {
  const u = new URL(value);
  for (const k of [...u.searchParams.keys()]) {
    if (/^(?:token|sig|signature|expires|expiry|policy|key-pair-id|x-amz-.+|x-goog-.+)$/i.test(k)) u.searchParams.delete(k);
  }
  u.searchParams.sort();
  return u.href;
}
function reason(text) {
  if (/403|401|forbidden|unauthorized|login|sign in|cookies/i.test(text)) return 'Access denied or authentication required';
  if (/DRM|encrypted|protected/i.test(text)) return 'Protected/encrypted media is not supported';
  if (/404|not found|410/i.test(text)) return 'Media unavailable (404/410)';
  if (/timed? ?out|network|resolve|connection/i.test(text)) return 'Network failure or request timeout';
  if (/playlist|channel|scope/i.test(text)) return 'External playlist/channel refused';
  if (/permission|EACCES|EPERM|ENOSPC|disk/i.test(text)) return 'Cannot write file: permissions or disk space';
  return 'Unsupported player or extraction/download/verification failed';
}
// No shell is involved. URLs are always individual argv entries after --.
const children = new Set();
let cancelled = false;
process.on('SIGINT', () => {
  cancelled = true;
  for (const child of children) child.kill('SIGINT');
});
function run(exe, args, { timeout = 0, progress = false } = {}) {
  return new Promise((resolve, reject) => {
    if (cancelled) return reject(new Error('Cancelled'));
    const child = spawn(exe, args, { shell: false, windowsHide: true, cwd: ROOT });
    children.add(child);
    let stdout = '', stderr = '', tail = '';
    const timer = timeout ? setTimeout(() => child.kill(), timeout) : null;
    child.stdout.on('data', b => {
      stdout += b;
      if (stdout.length > 32 * 1024 * 1024) child.kill();
      if (progress) {
        tail += b;
        const lines = tail.split(/[\r\n]+/); tail = lines.pop();
        for (const line of lines) if (/^PVD_PROGRESS:/.test(line)) console.log(line.replace('PVD_PROGRESS:', '  '));
      }
    });
    child.stderr.on('data', b => { stderr = (stderr + b).slice(-16000); });
    child.on('error', e => { clearTimeout(timer); children.delete(child); reject(e); });
    child.on('close', code => {
      clearTimeout(timer); children.delete(child);
      if (cancelled) reject(new Error('Cancelled'));
      else if (code !== 0) reject(new Error(stderr || `Process failed (${code})`));
      else resolve(stdout);
    });
  });
}

// Runs in both an inert static DOM and each live frame. Group all source/quality
// alternatives within one video element, rather than treating them as videos.
function scanDOM() {
  const out = [];
  const absolute = s => { try { const u = new URL(s, document.baseURI); return /^https?:$/.test(u.protocol) ? u.href : null; } catch { return null; } };
  const attrs = el => [...el.attributes].filter(a => /^(?:src|data-src|data-lazy-src|data-original|data-url|data-video-url|data-hls|data-dash|data-srcset)$/i.test(a.name)).map(a => a.value);
  for (const el of document.querySelectorAll('video,iframe,embed,object,[data-video-url],[data-hls],[data-dash]')) {
    if (el.closest('video') && el.tagName !== 'VIDEO') continue;
    const raw = [...attrs(el)];
    if (el.tagName === 'VIDEO') {
      raw.unshift(el.currentSrc);
      for (const s of el.querySelectorAll('source')) raw.push(...attrs(s));
    }
    if (el.tagName === 'OBJECT') raw.push(el.getAttribute('data'));
    const urls = [...new Set(raw.filter(Boolean).map(absolute).filter(Boolean))];
    out.push({ urls, kind: el.tagName.toLowerCase(), title: el.getAttribute('title') || el.getAttribute('aria-label') || '',
      marker: el.id || `${el.tagName}-${[...document.querySelectorAll(el.tagName)].indexOf(el)}`, top: el.getBoundingClientRect().top + scrollY });
  }
  return out;
}
async function discover(url, playwright) {
  const groups = [], unresolved = [], frameWarnings = new Set();
  const browser = await playwright.chromium.launch({ headless: true, args: ['--autoplay-policy=no-user-gesture-required'] });
  try {
    const context = await browser.newContext({ serviceWorkers: 'block', acceptDownloads: false });
    const page = await context.newPage();
    // All profiles are fresh and ephemeral. No installed-browser cookies are read.
    const add = (items, referrer, prefix) => {
      for (const [i, item] of items.entries()) {
        const urls = item.urls.map(u => httpURL(u, referrer)).filter(Boolean);
        if (!urls.length && !['video', 'iframe', 'object', 'embed'].includes(item.kind)) continue;
        let previous = groups.find(g => urls.some(u => g.urls.includes(u)) ||
          (item.marker && g.marker === prefix + item.marker));
        if (previous) { previous.urls = [...new Set([...previous.urls, ...urls])]; previous.top = item.top; continue; }
        groups.push({ ...item, urls, referrer, marker: item.marker ? prefix + item.marker : '', ordinal: groups.length, label: `${prefix} player ${i + 1}` });
      }
    };
    // Static inventory is retained even if scripts remove an inaccessible player.
    try {
      const response = await context.request.get(url, { timeout: 30000 });
      if (!response.ok()) unresolved.push(`Page HTTP ${response.status()}`);
      if ((response.headers()['content-type'] || '').includes('text/html')) {
        const staticContext = await browser.newContext({ javaScriptEnabled: false });
        const staticPage = await staticContext.newPage();
        await staticPage.route('**/*', r => r.abort());
        const html = await response.text();
        await staticPage.setContent(`<base href="${url.replace(/&/g, '&amp;').replace(/"/g, '&quot;')}">${html}`, { timeout: 15000 });
        add(await staticPage.evaluate(scanDOM), url, 'Page');
        await staticContext.close();
      }
    } catch { unresolved.push('Static page fetch failed; browser discovery attempted'); }
    const observed = [];
    page.on('request', request => {
      if (!mediaURL(request.url()) || /\.(?:ts|m4s)(?:[?#]|$)/i.test(request.url())) return;
      try { observed.push({ url: request.url(), frame: request.frame(), origin: request.headers().origin }); } catch { /* worker */ }
    });
    console.log('Checking rendered page, embedded frames and lazy-loaded players...');
    try { await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 }); }
    catch { unresolved.push('Browser page navigation incomplete (timeout or network error)'); }
    // Finite discovery budget, not a deadline on the download job. Scroll the
    // entire finite page, stopping infinite feeds without following links.
    let reachedBottom = false;
    for (let step = 0; step < 100 && !cancelled; step++) {
      for (const frame of page.frames()) {
        try {
          const parent = frame.parentFrame();
          const frameUrl = httpURL(frame.url(), url) || url;
          // Activate only lazy player URLs; never click arbitrary links/buttons.
          await frame.evaluate(() => {
            for (const el of document.querySelectorAll('iframe,video,source')) {
              if (!el.getAttribute('src')) {
                const s = el.getAttribute('data-src') || el.getAttribute('data-lazy-src') || el.getAttribute('data-original');
                if (s && !/^javascript:/i.test(s)) el.setAttribute('src', s);
              }
            }
            for (const video of document.querySelectorAll('video')) {
              video.muted = true;
              video.play().catch(() => {});
            }
          });
          const items = await frame.evaluate(scanDOM);
          add(items, frameUrl, parent ? `Frame ${hash(frameUrl).slice(0, 6)}` : 'Page');
        } catch { frameWarnings.add('One embedded frame could not be inspected'); }
      }
      reachedBottom = await page.evaluate(() => {
        const bottom = scrollY + innerHeight >= document.documentElement.scrollHeight - 5;
        if (!bottom) scrollBy(0, Math.max(400, innerHeight * 0.8));
        return bottom;
      }).catch(() => true);
      await page.waitForTimeout(reachedBottom ? 1800 : 200);
      if (reachedBottom) {
        // Collect delayed JavaScript inserts once more before finishing.
        for (const frame of page.frames()) {
          try { add(await frame.evaluate(scanDOM), httpURL(frame.url(), url) || url, frame.parentFrame() ? `Frame ${hash(frame.url()).slice(0, 6)}` : 'Page'); } catch {}
        }
        break;
      }
    }
    if (!reachedBottom) unresolved.push('Discovery stopped at 100 scroll steps; infinite/very long page may contain additional players');
    // Attach network manifests to their player/frame rather than every quality
    // rendition becoming a new video. Prefer master manifests over variants.
    for (const item of observed) {
      const f = httpURL(item.frame.url(), url) || url;
      const exact = groups.find(g => g.urls.includes(item.url));
      if (exact) { exact.origin = item.origin || exact.origin; continue; }
      const owners = groups.filter(g => g.referrer === f && g.kind === 'video');
      const owner = owners.length === 1 ? owners[0] : groups.find(g => g.urls.includes(f) && g.kind === 'iframe');
      if (owner) { owner.urls.push(item.url); owner.origin = item.origin || owner.origin; }
      else if (/\.(?:m3u8|mpd)(?:[?#]|$)/i.test(item.url)) {
        const network = groups.find(g => g.kind === 'network' && g.referrer === f && new URL(g.urls[0]).pathname.replace(/[^/]+$/, '') === new URL(item.url).pathname.replace(/[^/]+$/, ''));
        if (network) {
          network.urls.push(item.url);
          frameWarnings.add('Multiple network-only manifests share a frame/directory; quality alternatives versus distinct clips could not be established');
        }
        else groups.push({ urls: [item.url], kind: 'network', referrer: f, origin: item.origin, title: '', label: 'Observed stream', ordinal: groups.length });
      }
    }
    // An iframe wrapper with inventoried child media is resolved via those
    // children; keep the wrapper as an alternative only when there are none.
    const order = g => {
      if (g.referrer === url) return g.ordinal;
      const wrapper = groups.find(w => w !== g && w.urls.includes(g.referrer));
      return wrapper ? wrapper.ordinal + 0.001 * (g.ordinal + 1) : Number.MAX_SAFE_INTEGER;
    };
    const useful = groups.filter(g => !(g.kind === 'iframe' && g.urls.some(u => groups.some(c => c !== g && c.referrer === u && c.urls.length)))).sort((a, b) => order(a) - order(b));
    return { groups: useful, unresolved: [...unresolved, ...frameWarnings], userAgent: await page.evaluate(() => navigator.userAgent) };
  } finally { await browser.close(); }
}
function createEngine(runtime = RUNTIME) {
  const bin = path.join(runtime, 'bin');
  const yt = path.join(bin, 'yt-dlp.exe');
  const base = ['--ignore-config', '--no-plugin-dirs', '--no-playlist', '--no-cache-dir', '--socket-timeout', '25', '--retries', '2', '--extractor-retries', '2', '--fragment-retries', '2', '--ffmpeg-location', bin, '--js-runtimes', `deno:${path.join(bin, 'deno.exe')}`];
  return {
    async extract(url, referrer, ua, allowGenericPage = false, origin) {
      const headers = origin && httpURL(origin) ? ['--add-headers', `Origin:${new URL(origin).origin}`] : [];
      const raw = await run(yt, [...base, ...headers, '--referer', referrer, '--user-agent', ua, '--dump-single-json', '--skip-download', '--', url], { timeout: 120000 });
      const info = JSON.parse(raw);
      if (info._type === 'playlist' || info._type === 'multi_video') {
        if (!allowGenericPage || !/generic/i.test(info.extractor_key || info.extractor || '')) throw new Error('External playlist scope refused');
        return (info.entries || []).filter(Boolean).flatMap(x => x.entries ? [] : [x]);
      }
      return [info];
    },
    async download(video, template, ua) {
      const headers = video.origin && httpURL(video.origin) ? ['--add-headers', `Origin:${new URL(video.origin).origin}`] : [];
      const out = await run(yt, [...base, ...headers, '--referer', video.referrer, '--user-agent', ua,
        '-f', 'bv*+ba/b', '--merge-output-format', 'mp4/mkv', '--remux-video', 'mp4>mp4/mov>mp4',
        '--windows-filenames', '--trim-filenames', '150', '--no-overwrites', '--continue',
        '--newline', '--progress-template', 'download:PVD_PROGRESS:%(progress._percent_str)s %(progress._speed_str)s ETA %(progress._eta_str)s',
        '--print', 'after_move:PVD_FILE:%(filepath)s', '--no-simulate', '-o', template, '--', video.url], { progress: true });
      const files = out.split(/\r?\n/).filter(x => x.startsWith('PVD_FILE:')).map(x => x.slice(9));
      if (files.length !== 1) throw new Error('Final output was not produced as one video');
      return files[0];
    },
    async probe(file, expectedAudio = false) {
      const data = JSON.parse(await run(path.join(bin, 'ffprobe.exe'), ['-v', 'error', '-show_streams', '-show_format', '-of', 'json', file], { timeout: 60000 }));
      if (!data.streams?.some(s => s.codec_type === 'video') || (expectedAudio && !data.streams.some(s => s.codec_type === 'audio')) || !(Number(data.format?.duration) > 0)) throw new Error('Output media verification failed');
      return data;
    }
  };
}
function identity(info, fallback) {
  return info.id && !/^generic$/i.test(info.extractor_key || '')
    ? hash(`${info.extractor_key || info.extractor}:${info.id}`) : hash(canonical(fallback));
}
async function plan(inventory, pageURL, engine) {
  const videos = [], unresolved = [...inventory.unresolved], seen = new Map();
  const addInfo = (info, group, source) => {
    if (!info || info._type === 'playlist' || info._type === 'url' || info._type === 'url_transparent') return false;
    const key = identity(info, source);
    const aliases = [source, info.url, ...(info.formats || []).map(f => f.manifest_url || f.url)].filter(u => httpURL(u)).map(canonical);
    let previous = seen.get(key) || videos.find(v => v.aliases.some(a => aliases.includes(a)));
    if (previous) { previous.aliases = [...new Set([...previous.aliases, ...aliases])]; return true; }
    const hasAudio = (info.formats || [info]).some(f => f.acodec && f.acodec !== 'none');
    const video = { key, aliases, url: source, referrer: group.referrer, origin: group.origin, title: info.title || group.title || 'Video', hasAudio, label: group.label };
    videos.push(video); seen.set(key, video); return true;
  };
  const sourceFor = (info, fallback) => {
    if (!/^generic$/i.test(info.extractor_key || info.extractor || '')) return httpURL(info.webpage_url) || fallback;
    if (mediaURL(fallback)) return fallback;
    return [info.url, ...(info.formats || []).map(f => f.manifest_url), ...(info.formats || []).map(f => f.url)].find(u => u && mediaURL(u)) || null;
  };
  for (const group of inventory.groups) {
    if (cancelled) throw new Error('Cancelled');
    let resolved = false, failure = 'No accessible media URL';
    const alternatives = [];
    // Keep HTML source order. Each element is one player with alternate sources.
    for (const source of [...new Set(group.urls)]) {
      const old = videos.find(v => v.aliases.includes(canonical(source)));
      if (old) { resolved = true; break; }
      try {
        const entries = await engine.extract(source, group.referrer, inventory.userAgent, group.kind === 'iframe', group.origin);
        for (const info of entries) {
          const target = sourceFor(info, source);
          if (target) {
            if (['video', 'network'].includes(group.kind)) alternatives.push({ info, target });
            else resolved = addInfo(info, group, target) || resolved;
          }
        }
        if (resolved) break;
      } catch (e) { failure = reason(e.message); }
    }
    if (alternatives.length) {
      const score = ({ info }) => Math.max(...(info.formats || [info]).map(f =>
        (Number(f.height) || 0) * 1e9 + (Number(f.width) || 0) * 1e6 + (Number(f.tbr) || Number(f.filesize) / 1e6 || 0)));
      alternatives.sort((a, b) => score(b) - score(a));
      const best = alternatives[0];
      resolved = addInfo(best.info, group, best.target) || resolved;
      const selected = videos.find(v => v.key === identity(best.info, best.target) || v.aliases.includes(canonical(best.target)));
      if (selected) selected.aliases = [...new Set([...selected.aliases, ...group.urls.map(canonical)])];
    }
    if (!resolved) {
      if (group.urls.length) videos.push({ key: hash(canonical(group.urls[0])), aliases: [], label: group.label, failure });
      unresolved.push(`${group.label}: ${failure}`);
    }
  }
  // Page-level extraction supplements the explicit inventory, never replacing it.
  try {
    const entries = await engine.extract(pageURL, pageURL, inventory.userAgent, true);
    for (const info of entries) {
      const source = sourceFor(info, pageURL);
      if (httpURL(source)) addInfo(info, { referrer: pageURL, label: 'Page extractor', title: '' }, source);
    }
  } catch (e) {
    if (!inventory.groups.length) unresolved.push(`Page extraction: ${reason(e.message)}`);
  }
  return { videos, unresolved };
}
function safeTitle(s) {
  return String(s).normalize('NFKC').replace(/[<>:"/\\|?*\x00-\x1f%]/g, '_').replace(/[. ]+$/g, '').slice(0, 65) || 'Video';
}
async function downloadAll(planResult, engine, { destination = DESTINATION, stateDir = RUNTIME, userAgent = 'Mozilla/5.0' } = {}) {
  fs.mkdirSync(destination, { recursive: true }); fs.mkdirSync(stateDir, { recursive: true });
  const stateFile = path.join(stateDir, 'completed.json');
  let state = {};
  if (fs.existsSync(stateFile)) {
    try { state = JSON.parse(fs.readFileSync(stateFile, 'utf8')); }
    catch { throw new Error('Completion state is unreadable; restore or rename .runtime/completed.json before retrying'); }
  }
  const summary = { discovered: planResult.videos.length, downloaded: 0, skipped: 0, failed: [], unresolved: planResult.unresolved, destination };
  for (const [index, video] of planResult.videos.entries()) {
    if (cancelled) break;
    console.log(`[${index + 1}/${planResult.videos.length}] ${safeTitle(video.title || video.label)}`);
    try {
      if (video.failure) throw new Error(video.failure);
      const previous = state[video.key];
      if (previous?.file && path.dirname(path.resolve(previous.file)) === path.resolve(destination) && fs.existsSync(previous.file)) {
        const stat = fs.statSync(previous.file);
        if (stat.size === previous.size) {
          try { await engine.probe(previous.file, video.hasAudio); summary.skipped++; console.log('  Already completed; skipped.'); continue; }
          catch { /* Preserve the damaged existing file, but retry using a new stem. */ }
        }
        previous.stem = `${String(index + 1).padStart(3, '0')}_${safeTitle(video.title).slice(0, 45)}_[${video.key}]_retry${Date.now()}`;
      }
      // Persist the non-sensitive output stem before downloading so ordering or
      // title changes on another run still resume the same .part file.
      const stem = previous?.stem && path.basename(previous.stem) === previous.stem && !/[<>:"/\\|?*\x00-\x1f%]/.test(previous.stem) ? previous.stem : `${String(index + 1).padStart(3, '0')}_${safeTitle(video.title)}_[${video.key}]`;
      state[video.key] = { stem };
      const save = () => { fs.writeFileSync(stateFile + '.tmp', JSON.stringify(state, null, 2)); fs.renameSync(stateFile + '.tmp', stateFile); };
      save();
      const file = await engine.download(video, path.join(destination, stem + '.%(ext)s'), userAgent);
      if (path.dirname(path.resolve(file)) !== path.resolve(destination)) throw new Error('Unexpected output path');
      try { await engine.probe(file, video.hasAudio); }
      catch (e) {
        state[video.key] = { stem: `${String(index + 1).padStart(3, '0')}_${safeTitle(video.title).slice(0, 45)}_[${video.key}]_retry${Date.now()}` };
        save(); throw e;
      }
      state[video.key] = { stem, file, size: fs.statSync(file).size };
      save(); summary.downloaded++;
    } catch (e) {
      if (cancelled) break;
      const why = video.failure || reason(e.message);
      summary.failed.push({ number: index + 1, reason: why }); console.log(`  Failed: ${why}`);
    }
  }
  summary.cancelled = cancelled;
  // Diagnostics intentionally contain no source URLs, headers, cookies or raw errors.
  fs.writeFileSync(path.join(stateDir, 'last-summary.json'), JSON.stringify(summary, null, 2));
  return summary;
}
function printSummary(s) {
  console.log(`\nDistinct videos discovered: ${s.discovered}\nDownloaded successfully: ${s.downloaded}\nAlready downloaded and skipped: ${s.skipped}\nFailed: ${s.failed.length}`);
  for (const f of s.failed) console.log(`  Video ${f.number}: ${f.reason}`);
  console.log(`Known unresolved players/coverage warnings: ${s.unresolved.length}`);
  for (const u of s.unresolved) console.log(`  ${u}`);
  if (s.cancelled) console.log('Cancelled; remaining videos were not attempted. Partial downloads remain retryable.');
  console.log(`Save directory: ${s.destination}`);
  if (s.unresolved.length || s.failed.length) console.log('Some players remain unresolved. This run does not establish that every video was downloaded.');
}
async function main() {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  let input;
  try { input = await rl.question('Paste the webpage URL and press Enter:'); } finally { rl.close(); }
  const url = httpURL(input.trim());
  if (!url) throw new Error('Invalid URL: paste one HTTP or HTTPS URL without embedded credentials');
  const playwright = require(path.join(RUNTIME, 'node_modules', 'playwright'));
  const inventory = await discover(url, playwright);
  const engine = createEngine();
  const result = await plan(inventory, url, engine);
  const summary = await downloadAll(result, engine, { userAgent: inventory.userAgent });
  printSummary(summary);
  process.exitCode = cancelled ? 130 : (summary.failed.length || summary.unresolved.length || !summary.discovered ? 1 : 0);
}
module.exports = { httpURL, canonical, safeTitle, identity, discover, plan, downloadAll, createEngine, printSummary, reason };
if (require.main === module) main().catch(e => {
  console.error(cancelled ? 'Cancelled. Partial downloads can be resumed.' : (/^Invalid URL|^Completion state/.test(e.message) ? e.message : reason(e.message)));
  console.error(`Save directory: ${DESTINATION}`);
  process.exitCode = cancelled ? 130 : 1;
});
