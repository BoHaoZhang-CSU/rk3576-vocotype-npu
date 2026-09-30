#!/usr/bin/env python3
"""走 vocotype-core 的真实 IPC 模拟一次「按住 F9 说话」，量流式预览的实时性。

用的就是桌面端那三个请求：asr_preview_start / feed / close。
关键指标是「累计欠账」：送进去的音频时长 - 核心花掉的墙钟时间。
欠账持续增长 = 文字越拖越远 = 卡。

用法: bench_preview_e2e.py 音频.wav [--seconds 30]
"""

import argparse
import base64
import json
import os
import socket
import sys
import time

import numpy as np
import soundfile as sf

SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
                    "vocotype-fcitx5.sock")
CHUNK_SAMPLES = 9600  # 600ms


class Client:
    """vocotype-core 每个连接只服务一个请求就关闭，而且响应不带换行符，
    所以每次都开新连接、读到 EOF 为止。"""

    def request(self, payload):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(120)
        sock.connect(SOCK)
        sock.sendall((json.dumps(payload) + "\n").encode())
        buffer = b""
        while True:
            piece = sock.recv(1 << 20)
            if not piece:
                break
            buffer += piece
            try:
                json.loads(buffer.decode())
                break
            except json.JSONDecodeError:
                continue
        sock.close()
        if not buffer:
            raise RuntimeError("核心没回任何东西")
        return json.loads(buffer.decode())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wav")
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args()

    samples, rate = sf.read(args.wav, dtype="float32")
    if samples.ndim > 1:
        samples = samples[:, 0]
    assert rate == 16000, rate
    samples = samples[: int(args.seconds * rate)]
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()

    client = Client()
    started = client.request({"type": "asr_preview_start"})
    if not started.get("success"):
        print("启动流式会话失败:", started)
        return 1
    print(f"会话已建立（核心报的 chunk_samples={started.get('chunk_samples')}）\n")
    session = started["session_id"]

    # 第一次 feed 含模型热身，单独算
    print(f"{'块':>3}  {'本块耗时':>9}  {'累计音频':>9}  {'累计耗时':>9}  {'欠账':>7}  文字")
    audio_ms = 0.0
    spent_ms = 0.0
    worst = 0.0
    text = ""
    warmup = None
    for index, offset in enumerate(range(0, len(pcm), CHUNK_SAMPLES * 2), 1):
        block = pcm[offset:offset + CHUNK_SAMPLES * 2]
        is_last = offset + CHUNK_SAMPLES * 2 >= len(pcm)
        t0 = time.monotonic()
        reply = client.request({"type": "asr_preview_feed", "session_id": session,
                                "pcm16": base64.b64encode(block).decode(),
                                "is_final": is_last})
        elapsed = (time.monotonic() - t0) * 1000
        if not reply.get("success"):
            print("  失败:", reply)
            return 1
        chunk_audio = len(block) / 2 / 16000 * 1000
        audio_ms += chunk_audio
        spent_ms += elapsed
        text = reply.get("text", "")
        if index == 1:
            warmup = elapsed
            note = "（含热身）"
        else:
            worst = max(worst, elapsed)
            note = "🚨 欠账" if audio_ms - spent_ms < -50 else ""
        if index == 1 or index % 5 == 0 or is_last or note:
            print(f"{index:>3}  {elapsed:>7.0f}ms  {audio_ms:>7.0f}ms  "
                  f"{spent_ms:>7.0f}ms  {audio_ms - spent_ms:>+6.0f}ms  "
                  f"{text[-24:]} {note}")
        if is_last:
            break

    t0 = time.monotonic()
    closed = client.request({"type": "asr_preview_close", "session_id": session})
    close_ms = (time.monotonic() - t0) * 1000

    print(f"\n第一块（热身）: {warmup:.0f} ms")
    print(f"稳态最慢一块  : {worst:.0f} ms  （预算 600ms）")
    print(f"最终收尾      : {close_ms:.0f} ms")
    print(f"总计欠账      : {audio_ms - spent_ms:+.0f} ms "
          f"（负=跟不上，文字落后这么多）")
    print(f"最终文字      : {closed.get('text', text)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
