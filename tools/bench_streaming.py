#!/usr/bin/env python3
"""量 VocoType 流式 worker 的实时余量。

流式是硬实时的：核心每 600ms 送一块音频（chunk_size[1]=10 × 60ms），
worker 必须在这 600ms 内算完，否则文字越拖越远 = 卡。

用法: bench_streaming.py 音频.wav [--threads 1,2,4] [--seconds 30]
"""

import argparse
import base64
import json
import subprocess
import sys
import time

import numpy as np
import soundfile as sf

import _common

WORKER = _common.streaming_worker()
MODEL = _common.streaming_model()
CHUNK_SIZE = (5, 10, 5)
CHUNK_SAMPLES = CHUNK_SIZE[1] * 960  # 9600 = 600ms


def run(wav, threads, seconds):
    samples, rate = sf.read(wav, dtype="float32")
    if samples.ndim > 1:
        samples = samples[:, 0]
    assert rate == 16000, rate
    samples = samples[: int(seconds * rate)]
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()

    proc = subprocess.Popen(
        [str(WORKER), "--model-dir", str(MODEL), "--threads", str(threads),
         "--chunk-size", *map(str, CHUNK_SIZE),
         "--idle-timeout-ms", "600000", "--session-idle-timeout-ms", "600000"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def send(payload):
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    ready = json.loads(proc.stdout.readline())
    if not ready.get("success"):
        print("  启动失败:", ready)
        return None

    session = send({"type": "start"})["session_id"]

    timings = []
    text = ""
    offset = 0
    started = time.monotonic()
    while offset < len(pcm):
        block = pcm[offset:offset + CHUNK_SAMPLES * 2]
        is_last = offset + CHUNK_SAMPLES * 2 >= len(pcm)
        t0 = time.monotonic()
        reply = send({"type": "feed", "session_id": session,
                      "pcm16": base64.b64encode(block).decode(),
                      "is_final": is_last})
        timings.append((time.monotonic() - t0) * 1000)
        text = reply.get("text", "")
        offset += CHUNK_SAMPLES * 2
    wall = time.monotonic() - started
    send({"type": "stop"})
    proc.wait(timeout=10)

    audio_ms = len(pcm) / 2 / 16000 * 1000
    steady = timings[1:]  # 第一块含热身
    return {
        "threads": threads,
        "audio_s": audio_ms / 1000,
        "chunks": len(timings),
        "mean_ms": float(np.mean(steady)),
        "max_ms": float(np.max(steady)),
        "wall_s": wall,
        "rtf": wall / (audio_ms / 1000),
        "text": text,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wav")
    parser.add_argument("--threads", default="1,4")
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args()

    print(f"音频: {args.wav}  取前 {args.seconds:.0f} 秒")
    print(f"块大小: {CHUNK_SAMPLES} 采样 = {CHUNK_SAMPLES/16000*1000:.0f} ms\n")
    for threads in (int(t) for t in args.threads.split(",")):
        result = run(args.wav, threads, args.seconds)
        if not result:
            continue
        # 关键指标：每块平均耗时 / 600ms。>1 就是跟不上。
        headroom = CHUNK_SAMPLES / 16000 * 1000 / result["mean_ms"]
        flag = "✅ 跟得上" if headroom > 1.2 else ("⚠️ 勉强" if headroom > 1 else "❌ 跟不上")
        print(f"threads={result['threads']}  "
              f"平均 {result['mean_ms']:.0f} ms/块  最大 {result['max_ms']:.0f} ms  "
              f"→ 实时余量 {headroom:.2f}×  {flag}")
        print(f"    RTF {result['rtf']:.3f}   文字: {result['text'][-60:]}\n")


if __name__ == "__main__":
    sys.exit(main())
