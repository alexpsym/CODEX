'use strict';
// Controlled local integration test. Never contacts a real target website.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');
const { httpURL, canonical, safeTitle, discover, plan, downloadAll, createEngine } = require('./downloader.cjs');
const runtime = path.join(__dirname, '.runtime');
process.env.PLAYWRIGHT_BROWSERS_PATH = path.join(runtime, 'browsers');
process.env.TEMP = path.join(runtime, 'temp');
process.env.TMP = process.env.TEMP;
process.env.DENO_DIR = path.join(runtime, 'deno-cache');
test('URL validation, query data, title safety and stable expiring URL identity', () => {
  assert.equal(httpURL('javascript:alert(1)'), null);
  assert.equal(httpURL('https://user:secret@example.org/'), null);
  assert.equal(httpURL('../a.mp4?x=1&percent=%25', 'https://example.org/p/x'), 'https://example.org/a.mp4?x=1&percent=%25');
  assert.equal(canonical('https://example.org/x?id=2&token=secret'), 'https://example.org/x?id=2');
  assert(!/[<>:"/\\|?*%]/.test(safeTitle('Bad <name>%"/')));
});
test('quality alternatives form one video and choose higher resolution', async () => {
  const low = 'https://example.org/low.mp4', high = 'https://example.org/high.mp4';
  const engine = { extract: async u => {
    if (u === 'https://example.org/page') throw new Error('Unsupported');
    return [{ extractor_key: 'Generic', id: u, title: u === high ? 'High' : 'Low', url: u, formats: [{ url: u, height: u === high ? 1080 : 360 }] }];
  } };
  const result = await plan({ groups: [{ urls: [low, high], kind: 'video', referrer: 'https://example.org/page', label: 'Player' }], unresolved: [], userAgent: 'Test' }, 'https://example.org/page', engine);
  assert.equal(result.videos.length, 1);
  assert.equal(result.videos[0].url, high);
  assert(result.videos[0].aliases.includes(low));
});
test('multiple actual playable videos, iframe/lazy JS, duplicates, failure, repeat skip and deleted completion', { timeout: 600000 }, async () => {
  const work = fs.mkdtempSync(path.join(runtime, 'controlled test '));
  const media = path.join(work, 'media');
  fs.mkdirSync(media);
  const ffmpeg = path.join(runtime, 'bin', 'ffmpeg.exe');
  for (const [i, color] of ['red', 'green', 'blue', 'yellow'].entries()) {
    const r = spawnSync(ffmpeg, ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', `color=c=${color}:s=160x90:r=10:d=1`, '-f', 'lavfi', '-i', `sine=frequency=${400 + i * 100}:duration=1`, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', '-y', path.join(media, `${i + 1}.mp4`)], { encoding: 'utf8' });
    assert.equal(r.status, 0, r.stderr);
  }
  const hls = spawnSync(ffmpeg, ['-hide_banner', '-loglevel', 'error', '-i', path.join(media, '4.mp4'), '-c', 'copy', '-hls_time', '1', '-hls_list_size', '0', '-hls_segment_filename', path.join(media, 'segment%d.ts'), '-y', path.join(media, 'four.m3u8')], { encoding: 'utf8' });
  assert.equal(hls.status, 0, hls.stderr);
  const dash = spawnSync(ffmpeg, ['-hide_banner', '-loglevel', 'error', '-i', path.join(media, '4.mp4'), '-c', 'copy', '-f', 'dash', '-y', path.join(media, 'five.mpd')], { encoding: 'utf8', cwd: media });
  assert.equal(dash.status, 0, dash.stderr);
  const requests = [];
  const server = http.createServer((req, res) => {
    requests.push(req.url);
    const u = new URL(req.url, 'http://localhost');
    if (u.pathname === '/page') {
      res.setHeader('Content-Type', 'text/html');
      res.end(`<!doctype html><title>Controlled multi-video page</title>
        <video id="first" title="First"><source src="media/1.mp4?quality=best&x=%25"></video>
        <video title="Duplicate"><source src="media/1.mp4?quality=best&x=%25"></video>
        <video id="second" data-src="media/2.mp4"></video>
        <iframe src="/frame" title="Third embedded player"></iframe>
        <video id="broken" src="media/missing.mp4"></video>
        <div style="height:1800px"></div><div id="mount"></div>
        <a href="/channel">Unrelated channel - never follow</a>
        <script>addEventListener('scroll',()=>{if(!document.getElementById('dynamic')){let v=document.createElement('video');v.id='dynamic';v.src='/media/four.m3u8';document.getElementById('mount').append(v);let d=document.createElement('video');d.id='dash';d.dataset.src='/media/five.mpd';document.getElementById('mount').append(d)}},{once:true})</script>`);
      return;
    }
    if (u.pathname === '/frame') { res.setHeader('Content-Type', 'text/html'); res.end('<video id="third" src="/media/3.mp4"></video>'); return; }
    if (u.pathname.startsWith('/media/')) {
      const file = path.join(media, path.basename(u.pathname));
      if (fs.existsSync(file)) {
        // Confirm request context survives discovery and subsequent downloads.
        if (!req.headers.referer) { res.writeHead(403); res.end(); return; }
        const types = { '.mp4': 'video/mp4', '.m3u8': 'application/vnd.apple.mpegurl', '.ts': 'video/mp2t', '.mpd': 'application/dash+xml', '.m4s': 'video/iso.segment' };
        res.setHeader('Content-Type', types[path.extname(file)]);
        res.setHeader('Content-Length', fs.statSync(file).size);
        if (req.method === 'HEAD') res.end(); else fs.createReadStream(file).pipe(res);
        return;
      }
    }
    res.writeHead(404); res.end();
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const url = `http://127.0.0.1:${server.address().port}/page?x=one&literal=%25&quote=%22`;
    const playwright = require(path.join(runtime, 'node_modules', 'playwright'));
    const inventory = await discover(url, playwright);
    assert(inventory.groups.some(g => g.urls.some(u => u.includes('four.m3u8'))), 'JS scroll-created player discovered');
    assert(inventory.groups.some(g => g.urls.some(u => u.includes('2.mp4'))), 'Lazy data-src discovered');
    assert(inventory.groups.some(g => g.urls.some(u => u.includes('3.mp4'))), 'Nested iframe video discovered');
    const engine = createEngine();
    const result = await plan(inventory, url, engine);
    console.log('Controlled plan:', result.videos.map(v => ({ key: v.key, title: v.title, failure: v.failure })), result.unresolved);
    assert.equal(result.videos.length, 6, 'five unique videos plus one known failure');
    assert.deepEqual(result.videos.filter(v => !v.failure).map(v => v.title), ['1', '2', '3', 'four', 'five'], 'page player order preserved');
    const destination = path.join(work, 'completed videos with spaces');
    const options = { destination, stateDir: path.join(work, 'state with spaces'), userAgent: inventory.userAgent };
    const first = await downloadAll(result, engine, options);
    assert.equal(first.downloaded, 5);
    assert.equal(first.failed.length, 1);
    const files = fs.readdirSync(destination).filter(f => /\.(mp4|mkv|webm)$/.test(f));
    assert.equal(files.length, 5, 'separate final media files');
    for (const file of files) {
      const probe = await engine.probe(path.join(destination, file), true);
      assert(probe.streams.some(s => s.codec_type === 'audio'));
      assert(probe.streams.some(s => s.codec_type === 'video'));
    }
    const second = await downloadAll(result, engine, options);
    assert.equal(second.skipped, 5); assert.equal(second.downloaded, 0); assert.equal(second.failed.length, 1);
    assert(!requests.some(u => u.startsWith('/channel')), 'must not crawl ordinary links');
    assert(requests.some(u => u.includes('literal=%25') && u.includes('quote=%22')), 'query passed intact as data');
    // Remove only one test-owned file to verify stale completion never skips a missing output.
    fs.unlinkSync(path.join(destination, files[0]));
    const third = await downloadAll(result, engine, options);
    assert.equal(third.skipped, 4); assert.equal(third.downloaded, 1); assert.equal(third.failed.length, 1);
    fs.writeFileSync(path.join(work, 'verification.json'), JSON.stringify({ first, second, third, streamVerification: '5 files: video + audio, including split DASH merge', actualTargetWebsiteTested: false }, null, 2));
    console.log(`Controlled verification artifacts: ${work}`);
  } finally { await new Promise(resolve => server.close(resolve)); }
});
