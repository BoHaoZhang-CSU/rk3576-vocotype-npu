#!/usr/bin/env python3
"""工具脚本共用的路径解析。

每个位置都能用环境变量覆盖，默认值按 install.sh 装出来的样子排。换机器只改
环境变量，不用改脚本。

  VOCOTYPE_NPU_MODELS_DIR  NPU 模型目录（默认 ~/.local/share/vocotype-npu/models）
  VOCOTYPE_NPU_PYTHON      跑 NPU worker 的解释器（默认仓库里的 .venv）
  VOCOTYPE_NPU_WORKER      NPU worker 路径（默认仓库里的 worker/）
  VOCOTYPE_CPU_WORKER      原版 CPU 离线 worker
  VOCOTYPE_STREAMING_WORKER / VOCOTYPE_STREAMING_MODEL
                           原版流式 worker 及其模型（只给 bench_streaming.py 用）
  VOCOTYPE_FUNASR_MODELS   原版 CPU worker 用的 FunASR 模型根目录
"""

import os
import sys
from pathlib import Path

HOME = Path.home()

ASR_10S = "sherpa-onnx-rk3576-10-seconds-paraformer-zh-2023-03-28"
ASR_5S = "sherpa-onnx-rk3576-5-seconds-paraformer-zh-2023-03-28"
PUNC = "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12"


def _env(name):
    return os.environ.get(name, "").strip()


def repo_root():
    return Path(__file__).resolve().parent.parent


def model_roots():
    """按优先级列出可能的模型根目录（必须和 worker 里的 _model_roots 一致）。"""
    roots = []
    if _env("VOCOTYPE_NPU_MODELS_DIR"):
        roots.append(Path(_env("VOCOTYPE_NPU_MODELS_DIR")))
    roots.append(HOME / ".local" / "share" / "vocotype-npu" / "models")
    roots.append(HOME / "rknn-lab" / "paraformer" / "prebuilt")  # 开发机上的老路径
    return roots


def find_model(name):
    for root in model_roots():
        candidate = root / name
        if candidate.is_dir():
            return candidate
    return None


def require_model(name):
    found = find_model(name)
    if found is None:
        looked = "\n  ".join(str(r / name) for r in model_roots())
        raise SystemExit(f"找不到模型 {name}，找过这些地方：\n  {looked}\n"
                         f"跑 ./install.sh 下载，或用 VOCOTYPE_NPU_MODELS_DIR 指路。")
    return found


def model_files(name, filenames=("encoder.rknn", "predictor.rknn", "decoder.rknn")):
    """给 sherpa-onnx 的 paraformer= 参数用的逗号分隔路径。"""
    return ",".join(str(require_model(name) / f) for f in filenames)


def npu_worker():
    if _env("VOCOTYPE_NPU_WORKER"):
        return Path(_env("VOCOTYPE_NPU_WORKER"))
    return repo_root() / "worker" / "vocotype-offline-worker-npu"


def npu_python():
    """装了带 RKNN 的 sherpa-onnx 的解释器。"""
    if _env("VOCOTYPE_NPU_PYTHON"):
        return _env("VOCOTYPE_NPU_PYTHON")
    venv = repo_root() / ".venv" / "bin" / "python3"
    return str(venv) if venv.is_file() else sys.executable


def cpu_worker():
    if _env("VOCOTYPE_CPU_WORKER"):
        return Path(_env("VOCOTYPE_CPU_WORKER"))
    return HOME / ".local" / "lib" / "vocotype-streaming" / "bin" / "vocotype-offline-worker"


def funasr_models():
    if _env("VOCOTYPE_FUNASR_MODELS"):
        return Path(_env("VOCOTYPE_FUNASR_MODELS"))
    return HOME / ".cache" / "modelscope" / "hub" / "models" / "iic"


def streaming_worker():
    if _env("VOCOTYPE_STREAMING_WORKER"):
        return Path(_env("VOCOTYPE_STREAMING_WORKER"))
    return HOME / ".local" / "lib" / "vocotype-streaming" / "bin" / "vocotype-streaming-worker"


def streaming_model():
    if _env("VOCOTYPE_STREAMING_MODEL"):
        return Path(_env("VOCOTYPE_STREAMING_MODEL"))
    return funasr_models() / ("speech_paraformer-large_asr_nat-zh-cn-16k-common-"
                              "vocab8404-online-onnx")
