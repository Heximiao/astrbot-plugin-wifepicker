"""Private CPU inference process. No AstrBot imports or chat logging.

Started only after explicit opt-in. The parent installs binary dependencies in
a dedicated venv; downloads use a pinned ONNX model and verified file hashes.
"""

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

MODEL_REPO = "Xenova/distilbert-base-multilingual-cased-sentiments-student"
MODEL_REVISION = "9d9ac661fd7b0b48535a1fc99b20ae6947629e65"
FILES = {
    "onnx/model_quantized.onnx": (
        135953840,
        "9570fdd726d952dbaf559ba0b9f38a523554f08a8038d62d9263a90dcbb3cd3e",
    ),
    # Git blob IDs from the pinned repository (tokenizer is not a Git-LFS file).
    "tokenizer.json": (2919615, "3197aecca8512baa6460d5e19c88b89d1b6465f6"),
}


def retry_delay(error, attempt):
    """Respect server backoff; do not hammer a rate-limited mirror."""
    if isinstance(error, urllib.error.HTTPError) and error.code == 429:
        header = error.headers.get("Retry-After", "") if error.headers else ""
        try:
            delay = float(header)
        except ValueError:
            try:
                delay = parsedate_to_datetime(header).timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                delay = 30 * 2**attempt
        if delay != delay or delay > 60:
            # The parent waits an hour before a new setup attempt. Avoid
            # sending more requests sooner than a long Retry-After demands.
            raise error
        return max(10, delay)
    return 2 * 2**attempt


def valid_file(path, size, expected):
    if not path.is_file() or path.stat().st_size != size:
        return False
    digest = hashlib.sha256() if len(expected) == 64 else hashlib.sha1()
    if len(expected) == 40:
        digest.update(f"blob {size}\0".encode())
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected


