"""Local-only recognition of the printed LTT anchor packet, with independent checks.

No uploads, image files, OCR text logs, trading calls or inferred missing digits.
"""
from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

MAX_BYTES = 6 * 1024 * 1024
_slots = threading.BoundedSemaphore(2)


class AnchorImageError(ValueError):
    pass


def _binary() -> str:
    for value in (shutil.which('tesseract'),
                  r'C:\Program Files\Tesseract-OCR\tesseract.exe',
                  r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe'):
        if value and Path(value).is_file():
            return value
    raise AnchorImageError('Local Tesseract OCR is unavailable. Text/JSON paste still works.')


def _panels(image: Image.Image) -> list[tuple[int, int, int, int]]:
    """Find large constant dark table backgrounds, independent of screen position."""
    factor = max(2, (image.width + 639) // 640)
    small = image.resize((image.width // factor, image.height // factor), Image.Resampling.NEAREST)
    colors = sorted(small.getcolors(small.width * small.height) or [], reverse=True)
    candidates = [(n, c) for n, c in colors if n > 200 and max(c) < 125][:10]
    # Tinted table backgrounds before browser chrome; black tables remain supported.
    candidates.sort(key=lambda item: (item[1][2] - item[1][0] > 3, item[0]), reverse=True)
    regions = []
    for _, color in candidates:
        diff = ImageChops.difference(small, Image.new('RGB', small.size, color))
        channels = diff.split()
        mask = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2]).point(lambda x: 0 if x else 255)
        data = mask.tobytes()
        runs = {}
        for y in range(small.height):
            for match in re.finditer(b'\xff{' + str(max(40, 180 // factor)).encode() + b',}', data[y * small.width:(y + 1) * small.width]):
                key = (match.start(), match.end())
                runs.setdefault(key, []).append(y)
        for (left, right), rows in runs.items():
            if len(rows) < 4 or (rows[-1] - rows[0]) * factor < 90:
                continue
            # Stay inside the constant table background. Including the white
            # chart margin creates dark rails after inversion and spoils layout.
            rect = (max(0, left * factor + 2), max(0, rows[0] * factor),
                    min(image.width, right * factor - 2), min(image.height, (rows[-1] + 1) * factor - 2))
            if rect[2] - rect[0] < 240 or rect[3] - rect[1] > image.height * .85:
                continue
            if any(abs(rect[0] - old[0]) < 12 and abs(rect[2] - old[2]) < 12 for old in regions):
                continue
            regions.append(rect)
        if regions:
            return sorted(regions, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]), reverse=True)[:3]
    raise AnchorImageError('Export panel unreadable. Keep the complete printed export and both A1/A2 lines visible.')


def _ocr(image: Image.Image, binary: str, deadline: float, *, tsv=False, digits=False, psm=6) -> str:
    data = io.BytesIO()
    image.save(data, format='PNG')
    args = [binary, 'stdin', 'stdout', '--psm', str(psm)]
    if digits:
        args += ['-c', 'tessedit_char_whitelist=0123456789.']
    if tsv:
        args.append('tsv')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AnchorImageError('Screenshot recognition timed out; paste again or use text/JSON.')
    try:
        child = subprocess.run(args, input=data.getvalue(), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=remaining, check=False,
                               env={**os.environ, 'OMP_THREAD_LIMIT': '1'})
    except subprocess.TimeoutExpired as exc:
        raise AnchorImageError('Screenshot recognition timed out; no anchors applied.') from exc
    if child.returncode:
        raise AnchorImageError('Local OCR failed; no anchors applied.')
    return child.stdout.decode('utf-8', errors='replace')


def _lines(tsv: str) -> list[dict]:
    grouped = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter='\t', quoting=csv.QUOTE_NONE):
        if row.get('level') != '5' or not row.get('text', '').strip():
            continue
        key = tuple(row[k] for k in ('block_num', 'par_num', 'line_num'))
        words = grouped.setdefault(key, [])
        words.append(dict(text=row['text'], x=int(row['left']), y=int(row['top']),
                          w=int(row['width']), h=int(row['height'])))
    return [dict(text=' '.join(w['text'] for w in words), words=words) for words in grouped.values()]


def _number_crop(image: Image.Image, line: dict, label: str) -> Image.Image:
    for word in line['words']:
        # OCR can join the key and value into one token. A short match (the
        # 'o' in anchors, for example) must not hide the subsequent value.
        for match in re.finditer(r'[0-9@Oo]+(?:\.[0-9@Oo]+)?', word['text']):
            if len(match.group()) < 10 and '.' not in match.group():
                continue
            # The packet and A1/A2 numeric fields are monospace. Keep padding so
            # recognition, rather than a guessed digit substitution, decides.
            left = word['x'] + word['w'] * match.start() / max(1, len(word['text']))
            right = word['x'] + word['w'] * match.end() / max(1, len(word['text']))
            top = min(w['y'] for w in line['words'])
            bottom = max(w['y'] + w['h'] for w in line['words'])
            # TSV boxes are ink bounds, not complete monospace character cells.
            # Proportional offsets can otherwise cut the first/last glyph. Keep
            # a full-glyph margin; punctuation is excluded by numeric recognition.
            crop = image.crop((max(0, int(left) - 18), max(0, top - 4), min(image.width, int(right) + 22), min(image.height, bottom + 5)))
            crop.info['numeric_character_count'] = len(match.group())
            return crop
    raise AnchorImageError(label + ' unreadable; no anchors applied.')


def _tight_numeric_pixels(crop: Image.Image) -> Image.Image:
    """Trim surrounding quotes/brackets by visible glyph columns, not digit values."""
    count = crop.info.get('numeric_character_count', 0)
    mask = crop.convert('L').point(lambda value: 255 if value > 150 else 0)
    density = [mask.crop((0, y, mask.width, y + 1)).histogram()[255] for y in range(mask.height)]
    peak = max(density, default=0)
    bands, beginning = [], None
    for y, ink in enumerate([*density, 0]):
        if peak and ink >= peak * .35 and beginning is None:
            beginning = y
        elif (not peak or ink < peak * .35) and beginning is not None:
            bands.append((beginning, y))
            beginning = None
    if not bands:
        raise AnchorImageError('Numeric text row unreadable.')
    top, bottom = max(bands, key=lambda band: band[1] - band[0])
    # Exclude nearby-line ink before grouping glyph columns. Otherwise a quote
    # above the decimal point can merge columns and consume a bracket as a digit.
    mask = mask.crop((0, top, mask.width, bottom))
    columns = mask.getprojection()[0]
    spans, start = [], None
    for x, ink in enumerate([*columns, 0]):
        if ink and start is None:
            start = x
        elif not ink and start is not None:
            if x - start >= 2:  # Ignore a one-pixel chart crosshair.
                box = mask.crop((start, 0, x, mask.height)).getbbox()
                if box:
                    spans.append((start, x, box[3] - box[1]))
            start = None
    if not spans or not count:
        raise AnchorImageError('Numeric glyph boundaries unreadable.')
    full_height = max(span[2] for span in spans)
    first = next((i for i, span in enumerate(spans) if span[2] >= full_height * .65), None)
    if first is None or len(spans) - first < count:
        raise AnchorImageError('Numeric glyph boundaries unreadable.')
    # The count comes from the detected numeric token, including its decimal
    # point. No recognized value or independently printed label sets this edge.
    return crop.crop((max(0, spans[first][0] - 2), max(0, top - 3),
                      min(crop.width, spans[first + count - 1][1] + 2), min(crop.height, bottom + 3)))


def _numeric_reads(crops: list[Image.Image], binary: str, deadline: float,
                   timestamp_rows: list[Image.Image] | None = None) -> list[str]:
    versions = []
    for threshold in (None, 180):
        processed = []
        for crop in crops:
            gray = ImageOps.invert(crop.convert('L'))
            if threshold is not None:
                gray = gray.point(lambda x: 255 if x > threshold else 0)
            processed.append(gray.resize((gray.width * 2, gray.height * 2), Image.Resampling.LANCZOS))
        height = max(p.height for p in processed) + 24
        sheet = Image.new('L', (max(p.width for p in processed) + 24, height * len(processed)), 255)
        for i, part in enumerate(processed):
            sheet.paste(part, (12, i * height + 12))
        values = [re.sub(r'\s', '', x) for x in _ocr(sheet, binary, deadline, digits=True).splitlines() if x.strip()]
        if len(values) != len(crops):
            raise AnchorImageError('A timestamp or price is unreadable; no anchors applied.')
        # The numeric-only pass can render a following comma as full stops and
        # omit the redundant zero in a fractional decimal. Normalize syntax,
        # never digits: timestamps remain strict integers, and every price must
        # still equal its independently printed A1/A2 value below.
        values = [value.rstrip('.') for value in values]
        values = [('0' + value if i >= 2 and value.startswith('.') else value)
                  for i, value in enumerate(values)]
        versions.append(values)
    labels = ['A1 milliseconds', 'A2 milliseconds', 'A1 export price', 'A2 export price', 'A1 printed price', 'A2 printed price']
    agreed = list(versions[0])
    for i, (first, second) in enumerate(zip(*versions)):
        if first != second:
            if i < 2 and timestamp_rows is not None:
                # Read the complete labelled row independently. The UTC label
                # only proves seconds; it cannot repair or supply milliseconds.
                row = ImageOps.expand(ImageOps.invert(timestamp_rows[i].convert('L')), border=20, fill=255)
                text = _ocr(row, binary, deadline, psm=7)
                matches = re.findall(r'timestamp[_\s]*ms[^\d\n]*([0-9]{13,16})(?!\d)', text, re.I)
                if len(matches) == 1 and matches[0] in (first, second):
                    agreed[i] = matches[0]
                    continue
            # Resolve only by another actual pixel read, never by choosing the
            # value closest to a price or by replacing a suspected digit.
            # Preserve native glyph strokes. Upscaling the slashed zero can
            # change its contour into a 6/9 for the line-layout recognizer.
            single = ImageOps.invert(_tight_numeric_pixels(crops[i]).convert('L'))
            single = ImageOps.expand(single, border=20, fill=255)
            third = re.sub(r'\s', '', _ocr(single, binary, deadline, digits=True, psm=13)).rstrip('.')
            if i >= 2 and third.startswith('.'):
                third = '0' + third
            if third not in (first, second):
                raise AnchorImageError(labels[i] + ' is ambiguous; no anchors applied.')
            agreed[i] = third
    return agreed


def _read_panel(panel: Image.Image, binary: str, deadline: float) -> dict:
    image = panel.resize((panel.width * 2, panel.height * 2), Image.Resampling.LANCZOS)
    lines = _lines(_ocr(ImageOps.invert(image.convert('L')), binary, deadline, tsv=True))
    text = '\n'.join(line['text'] for line in lines)
    # These glyph alternatives concern the fixed schema literal only. Never
    # substitute glyphs in a timestamp, price, symbol or timeframe.
    if not re.search(r'[l1i|]tt[- ]anchors/v[1il|]', text, re.I):
        raise AnchorImageError('LTT export schema unreadable.')
    symbol = re.findall(r'symbol[^\n]*?([A-Z][A-Z0-9_]+)\s*:\s*([A-Z0-9_/.]+)', text, re.I)
    frame = re.findall(r'timeframe[^\n]*?[:=]\s*[^\w]*([0-9]+|[DWM])', text, re.I)
    extension = re.findall(r'right_extension[^\n]*?[:=]\s*(true|false)', text, re.I)
    if len(symbol) != 1 or len(frame) != 1 or len(extension) != 1:
        raise AnchorImageError('Export symbol, timeframe or extension unreadable or conflicting.')
    times = [line for line in lines if re.search(r'timestamp[_\s]*ms', line['text'], re.I)]
    prices = [line for line in lines if re.search(r'price', line['text'], re.I) and 'UTC' not in line['text']]
    # The small cyan coordinate labels must be recognized independently of the
    # much larger white JSON. A single layout pass can omit those smaller lines.
    schema_lines = [line for line in lines if re.search(r'schema', line['text'], re.I)]
    if not schema_lines:
        raise AnchorImageError('Export schema row unreadable.')
    header_end = min(w['y'] for w in schema_lines[0]['words']) - 3
    if header_end <= 0:
        raise AnchorImageError('Independent A1/A2 UTC labels are not visible.')
    header = image.crop((0, 0, image.width, header_end))
    red, green, blue = header.split()
    cyan = ImageChops.darker(ImageChops.subtract(green, red), ImageChops.subtract(blue, red)).point(lambda value: 0 if value > 50 else 255)
    printed = [line for line in _lines(_ocr(cyan, binary, deadline, tsv=True))
               if re.search(r'A[12l]\s+UTC', line['text'])]
    if len(times) != 2 or len(prices) != 2 or len(printed) != 2:
        raise AnchorImageError('Both export timestamps/prices and independent A1/A2 UTC lines must be readable.')
    utc, printed_prices = [], []
    for index, line in enumerate(printed, 1):
        match = re.search(r'A' + ('[1l]' if index == 1 else '2') + r'\s+UTC\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*@\s*((?:0|[1-9]\d*)\.\d+)', line['text'])
        if not match:
            raise AnchorImageError(f'A{index} printed UTC time unreadable.')
        try:
            utc.append(int(datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp()))
            printed_prices.append(match.group(2))
        except ValueError as exc:
            raise AnchorImageError(f'A{index} printed UTC time invalid.') from exc
    fields = times + prices + printed
    labels = ['A1 milliseconds', 'A2 milliseconds', 'A1 export price', 'A2 export price', 'A1 printed price', 'A2 printed price']
    crops = [_number_crop(image, line, label) for line, label in zip(fields, labels)]
    timestamp_rows = [image.crop((0, max(0, min(w['y'] for w in line['words']) - 4), image.width,
                                  min(image.height, max(w['y'] + w['h'] for w in line['words']) + 5)))
                      for line in times]
    values = _numeric_reads(crops, binary, deadline, timestamp_rows)
    anchors = []
    for i in range(2):
        if not re.fullmatch(r'[1-9]\d{12,15}', values[i]):
            raise AnchorImageError(f'A{i+1} milliseconds unreadable.')
        ms = int(values[i])
        price, independent = values[i + 2], values[i + 4]
        if not re.fullmatch(r'(?:0|[1-9]\d*)\.\d+', price) or price != independent or independent != printed_prices[i]:
            raise AnchorImageError(f'A{i+1} export and printed prices disagree or are unreadable.')
        if ms // 1000 != utc[i] or ms > 8640000000000000 or float(price) <= 0:
            raise AnchorImageError(f'A{i+1} export milliseconds conflict with printed UTC; no anchors applied.')
        anchors.append(dict(timestamp_ms=ms, price=price))
    if anchors[1]['timestamp_ms'] <= anchors[0]['timestamp_ms']:
        raise AnchorImageError('Anchor 2 must be later than anchor 1.')
    return dict(schema='ltt-anchors/v1', symbol=':'.join(symbol[0]).upper(),
                timeframe=frame[0].upper(), right_extension=extension[0].lower() == 'true', anchors=anchors)


def recognize_anchor_image(data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise AnchorImageError('PNG exceeds the 6 MB limit.')
    if not _slots.acquire(blocking=False):
        raise AnchorImageError('Local OCR is busy; paste again shortly.')
    started = time.monotonic()
    try:
        with Image.open(io.BytesIO(data)) as source:
            if (source.format != 'PNG' or source.width * source.height > 16000000
                    or source.width < 240 or source.height < 120
                    or getattr(source, 'n_frames', 1) != 1):
                raise AnchorImageError('Use one PNG screenshot up to 16 megapixels.')
            image = source.convert('RGB')
        binary = _binary()
        deadline = started + 8
        packets, errors = [], []
        for rect in _panels(image):
            try:
                packets.append(_read_panel(image.crop(rect), binary, deadline))
            except AnchorImageError as exc:
                errors.append(str(exc))
        if len(packets) != 1:
            raise AnchorImageError('Multiple competing anchor exports.' if len(packets) > 1 else (errors[0] if errors else 'Export unreadable.'))
        return dict(packet=packets[0], elapsed_ms=round((time.monotonic() - started) * 1000), source='Local Tesseract')
    except (OSError, Image.DecompressionBombError) as exc:
        raise AnchorImageError('PNG could not be decoded; no anchors applied.') from exc
    finally:
        _slots.release()
