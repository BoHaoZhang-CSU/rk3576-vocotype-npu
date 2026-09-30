# 预编译的 sherpa-onnx（带 RKNN 后端）

```
sherpa_onnx-1.13.8-1-cp311-cp311-manylinux_2_27_aarch64.whl   15 MB
```

**这个是干什么的**：省掉你自己编译 sherpa-onnx 的那 25 分钟。
PyPI 上的 `sherpa-onnx` **不带** RKNN 后端（见根目录 README），必须自己编一个，
而编一次的产物所有同架构的板子都能用 —— 就是它。

## 怎么用

```bash
VOCOTYPE_NPU_WHEEL=/path/to/sherpa_onnx-1.13.8-1-cp311-cp311-manylinux_2_27_aarch64.whl \
    ./install.sh
```

或者手动：

```bash
.venv/bin/python3 -m pip install sherpa_onnx-1.13.8-1-cp311-cp311-manylinux_2_27_aarch64.whl
```

## 适用条件（不满足就别用，自己编）

| | 要求 |
|---|---|
| 架构 | **aarch64**（文件名里的 `aarch64`） |
| Python | **3.11**（文件名里的 `cp311`） |
| 系统 | 要有 `librknnrt.so`，Radxa 镜像自带的 `rknpu2-rk3588` 包提供 |

轮子里**不含** `librknnrt.so`（故意的）—— 它是运行时跟 NPU 内核驱动配套的，
必须用系统那份。只依赖它一个外部库，其余（onnxruntime 等）都打包在里面了。

## 装完怎么确认能用

```bash
f=$(.venv/bin/python3 -c "import sherpa_onnx,os;print(os.path.dirname(sherpa_onnx.__file__))")/lib/_sherpa_onnx*.so
grep -a "offline-paraformer-model-rknn" $f && echo "带 RKNN ✅" || echo "不带 ❌"
```

或者直接 `./install.sh --check`。

## 这东西是怎么来的（可复核）

**不是**从源码重新编译的，是**把一个已经编译好的安装目录重新打包**：

1. 在一台 RK3576 板子上按根目录 README 的配方从源码编译：
   `SHERPA_ONNX_CMAKE_ARGS="-DSHERPA_ONNX_ENABLE_RKNN=ON" pip install .`
   （源码树是 k2-fsa/sherpa-onnx 的 `v1.13.8` tag）
2. 把 `site-packages/sherpa_onnx/` 和 `site-packages/sherpa_onnx-1.13.8.dist-info/`
   原样拷到一个空目录
3. `python3 -m wheel pack --build-number 1 -d dist .`

所以它里面的二进制和「你自己在同样的板子上编一遍」得到的**是同一份东西**，
只是省了编译时间。`1` 这个 build number 就是用来跟 PyPI 的同版本号区分的
（PyPI 那份不带 RKNN）。

想完全自己编、不用别人的二进制，就照 README 的源码编译步骤走，或者
`./install.sh --build-sherpa`。

## 验证记录

这个轮子在 2026-09-30 做过完整的端到端验证：干净 venv → 装轮子 →
启动 worker → 识别一段 5.61 秒中文音频 → 输出带标点的正确文字（394 ms）。

## 许可

sherpa-onnx 是 **Apache-2.0**，可以自由分发。分发时保留本说明即可。
