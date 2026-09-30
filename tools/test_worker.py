#!/usr/bin/env python3
"""直接驱动 NPU worker（不经过 vocotype-core），测各长度音频的耗时。

用法: <装 sherpa-onnx 的 python> test_worker.py 音频.wav [更多 wav ...]

worker 用**当前这个 python** 启动，所以要用装了 sherpa-onnx 的解释器跑本脚本
（install.sh 建的那个 venv）。找到标点模型就自动带上，测出来的就是
VocoType 平时那条路径。
"""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import soundfile as sf

import _common

WORKER = _common.npu_worker()


def drain(stream, sink):
    """后台线程把 worker 的 stderr 日志抽干，防止管道写满卡死。"""
    for line in stream:
        line = line.rstrip()
        if line:
            sink.append(line)


def main():
    wavs = [Path(p) for p in sys.argv[1:]]
    if not wavs:
        print(__doc__)
        return 1

    # 标点模型有就带上：只测识别测不出标点坏没坏，而 VocoType 平时是开标点的。
    command = [sys.executable, str(WORKER)]
    punc = _common.find_model(_common.PUNC)
    if punc is not None:
        command += ["--punc-model-dir", str(punc)]

    proc = subprocess.Popen(command, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1)
    logs = []
    threading.Thread(target=drain, args=(proc.stderr, logs), daemon=True).start()

    started = time.monotonic()
    ready = json.loads(proc.stdout.readline())
    print(f"就绪用时 {time.monotonic() - started:.1f}s  {ready}\n")
    if not ready.get("success"):
        return 1

    def request(payload):
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    # 等后台的可选模型加载完，否则第一个短句会退回 10 秒档，测出来不准。
    # parallel_chunks=1 时不打任何日志，所以还得有个超时兜底。
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not any(
            "并行解码" in line or "仍串行" in line or "秒档模型加载完成" in line
            for line in logs):
        time.sleep(0.2)
    for line in logs:
        print("  " + line)
    logs.clear()

    print()
    for wav in wavs:
        duration = sf.info(wav).duration
        t0 = time.monotonic()
        reply = request({"type": "transcribe", "audio_path": str(wav.resolve()),
                         "itn": True})
        wall = (time.monotonic() - t0) * 1000

        if not reply.get("success"):
            print(f"{wav.name}: 失败 {reply.get('error')}")
            continue
        latency = reply.get("latency_ms", 0)
        print(f"--- {wav.name}  {duration:.2f}s")
        for line in logs:
            print(f"    {line}")
        logs.clear()
        print(f"    往返 {wall:.0f} ms | worker {latency:.0f} ms | "
              f"RTF {latency / 1000 / duration:.3f}")
        print(f"    {reply.get('text', '')[:90]}")

    request({"type": "stop"})
    proc.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
