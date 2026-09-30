# vocotype-npu —— 让 VocoType 的离线识别跑在 Rockchip NPU 上

在 **RK3576**（Radxa ROCK 4D）上，把 [VocoType-linux](https://github.com/LeonardNJU/VocoType-linux)
的离线语音识别从 CPU 挪到 NPU。文字几乎完全一致，**CPU 时间降到约 1/140**。

> **先说清楚**：VocoType 是 [LeonardNJU](https://github.com/LeonardNJU/VocoType-linux) 的项目，
> **不是**本仓库作者的作品。本仓库只是一个**外挂的 worker 进程** —— 不改 VocoType
> 一行代码（可选补丁除外），装完不喜欢删掉配置就回到原样。
> VocoType-linux 本身是 [233stone](https://github.com/233stone/vocotype-cli) 的
> 早期版本发展来的。

---

## 效果

同一段音频，同样的 4 线程 + 标点，走 VocoType 的真实 IPC 测出来：

| 音频长度 | 原版 CPU worker | NPU 版 | |
|---|---|---|---|
| 5.6 s | 1953 ms | **353 ms** | 5.5× |
| 8.1 s（中英混）| 2831 ms | **370 ms** | 7.7× |
| 29 s | 11395 ms | **958 ms** | 11.9× |
| 47 s | — | **1282 ms** | |
| 123 s | — | **3483 ms** | |

### 更重要的数字：CPU 时间

29 秒音频，记录 worker 进程自己的 CPU 时间（`/proc/<pid>/stat`）：

| | 墙钟 | **CPU 时间** |
|---|---|---|
| 原版 CPU worker | 7250 ms | **28090 ms** |
| NPU 版 | 958 ms | **197 ms** |

墙钟只快 7.6×，但 **CPU 时间差两个数量级**。这才是真正的收益：识别的时候
机器是「闲」的，别的活照样干。对一台同时要跑桌面、浏览器、编辑器的开发板来说，
这比墙钟数字重要得多。

### 其它

| | 原版 CPU | NPU 版 |
|---|---|---|
| 冷启动 | ~15 s | **~3.5 s** |
| 长音频 RTF | — | **0.024** |
| 常驻内存 | 1853 MB | 1302 MB（保守配置）<br>2930 MB（默认，三模型并行） |

内存涨是因为默认同时驻留三个模型实例（见下面的「三处优化」），可以用
`npu.json` 关掉，退回 1.3 GB。

---

## 快速开始

```bash
git clone <这个仓库> && cd vocotype-npu

# 有 dist/ 里那个预编译轮子的话，加上这一行能省 25 分钟编译
export VOCOTYPE_NPU_WHEEL=dist/sherpa_onnx-1.13.8-1-cp311-cp311-manylinux_2_27_aarch64.whl

./install.sh
```

装完按 F9 说话就行。脚本会做这些事：

1. 体检（aarch64、NPU 在不在）
2. 在仓库里建 `.venv`，装 numpy / soundfile / sherpa-onnx
3. 下载三个模型到 `~/.local/share/vocotype-npu/models/`（约 1.0 GB）
4. 装一个启动器到 `~/.local/bin/vocotype-offline-worker-npu`
5. 生成 `~/.config/vocotype/npu.json`
6. 把 `asr.worker_path` 指到启动器（原配置备份成 `config.json.bak-npu`）
7. 重启 VocoType 服务，并跑一次真识别验证

其它用法：

```bash
./install.sh --check          # 只体检，什么都不改
./install.sh --no-config      # 只装环境，不动 VocoType 配置
./install.sh --revert         # 换回原来的 CPU worker
./install.sh --build-sherpa   # 顺带从源码编译 sherpa-onnx（约 25 分钟）
```

### 前置条件

- **aarch64 的 Rockchip 板子，带 NPU**（只在 RK3576 / ROCK 4D 上实测过）
- VocoType 已经装好并且能用（它自己那套 CPU 识别要先跑通）
- Radxa 镜像自带的 `rknpu2-rk3588` 包（提供 `/usr/include/rknn_api.h` 和
  `/usr/lib/librknnrt.so`，编译 sherpa-onnx 要用）

> NPU 不在 `/dev/rknpu` 上，而是 DRM 的 `/dev/dri/renderD*`。
> 确认：`grep -l DRIVER=RKNPU /sys/class/drm/renderD*/device/uevent`

---

## ⚠️ sherpa-onnx 不能用 PyPI 那个

**PyPI 上的 `sherpa-onnx` 不带 RKNN 后端，装了没用。** 这一点实测确认过：
PyPI 的 1.13.8 装完，扩展模块里**没有** `offline-paraformer-model-rknn`，
`provider="rknn"` 根本走不通。

### 办法一：用编好的轮子（推荐，省 25 分钟）

`dist/` 里带了一个已经编好、**验证过能跑**的（15 MB）：

```bash
VOCOTYPE_NPU_WHEEL=dist/sherpa_onnx-1.13.8-1-cp311-cp311-manylinux_2_27_aarch64.whl \
    ./install.sh
```

要求 **aarch64 + Python 3.11**。细节和来历见 `dist/README.md`。

### 办法二：自己编（约 25 分钟，本机验证过）

```bash
git clone --depth 1 -b v1.13.8 https://github.com/k2-fsa/sherpa-onnx
cd sherpa-onnx
SHERPA_ONNX_CMAKE_ARGS="-DSHERPA_ONNX_ENABLE_RKNN=ON" \
    /path/to/vocotype-npu/.venv/bin/python3 -m pip install .
```

或者让脚本代劳：`./install.sh --build-sherpa`。

编完的产物可以重新打包成轮子留给别人，别让大家各编一遍：

```bash
SP=.venv/lib/python3.11/site-packages
mkdir -p /tmp/pack && cd /tmp/pack
cp -a "$SP/sherpa_onnx" "$SP"/sherpa_onnx-*.dist-info .
python3 -m wheel pack --build-number 1 -d dist .
```

（`dist/` 里那个轮子就是这么来的 —— 不是重编，是把装好的目录重打包。）

---

## 原理：为什么不用改 VocoType

VocoType 的 worker 是**独立进程**，跟核心用 JSON-lines 在 stdin/stdout 上通信。
核心找 worker 的顺序是（`src/core/src/offline_asr.cpp`）：

1. 环境变量 `VOCOTYPE_OFFLINE_WORKER`
2. **配置项 `asr.worker_path`**　← 用的是这个
3. 核心可执行文件同目录下的 `vocotype-offline-worker`
4. `$PATH`、`/usr/libexec/...`

所以只要写一个**说同样协议、收同样参数**的新 worker，改一行配置就换掉了。
上游自己就编了个 `vocotype-fake-offline-worker` 测试替身
（`src/core/tests/fake_offline_worker.cpp`），说明「换掉 worker」本来就是设计内的用法
—— 只是这条路没写进文档。

```
VocoType 核心 ──JSON-lines──> vocotype-offline-worker-npu ──> sherpa-onnx ──> NPU
   (不变)                          (本仓库)                    (自编译)      RK3576
```

### 三处优化（都是实测选出来的）

1. **ASR 和标点并行加载**：串行 4.7 s → 并行 3.2 s。
2. **短音频走 5 秒档模型**：模型是**固定形状**的，你说 3 秒它也按 10 秒算。
   实测同一段 3.8 s 音频：5 秒档 168 ms，10 秒档 270 ms。
3. **长音频的块并行跑在两个 NPU 核上**：RK3576 是**双核** NPU（实测开 2 个
   RKNN 上下文，单次只慢 1.25×，吞吐到 1.61×；开 3 个只到 1.73×，第三个核没收益）。

两个可选模型在**后台线程**加载，所以 worker 3.5 秒就能用；加载完之前自动走单实例串行。

**试过但没收益的（别再试一遍）**：内存搬运/零拷贝（CPU/墙钟 = 0.23，CPU 77% 的时间
在等 NPU，数据早搬完了）、int8 量化（官方模型已经是 int8）、标点搬到 NPU（只占
总耗时 14%）、分段标点（更慢**且**更差，会丢跨块上下文把句子断错）。

---

## 可调参数

`~/.config/vocotype/npu.json`（不存在就用默认值，模板见 `worker/npu.json.example`）：

| 键 | 默认 | 说明 |
|---|---|---|
| `asr_model_dir` | 10 秒档模型 | 主识别模型 |
| `short_audio_model_dir` | 5 秒档模型 | 短句用的。设 `""` 关掉 |
| `short_audio_max_seconds` | 4.8 | 多短算「短句」 |
| `punc_model` | CT-Transformer | 标点模型路径 |
| `punctuation` | `true` | 总开关（VocoType 关标点时也会关） |
| `window_seconds` | 10.0 | 主模型的固定窗口 |
| `parallel_chunks` | 2 | 长音频并行块数。设 `1` 省 440 MB NPU 内存 |
| `punc_threads` | 4 | 标点用的 CPU 线程数 |

改完要 `systemctl --user restart vocotype-fcitx5-backend.service`。

**想省内存**（2.93 GB → 约 1.3 GB）：`"parallel_chunks": 1` +
`"short_audio_model_dir": ""`。这是初版的行为，长音频慢 1.6×、短句慢 1.7×。

### NPU 侧内存是**跨进程共享**的有限池

这不是普通内存。实测这个池子在 2.2～2.6 GB 之间封顶：

- 常驻 worker（三个模型实例，1.3 GB **NPU 内存**）活着时，**再起第二个 NPU worker
  会在加载第三个实例时失败**：`failed to malloc npu memory`。
  而此时普通内存还剩 6.4 GB，不缺。
- 更麻烦的是 sherpa 的 C++ 层会**直接 `exit(255)`**，Python 的 `try/except`
  接不住，所以 worker 里的兜底逻辑在这条路径上失效，整个进程会死。

**只有同时跑别的 NPU 任务时才会踩到**（另一个 VocoType 实例、别的 RKNN 程序）。
只跑一个 worker 不会。真需要让位就把 `parallel_chunks` 设成 1。

---

## 和原版 worker 的行为差别

| | 原版 CPU worker | NPU 版 |
|---|---|---|
| `--asr-model-dir` | 用它 | **忽略**（用 `npu.json` 里的 RKNN 模型） |
| `--punc-model-dir` | 用它 | **只当开关用**（路径从 `npu.json` 来） |
| `--threads` | 用它 | **忽略**（标点线程数看 `npu.json` 的 `punc_threads`） |
| `--vad-model-dir` | 用它 | **忽略**（改用切块，不用 VAD） |
| `--idle-timeout-ms` | 用它 | 用它 |
| **热词** | 支持 | **不支持**（sherpa-onnx 的离线 Paraformer 没有热词偏置） |
| `itn` | 支持 | **忽略**（sherpa-onnx 没这个开关） |

热词没了影响其实很小：核心自己的**别名替换（`rewrite_terms`）仍然生效**，
所以「沃口泰普 → VocoType」这类修正照旧。`itn` 也一样，核心自己会做数字归一化。

### 两个踩过的坑

1. **不要用 VAD 切长音频。** 一开始用 Silero VAD 切，结果「对我做了介绍啊」被切成
   三段，识别成「对我做了介绍什么？我想说的事呢？」—— 切在词中间会丢字，而且每段
   各付一次窗口钱，更慢。**直接在能量最低处切块反而又快又准。**
2. **别用 30 秒档整段跑长音频。** 29 秒音频用 30 秒档一次跑完，前 20 秒正确，后面
   变成「就就觉是在特别别就就自自自的的一思」。固定形状模型的尾巴会糊。

---

## 没有麦克风也能测

核心暴露了一个 Unix socket，有 `transcribe` 请求类型，直接发就能端到端验证：

```bash
.venv/bin/python3 tools/ask_core.py 音频.wav [更多.wav ...]
```

（`tools/` 下还有一批脚本：`test_worker.py` 直接驱动 worker 测耗时、
`bench_cpu_vs_npu.py` 对比 CPU 时间和墙钟、`test_parallel.py` 验证 NPU 双核、
`profile_window.py` 拆解单个窗口的耗时。跑法看各自文件头部的 docstring。）

---

## 出问题怎么办

**worker 起不来 / 一说话就报错**，看日志：

```bash
journalctl --user -u vocotype-fcitx5-backend.service -n 80
```

**第一次说完要等 3～4 秒**：模型在冷加载，正常。之后就一直快了。
如果**每次**都等，说明 worker 被空闲超时杀掉了 —— 检查 `asr.idle_timeout_s`
（默认只有 300 秒，`install.sh` 会改成 3600）。

**`failed to malloc npu memory` 然后 worker 直接死掉**：NPU 内存池被别的进程占了。
把 `npu.json` 的 `parallel_chunks` 设成 1，重启服务。

**识别出来是空的或者乱的**：先 `./install.sh --check` 看模型全不全。

**想彻底退回去**：`./install.sh --revert`。

---

## 文件说明

```
vocotype-npu/
├── install.sh                    一键安装 / 体检 / 回滚
├── README.md                     本文
├── worker/
│   ├── vocotype-offline-worker-npu   worker 本体（Python 脚本）
│   └── npu.json.example              参数模板
├── tools/
│   ├── _common.py                工具脚本共用的路径解析
│   ├── ask_core.py               走核心 IPC 做端到端测试（不需要麦克风）
│   ├── test_worker.py            直接驱动 worker，测各长度音频耗时
│   ├── bench_cpu_vs_npu.py       CPU vs NPU：墙钟 + CPU 时间
│   ├── bench_streaming.py        流式 worker 的实时余量
│   ├── bench_preview_e2e.py      走核心 IPC 模拟按 F9，量流式预览延迟
│   ├── profile_window.py         拆解单个窗口的耗时（识别 vs 标点）
│   └── test_parallel.py          验证 NPU 是不是双核
├── patches/
│   ├── 0001-fcitx5-toggle-hotkey.patch  可选：F9 改成按一下切换
│   └── README.md
└── docs/                         实测数据、踩坑记录、生态调研、视频素材
```

---

## 已知限制

- **只在 RK3576 / ROCK 4D 上验证过。** RK3588 理论上也能跑（把模型换成 rk3588 版），
  但没试过。
- **只接了离线识别。** 流式那条路还是 CPU 原版 —— 自转的 zipformer 收益只有 1.3×，
  不值得，没接。
- **热词不支持**（见上）。
- **NPU 内存是共享的**（见上）。
- `patches/` 里那个 F9 补丁改的是**上游源码**，`git pull` 或重新编译会被覆盖。

## 许可

GPL-3.0（见 `LICENSE`）。原因是 `patches/` 里的补丁是 VocoType 的衍生作品，
而 VocoType 本身是 GPL-3.0。worker 和工具脚本跟着一起用同一个许可，省得扯皮。
