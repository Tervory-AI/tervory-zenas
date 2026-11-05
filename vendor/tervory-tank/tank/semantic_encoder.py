"""Pinned, CPU-only FastEmbed model. Downloads happen only in the image build."""
from pathlib import Path
from threading import Lock

MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODEL_REPOSITORY = "Qdrant/bge-small-en-v1.5-onnx-Q"
MODEL_REVISION = "52398278842ec682c6f32300af41344b1c0b0bb2"
MODEL_PATH = Path("/opt/tank-semantic-model")
DIMENSIONS = 384
# Changing the model, matching guards or threshold requires fresh evidence/approval.
ENCODER_ID = f"{MODEL_REPOSITORY}@{MODEL_REVISION}:guards-v1:cosine-0.90"
FILES = ("model_optimized.onnx", "config.json", "tokenizer.json",
         "tokenizer_config.json", "special_tokens_map.json")


class LocalEncoder:
    def __init__(self) -> None:
        self._model = None
        self._tokenizer = None
        self._lock = Lock()

    def _validate(self, text: str) -> None:
        if self._tokenizer is None:
            if (not (MODEL_PATH / "tokenizer.json").is_file()
                    or not (MODEL_PATH / "revision").is_file()
                    or (MODEL_PATH / "revision").read_text().strip() != MODEL_REVISION):
                raise RuntimeError("Pinned semantic model is missing from the image")
            from tokenizers import Tokenizer
            self._tokenizer = Tokenizer.from_file(str(MODEL_PATH / "tokenizer.json"))
            self._tokenizer.no_truncation()
        if len(self._tokenizer.encode(text).ids) > 512:
            raise ValueError("Semantic input exceeds encoder context")

    def validate(self, text: str) -> None:
        """Check the pinned tokenizer's full input, including special tokens.

        Validation loads no model and performs no inference or download.
        """
        with self._lock:
            self._validate(text)

    def embed(self, text: str) -> list[float]:
        # Called in a worker thread. Serialize lazy initialization and CPU work.
        with self._lock:
            self._validate(text)
            if self._model is None:
                if (not all((MODEL_PATH / name).is_file() for name in FILES)
                        or (MODEL_PATH / "revision").read_text().strip() != MODEL_REVISION):
                    raise RuntimeError("Pinned semantic model is missing from the image")
                from fastembed import TextEmbedding
                self._model = TextEmbedding(
                    model_name=MODEL_NAME, specific_model_path=str(MODEL_PATH),
                    local_files_only=True, cuda=False, threads=2,
                )
            return [float(value) for value in next(iter(self._model.embed([text])))]


def download_for_image() -> None:
    """Docker build only; public artifacts, no credential lookup or remote code."""
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=MODEL_REPOSITORY, revision=MODEL_REVISION,
                      local_dir=str(MODEL_PATH), allow_patterns=list(FILES) + ["vocab.txt", "ort_config.json"],
                      token=False)
    if not all((MODEL_PATH / name).is_file() for name in FILES):
        raise RuntimeError("Incomplete semantic model download")
    (MODEL_PATH / "revision").write_text(MODEL_REVISION + "\n")


if __name__ == "__main__":
    download_for_image()
