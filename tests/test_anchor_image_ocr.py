"""Isolated local screenshot recognition and production-JS clipboard coverage."""
import io
import json
import os
import runpy
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = dict(schema='ltt-anchors/v1', symbol='PEPPERSTONE:NZDUSD', timeframe='5',
                right_extension=True, anchors=[
                    dict(timestamp_ms=1795080900000, price='0.550765543862194'),
                    dict(timestamp_ms=1795084500000, price='0.5511461869748545')])
XRP_EXPECTED = dict(schema='ltt-anchors/v1', symbol='BYBIT:XRPUSDT.P', timeframe='1',
                    right_extension=True, anchors=[
                        dict(timestamp_ms=1795134120000, price='0.9529982265894831'),
                        dict(timestamp_ms=1795135380000, price='0.9540231016359697')])
CASES = [('NZDUSD_anchor_control.png', EXPECTED), ('XRP_anchor_case.png', XRP_EXPECTED)]


def _original_or_fixture(tmp_path, filename, expected):
    directory = os.environ.get('LTT_ANCHOR_OCR_FIXTURE_DIR')
    return Path(directory) / filename if directory else _fixture(tmp_path, expected=expected)


def _fixture(tmp_path, *, conflict=False, unreadable=False, expected=EXPECTED):
    from PIL import Image, ImageDraw, ImageFont
    packet = json.loads(json.dumps(expected))
    if conflict:
        packet['anchors'][0]['timestamp_ms'] += 1000
    text = json.dumps(packet, separators=(',', ':')).replace(',"', ',\n"').replace('},{', '},\n{')
    if unreadable:
        text = text.replace(str(packet['anchors'][0]['timestamp_ms']), '17950????0000')
    image = Image.new('RGB', (1920, 1008), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((200, 280, 990, 660), fill=(54, 58, 69))
    label_font = ImageFont.truetype(r'C:\Windows\Fonts\consola.ttf', 18)
    data_font = ImageFont.truetype(r'C:\Windows\Fonts\consolab.ttf', 22)
    from datetime import datetime, timezone
    labels = ['LTT Anchor Export 2A.2']
    for i, anchor in enumerate(expected['anchors'], 1):
        utc = datetime.fromtimestamp(anchor['timestamp_ms'] // 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        labels.append(f'A{i} UTC {utc} @ {anchor["price"]}')
    draw.multiline_text((210, 295), '\n'.join(labels), fill=(0, 188, 212), font=label_font, spacing=4)
    draw.multiline_text((210, 380), text, fill='white', font=data_font, spacing=3)
    target = tmp_path / ('conflict.png' if conflict else 'unreadable.png' if unreadable else 'representative.png')
    image.save(target)
    return target


@pytest.mark.parametrize('filename,expected', CASES, ids=['NZDUSD', 'XRP'])
def test_anchor_image_real_engine_paste_roundtrip(tmp_path, monkeypatch, filename, expected):
    # No service startup, trading transport, live state or external network.
    from starlette.requests import Request
    from fastapi import HTTPException
    from render import master_service as service
    monkeypatch.setattr(service, 'APP_PROFILE', 'local')
    import requests
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a, **k: pytest.fail('External transport denied'))
    path = _original_or_fixture(tmp_path, filename, expected)
    print('ORIGINAL_PNG ' + filename if os.environ.get('LTT_ANCHOR_OCR_FIXTURE_DIR') else 'REPRESENTATIVE_PNG_ORIGINAL_ACCEPTANCE_PENDING')
    run_browser = runpy.run_path(str(ROOT / 'tests/test_calculator_js_behavior.py'))['_run_anchor_import_behavior']
    import subprocess
    native_run = subprocess.run
    diagnostics = []
    def observed_run(*args, **kwargs):
        result = native_run(*args, **kwargs)
        command = args[0] if args else kwargs.get('args', [])
        if command and 'tesseract' in str(command[0]).lower():
            diagnostics.append(result.stdout.decode('utf-8', errors='replace'))
        return result
    monkeypatch.setattr(subprocess, 'run', observed_run)

    # Real loopback HTTP transport to the production endpoint function. No live
    # LTT process, application startup or external transport is involved.
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.path == '/api/calculator/anchor-image'
            request = Request(dict(type='http', headers=[(b'content-type', b'image/png')], client=self.client_address))
            try:
                result = service.calculator_anchor_image(request, self.rfile.read(int(self.headers['Content-Length'])))
                body, status = result.body, result.status_code
            except HTTPException as exc:
                body, status = json.dumps(dict(detail=exc.detail)).encode(), exc.status_code
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        # First native-engine use is this first paste. Three repeats follow, then PNG drop.
        run_browser('image', 'Australia/Brisbane', image_options=dict(
            file=str(path), expected=expected, http=f'http://127.0.0.1:{server.server_port}'))
    except AssertionError:
        # Job-private direct test log only; never log screenshots/OCR in the app.
        print('OCR_FAILURE_DIAGNOSTIC', '\n'.join(diagnostics)[:12000] if diagnostics else 'No native OCR output')
        raise
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        assert not thread.is_alive()

    from PIL import Image
    from render.anchor_image_ocr import recognize_anchor_image, _panels, AnchorImageError, MAX_BYTES
    with Image.open(path) as source:
        image = source.convert('RGB')
    # Automatically found panel moved to a different chart location, not a fixed crop.
    panel = image.crop(_panels(image)[0])
    moved = Image.new('RGB', image.size, 'white')
    moved.paste(panel, (80, 210))
    data = io.BytesIO()
    moved.save(data, format='PNG')
    try:
        assert recognize_anchor_image(data.getvalue())['packet'] == expected
    except AnchorImageError:
        print('MOVED_OCR_DIAGNOSTIC', '\n'.join(diagnostics[-4:])[:12000])
        raise
    for variant in ('conflict', 'unreadable'):
        bad = _fixture(tmp_path, expected=expected, **{variant: True})
        with pytest.raises(AnchorImageError, match='unreadable|ambiguous|conflict|readable'):
            recognize_anchor_image(bad.read_bytes())
    with pytest.raises(AnchorImageError, match='6 MB'):
        recognize_anchor_image(b'x' * (MAX_BYTES + 1))
    nonlocal_request = Request(dict(type='http', headers=[(b'content-type', b'image/png')], client=('192.0.2.1', 12345)))
    with pytest.raises(HTTPException) as exc:
        service.calculator_anchor_image(nonlocal_request, path.read_bytes())
    assert exc.value.status_code == 403


def test_anchor_image_pending_results_and_drop():
    run_browser = runpy.run_path(str(ROOT / 'tests/test_calculator_js_behavior.py'))['_run_anchor_import_behavior']
    run_browser('image', 'Australia/Brisbane', image_options=dict(expected=EXPECTED))


@pytest.mark.parametrize('filename,expected', CASES, ids=['NZDUSD', 'XRP'])
def test_anchor_image_moved_panel_numeric_reads(tmp_path, filename, expected):
    from PIL import Image
    from render.anchor_image_ocr import recognize_anchor_image, _panels, AnchorImageError
    path = _original_or_fixture(tmp_path, filename, expected)
    with Image.open(path) as source:
        image = source.convert('RGB')
    panel = image.crop(_panels(image)[0])
    moved = Image.new('RGB', image.size, 'white')
    moved.paste(panel, (80, 210))
    data = io.BytesIO()
    moved.save(data, format='PNG')
    assert recognize_anchor_image(data.getvalue())['packet'] == expected
    for variant in ('conflict', 'unreadable'):
        with pytest.raises(AnchorImageError):
            recognize_anchor_image(_fixture(tmp_path, expected=expected, **{variant: True}).read_bytes())


def test_anchor_image_joined_token_and_exact_milliseconds(monkeypatch):
    from render.anchor_image_ocr import _number_crop
    from PIL import Image
    # The first numeric-looking match is the 'o' in anchors. Locate the actual
    # value later in that same joined token, retaining all thirteen digits.
    line = dict(words=[dict(text='"anchors":[{"timestamp_ms":1795134120789,', x=0, y=4, w=700, h=20)])
    crop = _number_crop(Image.new('RGB', (720, 40)), line, 'A1 milliseconds')
    assert crop.info['numeric_character_count'] == 13
    assert crop.width < 360
    # Real numeric recognition on clear isolated field pixels, including nonzero
    # milliseconds. Whole-second labels must not be used to fill these digits.
    from PIL import ImageDraw, ImageFont
    from render import anchor_image_ocr as o
    import time
    values = ['1795080900789', '1795084500123', '0.550765543862194',
              '0.5511461869748545', '0.550765543862194', '0.5511461869748545']
    font = ImageFont.truetype(r'C:/Windows/Fonts/consola.ttf', 28)
    crops = []
    for value in values:
        image = Image.new('RGB', (350, 50), (54, 58, 69))
        ImageDraw.Draw(image).text((12, 7), value, font=font, fill='white')
        image.info['numeric_character_count'] = len(value)
        crops.append(image)
    rows = []
    for value in values[:2]:
        image = Image.new('RGB', (700, 50), (54, 58, 69))
        ImageDraw.Draw(image).text((10, 7), '"timestamp_ms":' + value + ',', font=font, fill='white')
        rows.append(image)
    assert o._numeric_reads(crops, o._binary(), time.monotonic() + 8, rows) == values
    # Four distinct pixel-read answers are not a vote. In particular a UTC
    # seconds match cannot choose between conflicting millisecond digits.
    first, second = values[:], values[:]
    second[0] = '1795080900788'
    answers = iter(['\n'.join(first), '\n'.join(second), '"timestamp_ms":1795080900787,', '1795080900786'])
    monkeypatch.setattr(o, '_ocr', lambda *a, **k: next(answers))
    with pytest.raises(o.AnchorImageError, match='A1 milliseconds.*ambiguous'):
        o._numeric_reads(crops, 'test', time.monotonic() + 8, rows)
