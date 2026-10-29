"""Optional automatic model installation, isolated from AstrBot dependencies."""

import asyncio
import math
import os
import sys
import time
import json
from pathlib import Path
from urllib.parse import urlparse


def https_endpoint(value):
    url = urlparse(str(value).strip())
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("镜像地址必须为不含账号、查询参数的 HTTPS 地址")
    return str(value).strip().rstrip("/")


def download_sources(config):
    """Try upstream, project R2, then configured and built-in mirrors."""
    endpoints = ["https://huggingface.co", "https://models.heximiao.com",
                 config.get("model_mirror", "https://models.heximiao.com"),
                 "https://hf-mirror.com", "https://aifasthub.com", "https://hf-mirror.net"]
    return list(dict.fromkeys(https_endpoint(endpoint) for endpoint in endpoints))


def progress_message(data):
    """Compact console messages, independent of debug mode."""
    phase = data.get("phase")
    filename = data.get("file", "")
    if phase == "download_source":
        return f"尝试下载源 {data.get('index')}/{data.get('count')}：{data.get('endpoint')}（{filename}）"
    if phase == "source_failed":
        reason = f"HTTP {data['http_status']}" if data.get("http_status") else data.get("reason", "未知错误")
        return f"当前源不可用：{data.get('endpoint')}，原因 {reason}；自动尝试下一个下载源"
    if phase in {"download", "resume"}:
        downloaded = data.get("downloaded", 0) / (1024 * 1024)
        total = data.get("total", 0) / (1024 * 1024)
        action = "断点续传" if phase == "resume" else "下载"
        return f"{action} {filename}：{data.get('percent', 0)}%（{downloaded:.1f}/{total:.1f} MiB）"
    if phase == "download_retry":
        reason = f"HTTP {data['http_status']}" if data.get("http_status") else data.get("reason", "未知错误")
        return (f"下载重试 {filename}：{reason}，等待 {data.get('wait_seconds', 0):g} 秒，"
                f"准备第 {data.get('attempt')}/3 次尝试")
    if phase == "cache_verified":
        return f"使用已校验的模型缓存：{filename}"
    if phase == "verified":
        return f"下载完成并通过校验：{filename}"
    if phase == "loading_cpu_model":
        return "模型文件已就绪，正在加载 CPU 推理引擎"
    return f"模型准备：{phase}"