def download(root, endpoints):
    if isinstance(endpoints, str):
        endpoints = [endpoints]
    # Reuse the working source for later files in this download run.
    # A new run starts at upstream again; failures are not persisted.
    source_start = 0
    for filename, (size, expected) in FILES.items():
        path = root / filename
        if valid_file(path, size, expected):
            print(json.dumps({"phase": "cache_verified", "file": filename}), flush=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        for index, endpoint in enumerate(endpoints):
            if index < source_start:
                continue
            print(
                json.dumps(
                    {
                        "phase": "download_source",
                        "file": filename,
                        "endpoint": endpoint,
                        "index": index + 1,
                        "count": len(endpoints),
                    }
                ),
                flush=True,
            )
            try:
                download_file(
                    path,
                    filename,
                    size,
                    expected,
                    endpoint,
                    started,
                    index + 1 < len(endpoints),
                )
                source_start = index
                break
            except Exception as exc:
                if index + 1 == len(endpoints) or time.monotonic() - started > 900:
                    raise
                print(
                    json.dumps(
                        {
                            "phase": "source_failed",
                            "file": filename,
                            "endpoint": endpoint,
                            "reason": type(exc).__name__,
                            "http_status": exc.code
                            if isinstance(exc, urllib.error.HTTPError)
                            else None,
                        }
                    ),
                    flush=True,
                )
        print(json.dumps({"phase": "verified", "file": filename}), flush=True)


def download_file(path, filename, size, expected, endpoint, started, has_fallback):
    """Resume the same verified artifact across sources without discarding progress."""
    part = path.with_suffix(path.suffix + ".part")
    url = f"{endpoint.rstrip('/')}/{MODEL_REPO}/resolve/{MODEL_REVISION}/{filename}"
    print(
        json.dumps(
            {
                "phase": "download",
                "file": filename,
                "percent": 0,
                "downloaded": 0,
                "total": size,
            }
        ),
        flush=True,
    )
    for attempt in range(3):
        try:
            if (
                part.exists()
                and part.stat().st_size == size
                and valid_file(part, size, expected)
            ):
                os.replace(part, path)
                break
            offset = part.stat().st_size if part.exists() else 0
            if offset >= size:
                part.unlink()
                offset = 0
            headers = {"User-Agent": "WifePicker-Affinity/1"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=30) as response:
                resume = (
                    offset > 0
                    and response.status == 206
                    and response.headers.get("Content-Range", "").startswith(
                        f"bytes {offset}-"
                    )
                )
                if response.status == 206 and not resume:
                    raise RuntimeError("下载分段响应不匹配")
                downloaded = offset if resume else 0
                last_percent = int(downloaded * 100 / size)
                last_report = time.monotonic()
                if resume:
                    print(
                        json.dumps(
                            {
                                "phase": "resume",
                                "file": filename,
                                "percent": last_percent,
                                "downloaded": downloaded,
                                "total": size,
                            }
                        ),
                        flush=True,
                    )
                with part.open("ab" if resume else "wb") as output:
                    for chunk in iter(lambda: response.read(64 * 1024), b""):
                        downloaded += len(chunk)
                        if downloaded > size or time.monotonic() - started > 900:
                            raise RuntimeError("模型下载超出大小或时间限制")
                        output.write(chunk)
                        percent = int(downloaded * 100 / size)
                        if (
                            percent >= last_percent + 10
                            or time.monotonic() - last_report >= 15
                        ):
                            print(
                                json.dumps(
                                    {
                                        "phase": "download",
                                        "file": filename,
                                        "percent": percent,
                                        "downloaded": downloaded,
                                        "total": size,
                                    }
                                ),
                                flush=True,
                            )
                            last_percent = percent
                            last_report = time.monotonic()
            if not valid_file(part, size, expected):
                part.unlink(missing_ok=True)
                raise RuntimeError("模型文件校验失败")
            os.replace(part, path)
            break
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError) and (
                exc.code in {401, 403, 404} or (exc.code == 429 and has_fallback)
            ):
                raise
            if attempt == 2 or time.monotonic() - started > 900:
                raise
            delay = retry_delay(exc, attempt)
            status = exc.code if isinstance(exc, urllib.error.HTTPError) else None
            print(
                json.dumps(
                    {
                        "phase": "download_retry",
                        "file": filename,
                        "attempt": attempt + 2,
                        "reason": type(exc).__name__,
                        "http_status": status,
                        "wait_seconds": delay,
                    }
                ),
                flush=True,
            )
            time.sleep(delay)


def run(root, endpoint):
    download(root, endpoint)
    print(json.dumps({"phase": "loading_cpu_model"}), flush=True)
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=128)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(root / "onnx/model_quantized.onnx"),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        try:
            text = str(json.loads(line)["text"])[:512]
            encoded = tokenizer.encode(text)
            values = {
                "input_ids": encoded.ids,
                "attention_mask": encoded.attention_mask,
            }
            inputs = {
                item.name: np.asarray([values[item.name]], dtype=np.int64)
                for item in session.get_inputs()
            }
            logits = session.run(None, inputs)[0][0]
            probabilities = np.exp(logits - np.max(logits))
            probabilities /= probabilities.sum()
            # The pinned config maps 0=positive, 1=neutral, 2=negative.
            print(
                json.dumps(
                    dict(
                        zip(
                            ("positive", "neutral", "negative"),
                            map(float, probabilities),
                            strict=True,
                        )
                    )
                ),
                flush=True,
            )
        except Exception:
            print(json.dumps({"error": "情感分类失败"}), flush=True)


if __name__ == "__main__":
    try:
        argument = sys.argv[2]
        endpoints = json.loads(argument) if argument.startswith("[") else [argument]
        run(Path(sys.argv[1]), endpoints)
    except Exception as exc:
        # Do not include user text, mirror credentials or tracebacks in output.
        print(
            json.dumps({"error": type(exc).__name__, "detail": str(exc)[:500]}),
            flush=True,
        )
        sys.exit(1)
