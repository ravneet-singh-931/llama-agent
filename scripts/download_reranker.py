"""Download the ONNX cross-encoder used by the RAG reranker.

    python scripts/download_reranker.py <target_dir>

Fetches ms-marco-MiniLM-L-6-v2 (int8 ONNX export, ~23 MB) and its tokenizer
from Hugging Face at a PINNED revision and verifies each file's hash, so every
build gets byte-identical model files. No PyTorch: the model runs on
onnxruntime + tokenizers, which chromadb already installs.

Used by the Dockerfile (model baked into the image -> pods never download at
runtime) and by CI (real-model reranker test). Exits non-zero on failure.
"""

import hashlib
import os
import sys
import time
import urllib.request

REPO = "Xenova/ms-marco-MiniLM-L-6-v2"
REVISION = "a09144355adeed5f58c8ed011d209bf8ee5a1fec"

# file in the repo -> (hash kind, expected value). LFS files publish a
# sha256; small files are checked against their git blob id (sha1).
FILES = {
    "onnx/model_quantized.onnx": (
        "sha256", "e9d8ebf845c413e981c175bfe49a3bfa9b3dcce2a3ba54875ee5df5a58639fbe"),
    "tokenizer.json": ("git-sha1", "688882a79f44442ddc1f60d70334a7ff5df0fb47"),
}


def _digest(kind: str, data: bytes) -> str:
    if kind == "sha256":
        return hashlib.sha256(data).hexdigest()
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def _fetch(url: str, attempts: int = 3) -> bytes:
    for attempt in range(1, attempts + 1):
        try:
            # Fixed https URL to huggingface.co (not user input).
            with urllib.request.urlopen(url, timeout=120) as resp:  # nosec B310
                return resp.read()
        except Exception as e:
            if attempt == attempts:
                raise
            print(f"  retry {attempt}/{attempts - 1} after error: {e}")
            time.sleep(3 * attempt)
    raise RuntimeError("unreachable")


def download(target: str) -> None:
    os.makedirs(target, exist_ok=True)
    for name, (kind, expected) in FILES.items():
        dest = os.path.join(target, os.path.basename(name))
        if os.path.isfile(dest):
            with open(dest, "rb") as f:
                if _digest(kind, f.read()) == expected:
                    print(f"ok (cached) {dest}")
                    continue
        url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}"
        print(f"downloading {url}")
        data = _fetch(url)
        got = _digest(kind, data)
        if got != expected:
            raise SystemExit(f"hash mismatch for {name}: expected {expected}, got {got}")
        with open(dest, "wb") as f:
            f.write(data)
        print(f"ok {dest} ({len(data) / 1e6:.1f} MB)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    download(sys.argv[1])
