"""Local CPU speech/image matching and Rig search; uncertain matches stay in review."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from uuid import uuid4

from rigbox.rank import search
from .video_models import MODELS, FRAME_SIZE, SAMPLE_SECONDS

MAX_BYTES = 32 * 1024 * 1024


async def command(*args, timeout=45):
    if args[0] == 'ffmpeg':
        args = list(args)
        args[1:1] = ['-threads', '1', '-filter_threads', '1', '-filter_complex_threads', '1']
        args[-1:-1] = ['-threads', '1']
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise ValueError('media_decode')
    return output


async def frames(path: Path):
    # Disable network protocols inside the decoder. At most 120 samples / 10 minutes.
    raw = await command('ffmpeg', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                        '-format_whitelist', 'mov,matroska,webm,avi',
                        '-i', str(path), '-t', '600', '-vf', 'fps=1/5:start_time=0:round=up,scale=224:224',
                        '-frames:v', '120', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1')
    return [raw[i:i + FRAME_SIZE * FRAME_SIZE * 3] for i in range(0, len(raw), FRAME_SIZE * FRAME_SIZE * 3)
            if len(raw[i:i + FRAME_SIZE * FRAME_SIZE * 3]) == FRAME_SIZE * FRAME_SIZE * 3]


class Repository:
    """Tenant-qualified durable media and revision-controlled reviews."""
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS videos (tenant TEXT, id TEXT, body TEXT, '
                       'PRIMARY KEY(tenant,id))')

    def db(self):
        return sqlite3.connect(self.root / 'video-shop.sqlite', timeout=10)

    def directory(self, tenant):
        path = self.root / hashlib.sha256(tenant.encode()).hexdigest()
        path.mkdir(exist_ok=True)
        return path

    def get(self, tenant, identifier):
        with self.db() as db:
            row = db.execute('SELECT body FROM videos WHERE tenant=? AND id=?', (tenant, identifier)).fetchone()
        if not row:
            raise KeyError(identifier)
        return json.loads(row[0])

    def list(self, tenant):
        with self.db() as db:
            rows = db.execute('SELECT body FROM videos WHERE tenant=? ORDER BY rowid DESC LIMIT 100',
                              (tenant,)).fetchall()
        return [{key: value for key, value in json.loads(row[0]).items()
                 if key in {'id', 'status', 'duration', 'revision'}} for row in rows]

    def save(self, tenant, body, expected=None):
        with self.db() as db:
            if expected is None:
                db.execute('INSERT INTO videos VALUES (?,?,?)', (tenant, body['id'], json.dumps(body)))
            else:
                result = db.execute('UPDATE videos SET body=? WHERE tenant=? AND id=? '
                                    "AND json_extract(body,'$.revision')=?",
                                    (json.dumps(body), tenant, body['id'], expected))
                if result.rowcount != 1:
                    raise ValueError('review_conflict')


class Engine:
    def __init__(self, rig, repository, models=None):
        self.rig, self.repository = rig, repository
        self.lock = asyncio.Semaphore(1)
        self.models = models if models is not None else MODELS

    async def ingest(self, tenant, video: bytes, transcript=None, references=None, *, permission=None):
        # Supplied transcripts cannot substitute for audio analysis.
        if transcript:
            raise ValueError('transcript_is_automatic')
        references = {} if references is None else references
        if not isinstance(references, dict):
            raise ValueError('media_input')
        if not video or len(video) > MAX_BYTES or len(references) > 100:
            raise ValueError('media_limit')
        identifier = uuid4().hex
        path = self.repository.directory(tenant) / (identifier + '.mp4')
        path.write_bytes(video)
        try:
            async with self.lock:
                metadata = json.loads(await command(
                    'ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                    '-format_whitelist', 'mov,matroska,webm,avi', '-show_entries', 'format=duration,format_name:stream=codec_type',
                    '-of', 'json', str(path)))
                duration = float(metadata['format']['duration'])
                if not (0 < duration <= 600):
                    raise ValueError('duration_limit')
                samples = await frames(path)
                if not samples:
                    raise ValueError('no_frames')
                products = await self.rig.store.products(tenant)
                catalogue = {str(p.get('sku') or p.get('id')): p for p in products if p.get('active', True)}
                # Reuse store catalogue images automatically. Explicit uploads can
                # replace inaccessible images without changing catalogue rows.
                from .video_media import download
                images = dict(references)
                image_errors = []
                slots = asyncio.Semaphore(4)

                async def fetch_reference(sku, product):
                    url = product.get('image_url') or product.get('image')
                    if sku in images or not url:
                        return
                    async with slots:
                        try:
                            images[sku] = await asyncio.to_thread(download, url, 2 * 1024 * 1024)
                        except (ValueError, OSError):
                            image_errors.append(sku)

                tasks = [asyncio.create_task(fetch_reference(sku, product))
                         for sku, product in list(catalogue.items())[:100]]
                if tasks:
                    _, pending = await asyncio.wait(tasks, timeout=45)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    image_errors.extend(['fetch_timeout'] * len(pending))
                fingerprints = {}
                for sku, image in images.items():
                    if sku not in catalogue or len(image) > 2 * 1024 * 1024:
                        raise ValueError('reference_invalid')
                    reference = path.with_suffix('.reference')
                    reference.write_bytes(image)
                    try:
                        fingerprints[sku] = await command(
                            'ffmpeg', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                            '-format_whitelist', 'png_pipe,jpeg_pipe,ppm_pipe',
                            '-i', str(reference), '-vf', 'scale=224:224', '-frames:v', '1',
                            '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1')
                        if len(fingerprints[sku]) != FRAME_SIZE * FRAME_SIZE * 3:
                            raise ValueError('reference_invalid')
                    except (ValueError, TimeoutError):
                        fingerprints.pop(sku, None)
                        if sku in references:
                            raise
                        image_errors.append(sku)
                    finally:
                        reference.unlink(missing_ok=True)
                audio = path.with_suffix('.wav')
                try:
                    has_audio = any(stream.get('codec_type') == 'audio' for stream in metadata.get('streams', []))
                    if has_audio:
                        await command('ffmpeg', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                                      '-format_whitelist', 'mov,matroska,webm,avi', '-i', str(path),
                                      '-t', '600', '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(audio))
                    inference = asyncio.create_task(asyncio.to_thread(
                        self.models.analyse, audio if has_audio else None, samples, fingerprints))
                    try:
                        analysis = await asyncio.shield(inference)
                    except asyncio.CancelledError:
                        # Keep CPU slot and audio until the worker has stopped.
                        await inference
                        raise
                finally:
                    audio.unlink(missing_ok=True)
                transcript = analysis['transcript']
                moments = []
                for index in range(len(samples)):
                    start, end = index * SAMPLE_SECONDS, min((index + 1) * SAMPLE_SECONDS, duration)
                    text = ' '.join(str(s['text']) for s in transcript
                                    if float(s['start']) < end and float(s['end']) > start)
                    ids = search({'tenant_id': tenant, 'products': products, 'q': text, 'limit': 5})['ids']
                    # Search proposes candidates; require a complete name or SKU in the
                    # transcript before claiming text evidence for that product.
                    normalized = ' ' + re.sub(r'[^\w]+', ' ', text.casefold()).strip() + ' '
                    evidence = {sku: {'transcript': True} for sku in ids if sku in catalogue
                                and any(' ' + re.sub(r'[^\w]+', ' ', str(term).casefold()).strip() + ' '
                                        in normalized for term in (sku, catalogue[sku].get('name', '')) if term)}
                    for sku, score in analysis['matches'][index].items():
                        if sku in catalogue:
                            evidence.setdefault(sku, {})['vision_score'] = score
                    for sku, proof in evidence.items():
                        moments.append({'start': start, 'end': end, 'product_id': sku,
                                        'evidence': proof, 'approved': False})
                body = {'id': identifier, 'duration': duration,
                        'media_type': 'video/webm' if 'webm' in metadata['format']['format_name'] else 'video/mp4',
                        'revision': 1, 'status': 'draft', 'moments': moments,
                        'permission': permission, 'transcript': transcript,
                        'receipt': {**analysis['receipt'], 'route': 'local_cpu', 'model_calls': 2 if has_audio else 1, 'media_sha256': hashlib.sha256(video).hexdigest(),
                                    'samples': len(samples),
                                    'reference_images': len(fingerprints), 'image_errors': image_errors,
                                    'catalogue_image_sha256': {sku: hashlib.sha256(images[sku]).hexdigest()
                                                               for sku in fingerprints},
                                    'catalogue_images_truncated': len(catalogue) > 100}}
                self.repository.save(tenant, body)
                return body
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    async def review(self, tenant, identifier, revision, moments):
        body = self.repository.get(tenant, identifier)
        products = {str(p.get('sku') or p.get('id')) for p in await self.rig.store.products(tenant)
                    if p.get('active', True)}
        if len(moments) > 1000:
            raise ValueError('moments_limit')
        cleaned = []
        for m in moments:
            start, end = float(m['start']), float(m['end'])
            if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= body['duration']):
                raise ValueError('timestamp')
            if m['product_id'] not in products or type(m['approved']) is not bool:
                raise ValueError('product')
            cleaned.append({'start': start, 'end': end, 'product_id': m['product_id'], 'approved': m['approved']})
        body.update(revision=revision + 1, moments=cleaned, status='reviewed')
        self.repository.save(tenant, body, expected=revision)
        return body
