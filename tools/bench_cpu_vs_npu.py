#!/usr/bin/env python3
"""同一段音频，分别用 CPU 版 worker 和 NPU 版 worker 识别，对比墙钟和 CPU 时间。

跑的是 VocoType 真正会拉起的那两个 worker，参数也照抄 vocotype-core
（offline_asr.cpp:worker_arguments），所以数字是端到端的，不是裸跑模型。

用法: bench_cpu_vs_npu.py 音频.wav [更多.wav ...]
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import soundfile as sf

import _common

NPU_WORKER = _common.npu_worker()
CPU_WORKER = _common.cpu_worker()
MODELS = _common.funasr_models()
ASR_DIR = MODELS / "speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-onnx"
PUNC_DIR = MODELS / "punc_ct-transformer_zh-cn-common-vocab272727-onnx"
TICKS = os.sysconf("SC_CLK_TCK")


def cpu_seconds(pid):
    """进程累计 CPU 时间（用户+系统），单位秒。"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return 0.0
    fields = stat[stat.rindex(")") + 2:].split()
    return (int(fields[11]) + int(fields[12])) / TICKS


def rss_mb(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


class Runner:
    def __init__(self, label, command):
        self.label = label
        self.command = command
        self.proc = None
        self.logs = []

    def start(self):
        self.proc = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1)
        threading.Thread(target=self._drain, args=(self.proc.stderr,),
                         daemon=True).start()
        started = time.monotonic()
        ready = json.loads(self.proc.stdout.readline())
        self.load_seconds = time.monotonic() - started
        if not ready.get("success"):
            raise RuntimeError(f"{self.label} 启动失败: {ready}")
        return self.load_seconds

    def _drain(self, stream):
        for line in stream:
            line = line.rstrip()
            if line:
                self.logs.append(line)

    def transcribe(self, wav):
        cpu_before = cpu_seconds(self.proc.pid)
        t0 = time.monotonic()
        self.proc.stdin.write(json.dumps(
            {"type": "transcribe", "audio_path": str(wav.resolve()), "itn": True}) + "\n")
        self.proc.stdin.flush()
        reply = json.loads(self.proc.stdout.readline())
        wall = time.monotonic() - t0
        return reply, wall, cpu_seconds(self.proc.pid) - cpu_before, rss_mb(self.proc.pid)

    def stop(self):
        if not self.proc:
            return
        try:
            self.proc.stdin.write(json.dumps({"type": "stop"}) + "\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=30)
        except Exception:
            self.proc.kill()


def worker_args(binary, threads):
    # NPU worker 是 Python 脚本，要用装了 sherpa-onnx 的解释器起；
    # 原版 CPU worker 是可直接执行的二进制，直接跑。
    launcher = [_common.npu_python()] if Path(binary) == NPU_WORKER else []
    return launcher + [str(binary), "--asr-model-dir", str(ASR_DIR),
                       "--threads", str(threads), "--idle-timeout-ms", "600000",
                       "--punc-model-dir", str(PUNC_DIR)]


def measure(label, binary, threads, wavs, warmup_seconds):
    """跑一个 worker，返回 {wav: (文字, 墙钟, CPU时间)}。

    两个 worker 必须**轮流**跑而不是同时跑：光它们自己就要 1.8GB / 2.9GB，
    再加上常驻的那个 NPU worker（2.9GB），同机会把内存挤爆，rknn 直接
    `failed to malloc npu memory` 起不来。
    """
    runner = Runner(label, worker_args(binary, threads))
    print(f"{label}: 加载 {runner.start():.1f}s "
          f"(常驻 {rss_mb(runner.proc.pid):.0f} MB)")
    time.sleep(warmup_seconds)  # 等后台把可选模型加载完，否则第一个短句会走另一条路径
    for line in runner.logs:
        print(f"    [{label}] {line}")
    runner.logs.clear()

    results = {}
    for wav in wavs:
        for attempt in range(2):  # 第一次带上懒初始化的开销，再测一次
            reply, wall, cpu, _ = runner.transcribe(wav)
            time.sleep(1)
        if not reply.get("success"):
            print(f"    {wav.name} 失败: {reply.get('error')}")
        results[wav] = (reply.get("text", ""), wall, cpu)
    runner.stop()
    print()
    return results


def main():
    wavs = [Path(p) for p in sys.argv[1:]]
    if not wavs:
        print(__doc__)
        return 1

    # 常驻的 NPU worker 已经占着 NPU 内存了，这时再起第二个会 `failed to malloc
    # npu memory` 并且**直接 exit(255)**（sherpa 的 C++ 层硬退出，Python 的
    # try/except 接不住）。所以 ONLY=cpu 可以单独跑 CPU 那一半。
    only = os.environ.get("ONLY", "").lower()
    threads = 4
    if only != "npu":
        cpu_results = measure(f"CPU {threads} 线程", CPU_WORKER, threads, wavs, 3)
    else:
        return 0
    if only == "cpu":
        print(f"{'音频':>8}  {'耗时':>9} {'CPU时间':>10}  RTF")
        for wav in wavs:
            text, wall, cpu = cpu_results[wav]
            duration = sf.info(wav).duration
            print(f"{duration:6.1f}s  {wall * 1000:8.0f}ms {cpu * 1000:9.0f}ms  "
                  f"{wall / duration:.3f}   {text[:40]}")
        return 0
    npu_results = measure("NPU", NPU_WORKER, threads, wavs, 25)

    print(f"{'音频':>8}  {'CPU 4线程':>22}  {'NPU':>22}   加速比")
    print(f"{'':>8}  {'耗时':>9} {'CPU时间':>10}  {'耗时':>9} {'CPU时间':>10}")
    for wav in wavs:
        duration = sf.info(wav).duration
        cpu_text, cpu_wall, cpu_cpu = cpu_results[wav]
        npu_text, npu_wall, npu_cpu = npu_results[wav]
        print(f"{duration:6.1f}s  {cpu_wall * 1000:8.0f}ms {cpu_cpu * 1000:9.0f}ms  "
              f"{npu_wall * 1000:8.0f}ms {npu_cpu * 1000:9.0f}ms   "
              f"{cpu_wall / npu_wall:5.2f}×")
        print(f"          CPU: {cpu_text[:60]}")
        print(f"          NPU: {npu_text[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
