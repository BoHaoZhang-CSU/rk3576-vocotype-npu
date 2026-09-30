#!/usr/bin/env python3
"""测 RK3576 的 NPU 到底能不能并行。

做法：开 N 个独立的 RKNN 上下文（各自一份模型），同时跑识别。
  - 每个上下文的耗时几乎不变 → 有 N 个核 / 能并行，吞吐随 N 线性增长
  - 耗时涨到 N 倍           → 只有 1 个核，纯粹在抢

用法: test_parallel.py [--workers 1,2,3] [--repeat 5]
"""

import argparse
import statistics
import threading
import time

import sherpa_onnx
import soundfile as sf

import _common

ASR_DIR = _common.require_model(_common.ASR_10S)
SAMPLE_RATE = 16000


def make_recognizer():
    return sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=_common.model_files(_common.ASR_10S),
        tokens=str(ASR_DIR / "tokens.txt"),
        provider="rknn", num_threads=1, debug=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", default="1,2,3")
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()

    wav = sorted((ASR_DIR / "test_wavs").glob("*.wav"))[0]
    samples, rate = sf.read(wav, dtype="float32")
    if samples.ndim > 1:
        samples = samples[:, 0]
    print(f"音频 {wav.name}  {len(samples)/rate:.2f} 秒，"
          f"每档跑 {args.repeat} 轮\n")

    print("预热 + 建立上下文…")
    warmup = make_recognizer()
    stream = warmup.create_stream()
    stream.accept_waveform(SAMPLE_RATE, samples)
    warmup.decode_stream(stream)
    del warmup, stream
    print("  就绪\n")

    baseline = None
    print(f"{'并发数':>6}  {'每轮墙钟':>10}  {'单次识别':>10}  {'相对单核':>10}  吞吐")
    for workers in (int(w) for w in args.workers.split(",")):
        recognizers = [make_recognizer() for _ in range(workers)]
        times = [[] for _ in range(workers)]
        barrier = threading.Barrier(workers)

        def work(index):
            recognizer = recognizers[index]
            barrier.wait()  # 让所有线程同时开跑
            for _ in range(args.repeat):
                stream = recognizer.create_stream()
                stream.accept_waveform(SAMPLE_RATE, samples)
                t0 = time.perf_counter()
                recognizer.decode_stream(stream)
                times[index].append((time.perf_counter() - t0) * 1000)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(workers)]
        t0 = time.perf_counter()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        round_wall = (time.perf_counter() - t0) * 1000

        per_call = statistics.mean(statistics.mean(t) for t in times)
        if baseline is None:
            baseline = per_call
        # 吞吐倍数：并发 N 个，单次耗时只涨了 xx 倍，就说明并行度有多少
        print(f"{workers:>6}  {round_wall:>8.0f} ms  {per_call:>8.0f} ms  "
              f"{per_call / baseline:>9.2f}×  {workers / (per_call / baseline):.2f}× "
              f"(单核=1.00×)")
        del recognizers

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
