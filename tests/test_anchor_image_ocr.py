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


def _fixture(tmp_path, *, conflict=False, unreadable=False):
    from PIL import Image, ImageDraw, ImageFont
    packet = json.loads(json.dumps(EXPECTED))
    if conflict:
        packet['anchors'][0]['timestamp_ms'] += 1000
    text = json.dumps(packet, separators=(',', ':')).replace(',"', ',\n"').replace('},{', '},\n{')
    if unreadable:
        text = text.replace('1795080900000', '17950????0000')
    image = Image.new('RGB', (1920, 1008), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((200, 280, 990, 660), fill=(54, 58, 69))
    label_font = ImageFont.truetype(r'C:\Windows\Fonts\consola.ttf', 18)
    data_font = ImageFont.truetype(r'C:\Windows\Fonts\consolab.ttf', 22)
    draw.multiline_text((210, 295), 'LTT Anchor Export 2A.2\nA1 UTC 2026-11-19 09:35:00 @ 0.550765543862194\nA2 UTC 2026-11-19 10:35:00 @ 0.5511461869748545', fill=(0, 188, 212), font=label_font, spacing=4)
    draw.multiline_text((210, 380), text, fill='white', font=data_font, spacing=3)
    target = tmp_path / ('conflict.png' if conflict else 'unreadable.png' if unreadable else 'representative.png')
    image.save(target)
    return target


def test_anchor_image_real_engine_paste_roundtrip(tmp_path, monkeypatch):
    # No service startup, trading transport, live state or external network.
    from starlette.requests import Request
    from fastapi import HTTPException
    from render import master_service as service
    monkeypatch.setattr(service, 'APP_PROFILE', 'local')
    import requests
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a, **k: pytest.fail('External transport denied'))
    supplied = os.environ.get('LTT_ANCHOR_OCR_FIXTURE')
    path = Path(supplied) if supplied else _fixture(tmp_path)
    print('ORIGINAL_PNG' if supplied else 'REPRESENTATIVE_PNG_ORIGINAL_ACCEPTANCE_PENDING')
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

    # This sandbox denies Node loopback connects (EACCES). Transport alone is
    # replaced by pre-registered private files; the actual route and OCR run here.
    rpc = tmp_path / 'ocr-rpc'
    rpc.mkdir()
    for i in range(5):
        (rpc / f'request-{i}.png').write_bytes(b'')
        (rpc / f'response-{i}.json').write_bytes(b'')
    stopped = threading.Event()
    def serve_fixture():
        for i in range(5):
            request_file = rpc / f'request-{i}.png'
            while not request_file.stat().st_size:
                if stopped.wait(.005):
                    return
            request = Request(dict(type='http', headers=[(b'content-type', b'image/png')], client=('127.0.0.1', 12345)))
            try:
                result = service.calculator_anchor_image(request, request_file.read_bytes())
                body, status = json.loads(result.body), result.status_code
            except HTTPException as exc:
                body, status = dict(detail=exc.detail), exc.status_code
            (rpc / f'response-{i}.json').write_text(json.dumps(dict(body=body, status=status)))
    thread = threading.Thread(target=serve_fixture, daemon=True)
    thread.start()
    try:
        # First native-engine use is this first paste. Three repeats follow, then PNG drop.
        run_browser('image', 'Australia/Brisbane', image_options=dict(
            file=str(path), expected=EXPECTED, rpc=str(rpc)))
    except AssertionError:
        # Job-private direct test log only; never log screenshots/OCR in the app.
        print('OCR_FAILURE_DIAGNOSTIC', '\n'.join(diagnostics)[:12000] if diagnostics else 'No native OCR output')
        raise
    finally:
        stopped.set()
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
        assert recognize_anchor_image(data.getvalue())['packet'] == EXPECTED
    except AnchorImageError:
        print('MOVED_OCR_DIAGNOSTIC', '\n'.join(diagnostics[-4:])[:12000])
        raise
    for variant in ('conflict', 'unreadable'):
        bad = _fixture(tmp_path, **{variant: True})
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


def test_anchor_image_moved_panel_numeric_reads(tmp_path):
    from PIL import Image
    from render.anchor_image_ocr import recognize_anchor_image, _panels, AnchorImageError
    supplied = os.environ.get('LTT_ANCHOR_OCR_FIXTURE')
    path = Path(supplied) if supplied else _fixture(tmp_path)
    with Image.open(path) as source:
        image = source.convert('RGB')
    panel = image.crop(_panels(image)[0])
    moved = Image.new('RGB', image.size, 'white')
    moved.paste(panel, (80, 210))
    data = io.BytesIO()
    moved.save(data, format='PNG')
    assert recognize_anchor_image(data.getvalue())['packet'] == EXPECTED
    for variant in ('conflict', 'unreadable'):
        with pytest.raises(AnchorImageError):
            recognize_anchor_image(_fixture(tmp_path, **{variant: True}).read_bytes())
