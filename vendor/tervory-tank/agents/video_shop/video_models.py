"""Pinned local CPU inference; serving never downloads weights or calls providers."""
from __future__ import annotations

import threading
import wave
from importlib.metadata import version
from pathlib import Path

from .prepare_models import verify

MODEL_ROOT = Path(__file__).with_name('models')
SAMPLE_SECONDS = 5
FRAME_SIZE = 224
VISION_THRESHOLD = 0.85


class ModelsUnavailable(RuntimeError):
    """The room build has missing or invalid local assets."""


class LocalModels:
    """One lazily loaded, serialized CPU model pair per room process."""

    def __init__(self, root: Path = MODEL_ROOT):
        self.root = root
        self.lock = threading.Lock()
        self.whisper = None
        self.clip = None
        self.manifest = None

    def _load(self) -> None:
        if self.whisper is not None:
            return
        try:
            manifest = verify(self.root)
        except ValueError as exc:
            raise ModelsUnavailable('local_models_unavailable') from exc
        import torch
        import open_clip
        from faster_whisper import WhisperModel
        if version('faster-whisper') != '1.2.1' or version('open_clip_torch') != '3.3.0':
            raise ModelsUnavailable('local_model_version')
        torch.set_num_threads(1)
        clip, _, preprocess = open_clip.create_model_and_transforms(
            'ViT-B-32-quickgelu', pretrained=str(self.root / 'clip.safetensors'),
            device='cpu', precision='fp32')
        clip.eval()
        whisper = WhisperModel(str(self.root / 'whisper'), device='cpu', compute_type='int8',
                               cpu_threads=1, num_workers=1, local_files_only=True)
        self.clip, self.preprocess, self.whisper, self.manifest = clip, preprocess, whisper, manifest

    def analyse(self, audio: Path | None, frames: list[bytes], references: dict[str, bytes]) -> dict:
        import torch
        from PIL import Image
        import numpy as np
        with self.lock:
            self._load()
            transcript = []
            language = None
            if audio is not None:
                # FFmpeg already produced bounded mono PCM; avoid a second
                # decoder (and any decoder network/protocol support) in inference.
                with wave.open(str(audio), 'rb') as stream:
                    if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (1, 2, 16000):
                        raise ValueError('audio_format')
                    pcm = stream.readframes(600 * 16000)
                waveform = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0
                segments, info = self.whisper.transcribe(
                    waveform, beam_size=5, vad_filter=True, condition_on_previous_text=False,
                    word_timestamps=False)
                language = info.language
                for segment in segments:
                    if segment.text.strip() and segment.end > segment.start:
                        transcript.append({'start': max(0, segment.start), 'end': min(600, segment.end),
                                           'text': segment.text.strip()})

            def embed(raw: bytes):
                image = Image.frombytes('RGB', (FRAME_SIZE, FRAME_SIZE), raw)
                tensor = self.preprocess(image).unsqueeze(0).to('cpu')
                with torch.inference_mode():
                    return self.clip.encode_image(tensor, normalize=True)[0]

            # Inputs have already been bounded/decoded by FFmpeg. Batches of one
            # bound CPU memory even at the maximum catalogue/video size.
            catalogue = {sku: embed(raw) for sku, raw in references.items()}
            matches = []
            for raw in frames:
                vector = embed(raw)
                matches.append({sku: round(float(vector @ reference), 6)
                                for sku, reference in catalogue.items()
                                if float(vector @ reference) >= VISION_THRESHOLD})
            return {'transcript': transcript, 'matches': matches,
                    'receipt': {'device': 'cpu', 'provider_calls': 0,
                                'speech': 'faster-whisper==1.2.1/tiny/int8',
                                'runtime': {name: version(name) for name in
                                            ('torch', 'torchvision', 'ctranslate2', 'onnxruntime')},
                                'vision': 'open_clip_torch==3.3.0/ViT-B-32-quickgelu/fp32',
                                'weights': {name: value['sha256'] for name, value in self.manifest.items()},
                                'language': language, 'audio': audio is not None,
                                'sample_seconds': SAMPLE_SECONDS, 'vision_threshold': VISION_THRESHOLD}}


MODELS = LocalModels()