class LocalSentiment:
    def __init__(self, data_dir, logger, debug=None):
        self.root = Path(data_dir) / "affinity_model"
        self.logger = logger
        self.debug = debug or (lambda message: None)
        self.task = None
        self.process = None
        self.install_process = None
        self.ready = False
        self.retry_at = 0
        self.status = "未启用"
        self.lock = asyncio.Lock()

    def ensure(self, config):
        if self.task is None and not self.ready and time.monotonic() >= self.retry_at:
            self.status = "准备中（自动下载模型和独立运行依赖）"
            self.logger.info("[wifepicker.affinity.model] 小模型已启用，开始后台准备；期间基础计分照常工作")
            self.task = asyncio.create_task(self._prepare(dict(config)))

    async def _command(self, *args):
        flags = {"creationflags": 0x08000000} if os.name == "nt" else {}
        self.install_process = await asyncio.create_subprocess_exec(
            *map(str, args), stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, **flags,
        )
        tail = []

        async def consume():
            while True:
                line = await self.install_process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()[:1500]
                self.debug(f"runtime_install {text}")
                tail.append(text)
                del tail[:-5]
            return await self.install_process.wait()

        result = await asyncio.wait_for(consume(), timeout=600)
        if result:
            self.logger.warning(f"[wifepicker.affinity.model] 运行依赖安装失败 exit={result}; {' | '.join(tail)[-2000:]}")
            raise RuntimeError("独立运行环境安装失败")
        self.install_process = None

    async def _prepare(self, config):
        try:
            sources = download_sources(config)
            index = https_endpoint(config.get("pip_mirror", "https://pypi.tuna.tsinghua.edu.cn/simple"))
            env = self.root / "runtime"
            python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            marker = env / ".affinity_dependencies_v1"
            self.root.mkdir(parents=True, exist_ok=True)
            self.debug(f"model_setup root={self.root} download_sources={sources} pip_mirror={index}")
            if not python.exists():
                self.logger.info("[wifepicker.affinity.model] 正在创建独立 Python 运行环境")
                await self._command(sys.executable, "-m", "venv", str(env))
            if not marker.exists():
                self.logger.info("[wifepicker.affinity.model] 正在通过 PyPI 镜像安装 CPU 推理依赖")
                await self._command(python, "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
                                    "--no-input", "--only-binary=:all:", "--retries", "1", "--timeout", "30",
                                    "--index-url", index, "onnxruntime>=1.20,<2", "tokenizers>=0.21,<1", "numpy>=1.26,<3")
                marker.write_text("CPU ONNX runtime v1\n", encoding="utf-8")
            worker = Path(__file__).with_name("model_worker.py")
            flags = {"creationflags": 0x08000000} if os.name == "nt" else {}
            self.process = await asyncio.create_subprocess_exec(
                str(python), "-I", "-u", str(worker), str(self.root / "weights"), json.dumps(sources),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, **flags,
            )
            async def bootstrap():
                while True:
                    line = await self.process.stdout.readline()
                    if not line:
                        raise RuntimeError("模型工作进程提前退出")
                    reply = json.loads(line)
                    if reply.get("ready"):
                        return
                    if reply.get("error"):
                        self.logger.warning(f"[wifepicker.affinity.model] 模型准备失败: {reply['error']} {reply.get('detail', '')}")
                        raise RuntimeError("模型下载或加载失败")
                    self.logger.info(f"[wifepicker.affinity.model] {progress_message(reply)}")
            await asyncio.wait_for(bootstrap(), timeout=1900)
            self.ready = True
            self.status = "本地 CPU 模型已就绪"
            self.logger.info("[wifepicker] 好感度本地情感模型已就绪，推理不发送聊天到外部服务")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status = "模型未就绪，使用基础计分；请检查镜像、磁盘和 Python venv/pip 支持"
            self.retry_at = time.monotonic() + 3600
            self.logger.warning(f"[wifepicker] 好感度模型准备失败 ({type(exc).__name__})，一小时后重试；基础计分继续工作")
        finally:
            if not self.ready:
                await self._stop_processes()
            self.task = None

    async def classify(self, text):
        if not self.ready:
            return None
        async with self.lock:
            try:
                started = time.monotonic()
                # ASCII JSON also works when a Windows child inherits a GBK
                # console locale: JSON decoding restores the original Unicode.
                self.process.stdin.write((json.dumps({"text": text}) + "\n").encode("ascii"))
                await self.process.stdin.drain()
                data = json.loads(await asyncio.wait_for(self.process.stdout.readline(), timeout=15))
                values = {key: float(data[key]) for key in ("positive", "neutral", "negative")}
                if not all(math.isfinite(v) and 0 <= v <= 1 for v in values.values()) or abs(sum(values.values()) - 1) > 0.02:
                    self.debug("sentiment_invalid_probabilities; contribution_skipped=true")
                    return None
                self.debug(f"local_inference chars={len(text)} elapsed_ms={(time.monotonic() - started) * 1000:.1f} probabilities={values}")
                return values
            except Exception as exc:
                self.ready = False
                self.status = "模型推理失败，使用基础计分"
                self.retry_at = time.monotonic() + 3600
                self.logger.warning(f"[wifepicker.affinity.model] 推理失败 ({type(exc).__name__})，已回退基础计分")
                await self._stop_processes()
                return None

    async def _stop_processes(self):
        for process in (self.process, self.install_process):
            if process is not None and process.returncode is None:
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=3)
                except (ProcessLookupError, asyncio.TimeoutError):
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
        self.process = self.install_process = None

    async def close(self):
        self.debug("model_stop cancelling_setup_and_releasing_cpu_worker=true")
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        async with self.lock:
            await self._stop_processes()
            self.ready = False
            self.status = "未启用"
