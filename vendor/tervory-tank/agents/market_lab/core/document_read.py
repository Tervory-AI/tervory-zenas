"""Bounded, local extraction. Uploaded text is evidence, never instructions."""
from __future__ import annotations

import csv
import io
import re
import zipfile
from xml.etree import ElementTree as ET
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from feeds.published import xlsx_tables

MAX_TEXT = 2_000_000
MAX_ROWS = 20000
MAX_PAGES = 500


class ReadError(ValueError):
    pass


def bounded_sheets(data: bytes) -> list[tuple[str, list[list[str]], list[int]]]:
    """Check untrusted workbook dimensions before the shared reader allocates cells."""
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(data)) as book:
        if len(book.infolist()) > 2000 or sum(info.file_size for info in book.infolist()) > 20_000_000:
            raise ReadError('This spreadsheet is too large. Split it into smaller files.')
        positions = []
        names = sorted(name for name in book.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', name))
        for name in names:
            raw = book.read(name)
            if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
                raise ReadError('This spreadsheet contains unsupported document features.')
            root = ET.fromstring(raw)
            sheet_rows = root.findall('.//m:sheetData/m:row', ns)
            if len(sheet_rows) > MAX_ROWS:
                raise ReadError('Use up to 20,000 rows per sheet.')
            for cell in root.findall('.//m:c', ns):
                reference = cell.get('r', '')
                match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,6})', reference)
                if not match:
                    raise ReadError('This spreadsheet has an invalid cell address.')
                column = 0
                for letter in match[1]:
                    column = column * 26 + ord(letter) - 64
                if column > 200:
                    raise ReadError('Use up to 200 columns per sheet.')
            positions.append([int(row.get('r', index)) for index, row in enumerate(sheet_rows, 1)])
        # A literal Date column identifies serial Excel dates unambiguously.
        epoch = date(1899, 12, 30)
        if 'xl/workbook.xml' in book.namelist():
            props = ET.fromstring(book.read('xl/workbook.xml')).find('m:workbookPr', ns)
            if props is not None and props.get('date1904') in {'1', 'true'}:
                epoch = date(1904, 1, 1)
    result = []
    for index, table in enumerate(xlsx_tables(data)):
        if table and 'date' in [str(cell).strip().lower() for cell in table[0]]:
            column = [str(cell).strip().lower() for cell in table[0]].index('date')
            for row in table[1:]:
                if len(row) > column and re.fullmatch(r'[0-9]{1,5}(?:\.0+)?', row[column]):
                    row[column] = (epoch + timedelta(days=int(float(row[column])))).isoformat()
        result.append((f'Worksheet {index + 1}', table, positions[index]))
    return result


def structured(table: list[list[str]], label: str) -> list[dict]:
    """Only explicit date/value/unit tables become series; never guess a unit."""
    if not table:
        return []
    headings = [str(x or '').strip().lower() for x in table[0]]
    if not {'date', 'value', 'unit'} <= set(headings):
        return []
    result = []
    for index, cells in enumerate(table[1:], 2):
        row = dict(zip(headings, cells))
        try:
            day = date.fromisoformat(str(row.get('date', '')))
            value = Decimal(str(row.get('value', '')).replace(',', ''))
            unit = str(row.get('unit', '')).strip()
            if not value.is_finite() or not unit:
                raise ValueError()
        except (ValueError, InvalidOperation):
            raise ReadError('A table needs a valid date, number and unit in every row.') from None
        result.append({'date': day.isoformat(), 'value': str(value), 'unit': unit,
                       'currency': str(row.get('currency') or 'none'), 'label': label,
                       'row': index, 'series': str(row.get('series') or row.get('name') or label)})
    return result


def extract(name: str, data: bytes) -> dict:
    passages, tables = [], []
    total = 0
    def add(text: str, label: str, page: int | None = None, table: str | None = None):
        nonlocal total
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text).strip()
        total += len(text)
        if total > MAX_TEXT:
            raise ReadError('This file has too much text. Split it into smaller files.')
        # Preserve sentence/paragraph boundaries where possible, and bound each passage.
        chunks = re.split(r'(?<=[.!?])\s+|\n\s*\n', text)
        for chunk in chunks:
            for start in range(0, len(chunk), 1800):
                part = chunk[start:start + 1800].strip()
                if part:
                    passages.append({'text': part, 'label': label, 'page': page, 'table': table})

    rows, pages = None, None
    unreadable = False
    try:
        if name.lower().endswith('.pdf'):
            import pdfplumber
            with pdfplumber.open(io.BytesIO(data)) as book:
                pages = len(book.pages)
                if pages > MAX_PAGES:
                    raise ReadError('Upload a PDF of up to 500 pages.')
                for index, page in enumerate(book.pages, 1):
                    label = f'Page {index}'
                    found = []
                    for table in page.extract_tables():
                        found.extend(structured(table, label))
                    tables.extend(found)
                    text = page.extract_text() or ''
                    unreadable = unreadable or not text.strip()
                    add(text, label, index, label if found else None)
        elif name.lower().endswith(('.csv', '.xlsx')):
            sheets = [('CSV', list(csv.reader(io.StringIO(data.decode('utf-8-sig')))), [])] if name.lower().endswith('.csv') else bounded_sheets(data)
            rows = 0
            for label, table, positions in sheets:
                if len(table) > MAX_ROWS or any(len(row) > 200 for row in table):
                    raise ReadError('Use up to 20,000 rows and 200 columns per sheet.')
                points = structured(table, label)
                tables.extend(points)
                if not table:
                    continue
                headers = table[0]
                for index, row in enumerate(table[1:], 2):
                    rows += 1
                    text = '; '.join(f'{headers[col] if col < len(headers) else "Column " + str(col + 1)}: {cell}' for col, cell in enumerate(row) if cell)
                    add(text, f'{label}, row {positions[index - 1] if positions else index}', table=label if points else None)
        else:
            raise ReadError('Only PDF, XLSX and CSV files can be read.')
        if not passages:
            raise ReadError('No readable text was found. For a scanned PDF, upload a copy with selectable text.')
        return {'passages': passages, 'tables': tables, 'pages': pages, 'rows': rows, 'note': 'Some PDF pages had no selectable text and could not be read.' if unreadable else ''}
    except ReadError:
        raise
    except Exception:
        # Parser errors can contain file text. Only a safe reason reaches clients/logs.
        raise ReadError('This file could not be read. Check that it opens and is not password protected.') from None
