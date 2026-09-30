#!/usr/bin/env python3
"""往正在运行的 vocotype-core 的 Unix socket 发请求，用来在没有麦克风的情况下做端到端测试。

用法: ask_core.py <wav 文件> [更多 wav ...]
"""

import json
import os
import socket
import sys
import time

SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
                    "vocotype-fcitx5.sock")


def request(payload):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(180)
    client.connect(SOCK)
    client.sendall((json.dumps(payload) + "\n").encode())
    buffer = b""
    while not buffer.endswith(b"\n"):
        chunk = client.recv(65536)
        if not chunk:
            break
        buffer += chunk
    client.close()
    return json.loads(buffer.decode())


def main():
    for wav in sys.argv[1:]:
        started = time.time()
        try:
            result = request({"type": "transcribe", "audio_path": os.path.abspath(wav),
                              "itn": True})
        except Exception as error:
            print(f"{wav}: 请求失败 {error}")
            continue
        wall_ms = (time.time() - started) * 1000
        print(f"--- {os.path.basename(wav)}  (往返 {wall_ms:.0f} ms)")
        if not result.get("success"):
            print(f"    失败: {result.get('error')}")
            continue
        print(f"    worker 耗时 {result.get('latency_ms', 0):.0f} ms | "
              f"backend={result.get('backend')}")
        print(f"    {result.get('text', '')}")


if __name__ == "__main__":
    main()
