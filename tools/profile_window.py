#!/usr/bin/env python3
"""拆解「一个 10 秒窗口」的耗时，找出优化空间。

分三部分量：
  1. 纯识别（NPU 上的 encoder/predictor/decoder + CPU 上的特征提取）
  2. 纯标点（CT-Transformer，纯 CPU）
  3. 前端读文件/重采样

同时记录 **进程 CPU 时间**：CPU/墙钟 越接近 0 说明越省 CPU，
越接近 1 说明瓶颈在 CPU 而不是 NPU。

用法: profile_window.py [--repeat 10] [--wav xxx.wav]
"""

import argparse
import time
from pathlib import Path

import numpy as np
import sherpa_onnx
import soundfile as sf

import _common

ASR_DIR = _common.require_model(_common.ASR_10S)
PUNC = _common.require_model(_common.PUNC) / "model.onnx"
SAMPLE_RATE = 16000


def cpu_times():
    t = time.process_time()
    return t


def bench(label, fn, repeat):
    fn()  # 热身
    walls, cpus = [], []
    for _ in range(repeat):
        w0, c0 = time.perf_counter(), cpu_times()
        result = fn()
        walls.append((time.perf_counter() - w0) * 1000)
        cpus.append((cpu_times() - c0) * 1000)
    wall = float(np.mean(walls))
    cpu = float(np.mean(cpus))
    print(f"  {label:<38} 墙钟 {wall:7.1f} ms   CPU {cpu:7.1f} ms   "
          f"CPU/墙钟 {cpu / wall:.2f}")
    return wall, cpu, result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--wav", default="")
    args = parser.parse_args()

    if args.wav:
        wav = Path(args.wav)
    else:
        candidates = sorted((ASR_DIR / "test_wavs").glob("*.wav"))
        wav = candidates[0] if candidates else None
    if wav is None or not wav.is_file():
        print("找不到测试 wav，请用 --wav 指定")
        return 1

    samples, rate = sf.read(wav, dtype="float32")
    if samples.ndim > 1:
        samples = samples[:, 0]
    duration = len(samples) / rate
    print(f"测试音频: {wav.name}  {duration:.2f} 秒\n")

    print("载入模型…")
    t0 = time.perf_counter()
    recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=_common.model_files(_common.ASR_10S),
        tokens=str(ASR_DIR / "tokens.txt"),
        provider="rknn", num_threads=1, debug=False)
    load_ms = (time.perf_counter() - t0) * 1000

    def decode():
        stream = recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        recognizer.decode_stream(stream)
        return stream.result.text

    text = decode()

    t0 = time.perf_counter()
    punc_cfg = sherpa_onnx.OfflinePunctuationConfig()
    punc_cfg.model.ct_transformer = str(PUNC)
    punc_cfg.model.num_threads = 4
    punctuator = sherpa_onnx.OfflinePunctuation(punc_cfg)
    punc_load_ms = (time.perf_counter() - t0) * 1000

    print(f"  模型载入: 识别 {load_ms:.0f} ms / 标点 {punc_load_ms:.0f} ms\n")

    print(f"重复 {args.repeat} 次：")
    w_decode, c_decode, _ = bench("1. 识别（NPU 推理）", decode, args.repeat)
    w_punc, c_punc, _ = bench("2. 标点（CT-Transformer，CPU）",
                              lambda: punctuator.add_punctuation(text), args.repeat)

    print("\n结论：")
    print(f"  一个窗口总墙钟 ≈ {w_decode + w_punc:.0f} ms"
          f"（识别 {w_decode / (w_decode + w_punc) * 100:.0f}% + "
          f"标点 {w_punc / (w_decode + w_punc) * 100:.0f}%）")
    print(f"  识别部分的 CPU/墙钟 = {c_decode / w_decode:.2f} "
          f"→ {'NPU 是瓶颈，CPU 有余' if c_decode / w_decode < 0.5 else 'CPU 开销偏高，值得查'}")
    print(f"  识别文本: {text[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
