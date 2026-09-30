#!/usr/bin/env bash
# 把 VocoType 的离线识别换成跑在 NPU 上的版本（Rockchip RK3576，在 ROCK 4D 上验证）。
#
#   ./install.sh                 装到当前用户（全部在 $HOME 下，不需要 root）
#   ./install.sh --check         只体检，什么都不改
#   ./install.sh --build-sherpa  顺带从源码编译带 RKNN 的 sherpa-onnx（约 25 分钟）
#   ./install.sh --no-config     装好环境，但不动 VocoType 的 config.json
#   ./install.sh --revert        把 VocoType 换回原来的 CPU worker
#
# 装完以后这些东西会出现（或更新）：
#   ./.venv                                    本仓库自带的 python 环境
#   ~/.local/share/vocotype-npu/models/         三个模型，约 1.0 GB
#   ~/.local/bin/vocotype-offline-worker-npu    启动器，VocoType 调的就是它
#   ~/.config/vocotype/npu.json                 可调参数（已存在就不覆盖）

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO/.venv"
MODELS="${VOCOTYPE_NPU_MODELS_DIR:-$HOME/.local/share/vocotype-npu/models}"
LAUNCHER="$HOME/.local/bin/vocotype-offline-worker-npu"
NPU_CONFIG="$HOME/.config/vocotype/npu.json"
VOCO_CONFIG="$HOME/.config/vocotype/config.json"
UNIT="vocotype-fcitx5-backend.service"
SHERPA_TAG="v1.13.8"   # 本机验证过的版本，别随手换

GH="https://github.com/k2-fsa/sherpa-onnx/releases/download"
ASR_RELEASE="$GH/asr-models"
PUNC_RELEASE="$GH/punctuation-models"
MODEL_10S="sherpa-onnx-rk3576-10-seconds-paraformer-zh-2023-03-28"
MODEL_5S="sherpa-onnx-rk3576-5-seconds-paraformer-zh-2023-03-28"
MODEL_PUNC="sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12"

DO_BUILD_SHERPA=0
DO_CONFIG=1
MODE=install

say()  { printf '%s\n' "$*"; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '  ! %s\n' "$*" >&2; }
die()  { printf '\n\033[1m出错：%s\033[0m\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --check)        MODE=check ;;
        --revert)       MODE=revert ;;
        --no-config)    DO_CONFIG=0 ;;
        --build-sherpa) DO_BUILD_SHERPA=1 ;;
        -h|--help)      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)              die "不认识的参数：$1" ;;
    esac
    shift
done

# ---------------------------------------------------------------- 体检

step "体检"

[ "$(uname -m)" = "aarch64" ] || die "本方案是 aarch64 的。当前是 $(uname -m)。"

if grep -lq "DRIVER=RKNPU" /sys/class/drm/renderD*/device/uevent 2>/dev/null; then
    say "  NPU 就绪（$(grep -l "DRIVER=RKNPU" /sys/class/drm/renderD*/device/uevent | head -1)）"
else
    die "找不到 Rockchip NPU。注意 NPU 不在 /dev/rknpu 上，而是 DRM 的 /dev/dri/renderD*，
     可以用这条确认：grep -l DRIVER=RKNPU /sys/class/drm/renderD*/device/uevent"
fi

command -v python3 >/dev/null || die "没有 python3"
python3 -c 'import venv' 2>/dev/null || die "python3 缺 venv 模块，先装：sudo apt install python3-venv"
say "  python3 $(python3 -V 2>&1 | awk '{print $2}')（$(command -v python3)）"

if [ ! -r /usr/include/rknn_api.h ]; then
    warn "没找到 /usr/include/rknn_api.h —— 如果 sherpa-onnx 已经装好了就无所谓；
     要自己编译 sherpa-onnx 的话，需要它和 librknnrt.so（Radxa 镜像里的 rknpu2-rk3588 包提供）"
fi

# 装了带 RKNN 的 sherpa-onnx 吗？直接查 .so 里有没有那个文件名字符串——
# 只有开了 SHERPA_ONNX_ENABLE_RKNN 才会把这个 .cc 编进去。
sherpa_so() { find "$VENV/lib" -name "_sherpa_onnx*.so" 2>/dev/null | head -1; }
has_rknn_sherpa() {
    local so; so="$(sherpa_so)"
    [ -n "$so" ] && grep -aq "offline-paraformer-model-rknn" "$so"
}

if [ -f "$LAUNCHER" ]; then say "  已装过启动器：$LAUNCHER"; fi
[ -f "$VOCO_CONFIG" ] || warn "没找到 $VOCO_CONFIG —— VocoType 装了吗？"
command -v systemctl >/dev/null && say "  服务名：$UNIT"

if [ "$MODE" = check ]; then
    step "已装情况"
    [ -x "$VENV/bin/python3" ] && say "  venv：有" || say "  venv：没有"
    if has_rknn_sherpa; then say "  sherpa-onnx：有，且带 RKNN 离线 Paraformer"; else say "  sherpa-onnx：没有 / 不带 RKNN"; fi
    # worker 找不到 $MODELS 时会退回几个老路径（见 worker 里的 _model_roots），
    # 所以这里也说一声，免得明明能用却看着像没装。
    legacy="$HOME/rknn-lab/paraformer/prebuilt"
    for m in "$MODEL_10S" "$MODEL_5S" "$MODEL_PUNC"; do
        if [ -d "$MODELS/$m" ]; then
            say "  模型 $m：有"
        elif [ -d "$legacy/$m" ]; then
            say "  模型 $m：$MODELS 里没有，但备用路径 $legacy 有（能用）"
        else
            say "  模型 $m：缺"
        fi
    done
    [ -f "$NPU_CONFIG" ] && say "  npu.json：有" || say "  npu.json：没有（用默认值）"
    if [ -f "$VOCO_CONFIG" ] && grep -q '"worker_path"' "$VOCO_CONFIG"; then
        say "  VocoType worker_path：$(python3 -c "import json;print(json.load(open('$VOCO_CONFIG'))['asr']['worker_path'])")"
    else
        say "  VocoType worker_path：没设（用原版 CPU worker）"
    fi
    exit 0
fi

# ---------------------------------------------------------------- 回滚

if [ "$MODE" = revert ]; then
    step "把 VocoType 换回原来的 CPU worker"
    [ -f "$VOCO_CONFIG" ] || die "没有 $VOCO_CONFIG"
    cp -a "$VOCO_CONFIG" "$VOCO_CONFIG.bak-npu"
    python3 - "$VOCO_CONFIG" <<'PY'
import json, sys
path = sys.argv[1]
cfg = json.load(open(path))
cfg.get("asr", {}).pop("worker_path", None)
json.dump(cfg, open(path, "w"), ensure_ascii=False, indent=2)
PY
    say "  已删掉 asr.worker_path（原文件备份成 config.json.bak-npu）"
    if systemctl --user restart "$UNIT" 2>/dev/null; then
        say "  服务已重启"
    else
        warn "重启 $UNIT 失败，手动跑：systemctl --user restart $UNIT"
    fi
    say "\n完成。现在用的是 VocoType 自带的 CPU worker，速度和以前一样。"
    say "NPU 那套东西还在，想换回来再跑一次 ./install.sh 就行。"
    exit 0
fi

# ---------------------------------------------------------------- venv

step "Python 环境"
if [ -x "$VENV/bin/python3" ]; then
    say "  $VENV 已存在，跳过"
else
    say "  建 $VENV"
    python3 -m venv "$VENV"
fi
"$VENV/bin/python3" -m pip install --quiet --upgrade pip
say "  装 numpy / soundfile"
"$VENV/bin/python3" -m pip install --quiet "numpy<2" soundfile

# ---------------------------------------------------------------- sherpa-onnx

step "sherpa-onnx（要带 RKNN 才行）"
WHEEL="${VOCOTYPE_NPU_WHEEL:-}"
if [ -z "$WHEEL" ]; then
    # 仓库里带了预编译轮子就直接用 —— 别让每个人都花 25 分钟编一遍同样的东西。
    for candidate in "$REPO"/dist/*.whl; do
        [ -f "$candidate" ] && WHEEL="$candidate" && break
    done
fi

if has_rknn_sherpa; then
    say "  已装好，跳过"
elif [ "$DO_BUILD_SHERPA" = 0 ] && [ -n "$WHEEL" ]; then
    say "  用预编译轮子：$(basename "$WHEEL")"
    "$VENV/bin/python3" -m pip install --quiet "$WHEEL" \
        || die "装 $WHEEL 失败（架构/Python 版本对得上吗？看 dist/README.md）"
    has_rknn_sherpa || die "$(basename "$WHEEL") 装上了但不带 RKNN 离线 Paraformer。
     要么它跟本机的 Python/架构不匹配，要么拿到的是 PyPI 那个版本。
     换成自己编：./install.sh --build-sherpa"
elif [ "$DO_BUILD_SHERPA" = 1 ]; then
    for tool in git cmake gcc; do command -v "$tool" >/dev/null || die "缺 $tool"; done
    say "  从源码编译 $SHERPA_TAG（约 25 分钟，去泡杯茶）"
    say "  用 make 而不是 ninja —— 有些镜像没装 ninja，cmake 自己会挑。"
    BUILD_DIR="${VOCOTYPE_NPU_BUILD_DIR:-$HOME/sherpa-onnx-rknn-build}"
    if [ ! -d "$BUILD_DIR" ]; then
        git clone --depth 1 --branch "$SHERPA_TAG" https://github.com/k2-fsa/sherpa-onnx "$BUILD_DIR"
    fi
    say "  编译中，日志在 /tmp/sherpa-rknn-build.log"
    ( cd "$BUILD_DIR" && \
      SHERPA_ONNX_CMAKE_ARGS="-DSHERPA_ONNX_ENABLE_RKNN=ON" \
      "$VENV/bin/python3" -m pip install . ) >/tmp/sherpa-rknn-build.log 2>&1 \
        || die "编译失败，看 /tmp/sherpa-rknn-build.log 的最后几十行"
    has_rknn_sherpa || die "编译完了但还是没有 RKNN 支持，看 /tmp/sherpa-rknn-build.log"
    say "  编译完成"
else
    cat <<EOF

  PyPI 上的 sherpa-onnx **不带** RKNN 后端，得用带 RKNN 的版本。三种办法：

  1) 用编好的轮子（最省事，省 25 分钟）：
         把 wheel 放到 dist/ 目录下，再跑一次 ./install.sh
     或者   VOCOTYPE_NPU_WHEEL=/path/to/sherpa_onnx-*.whl ./install.sh
     dist/README.md 里有适用条件和来历。

  2) 让本脚本从源码编（约 25 分钟，本机验证过）：
         ./install.sh --build-sherpa
     需要 git / cmake / gcc，以及 Radxa 镜像自带的 rknpu2-rk3588 包
     （提供 /usr/include/rknn_api.h 和 /usr/lib/librknnrt.so）。

  3) 自己手编（等价于方案 2）：
         git clone --depth 1 -b $SHERPA_TAG https://github.com/k2-fsa/sherpa-onnx
         cd sherpa-onnx
         SHERPA_ONNX_CMAKE_ARGS="-DSHERPA_ONNX_ENABLE_RKNN=ON" \\
             $VENV/bin/python3 -m pip install .

  编好以后把装出来的目录重打包成轮子，别人就不用再编一遍了：
         SP=$VENV/lib/python3.11/site-packages
         mkdir -p /tmp/pack && cd /tmp/pack
         cp -a "\$SP/sherpa_onnx" "\$SP"/sherpa_onnx-*.dist-info .
         python3 -m wheel pack --build-number 1 -d dist .
EOF
    exit 1
fi

# ---------------------------------------------------------------- 模型

step "下载模型（约 1.0 GB，只下一次）"
mkdir -p "$MODELS"

fetch_model() {
    local name="$1" base="$2"
    if [ -d "$MODELS/$name" ]; then
        say "  $name 已有，跳过"
        return
    fi
    say "  下 $name.tar.bz2"
    curl -fL --progress-bar "$base/$name.tar.bz2" -o "$MODELS/.$name.tar.bz2.part" \
        || die "下载失败：$base/$name.tar.bz2"
    tar -xjf "$MODELS/.$name.tar.bz2.part" -C "$MODELS"
    rm -f "$MODELS/.$name.tar.bz2.part"
    [ -d "$MODELS/$name" ] || die "$name 解压后没看到目录，压缩包结构变了？"
}

fetch_model "$MODEL_10S"  "$ASR_RELEASE"
fetch_model "$MODEL_5S"   "$ASR_RELEASE"
fetch_model "$MODEL_PUNC" "$PUNC_RELEASE"

# ---------------------------------------------------------------- 启动器

step "装启动器"
mkdir -p "$(dirname "$LAUNCHER")"
# VocoType 是直接 exec worker_path 的，所以这里得是个能自己找到 venv 的壳。
# 写绝对路径而不是改 worker 的 shebang，是为了让仓库里的 worker 保持可移植。
cat >"$LAUNCHER" <<EOF
#!/bin/sh
# 由 vocotype-npu 的 install.sh 生成，别手改（重跑 install.sh 会覆盖）。
exec "$VENV/bin/python3" "$REPO/worker/vocotype-offline-worker-npu" "\$@"
EOF
chmod +x "$LAUNCHER"
say "  $LAUNCHER"

step "配置文件 $NPU_CONFIG"
mkdir -p "$(dirname "$NPU_CONFIG")"
if [ -f "$NPU_CONFIG" ]; then
    say "  已存在，保留你自己的（模板看 worker/npu.json.example）"
else
    sed "s#~/.local/share/vocotype-npu/models#$MODELS#g" \
        "$REPO/worker/npu.json.example" >"$NPU_CONFIG"
    say "  已生成（改它不用重跑 install.sh，重启服务即可）"
fi

# ---------------------------------------------------------------- 接进 VocoType

if [ "$DO_CONFIG" = 0 ]; then
    step "跳过改 VocoType 配置（--no-config）"
    say "  手动改：在 $VOCO_CONFIG 的 asr 段里加"
    say "      \"worker_path\": \"$LAUNCHER\""
else
    step "接进 VocoType"
    [ -f "$VOCO_CONFIG" ] || die "没有 $VOCO_CONFIG，先用 --no-config 装环境，或确认 VocoType 装好了"
    cp -a "$VOCO_CONFIG" "$VOCO_CONFIG.bak-npu"
    python3 - "$VOCO_CONFIG" "$LAUNCHER" <<'PY'
import json, sys
path, worker = sys.argv[1], sys.argv[2]
cfg = json.load(open(path))
asr = cfg.setdefault("asr", {})
asr["worker_path"] = worker
# 默认 300 秒太短：停顿一下 worker 就退出，下次说话白等 5 秒重新加载模型。
# 只在用户没设过、或还是默认值时改，不覆盖用户自己调过的值。
if asr.get("idle_timeout_s", 300) <= 300:
    asr["idle_timeout_s"] = 3600
json.dump(cfg, open(path, "w"), ensure_ascii=False, indent=2)
PY
    say "  asr.worker_path = $LAUNCHER"
    say "  asr.idle_timeout_s = 3600（原默认 300 秒太短，会反复重新加载模型）"
    say "  原配置备份：$VOCO_CONFIG.bak-npu"
fi

# ---------------------------------------------------------------- 冒烟测试

step "跑一次真识别（不经过 VocoType，直接驱动 worker）"
SMOKE_WAV="$MODELS/$MODEL_10S/test_wavs/0.wav"
if [ -f "$SMOKE_WAV" ]; then
    if "$VENV/bin/python3" "$REPO/tools/test_worker.py" "$SMOKE_WAV" 2>&1 | tail -6; then
        say "  识别通了"
    else
        warn "冒烟测试没通过，但环境已经装好了。排查见 README 的「出问题怎么办」"
    fi
else
    warn "没有测试音频，跳过"
fi

# ---------------------------------------------------------------- 重启服务

step "重启 VocoType"
if systemctl --user restart "$UNIT" 2>/dev/null; then
    sleep 2
    systemctl --user is-active --quiet "$UNIT" \
        && say "  $UNIT 起来了" \
        || warn "$UNIT 没起来，看 journalctl --user -u $UNIT -n 50"
else
    warn "重启 $UNIT 失败。手动跑：systemctl --user restart $UNIT"
fi

cat <<EOF

完成。

  现在按一下 F9 就能说话（再按一下结束）。第一次说完会等 3～4 秒加载模型，
  之后就快了。

  改参数        $NPU_CONFIG（改完 systemctl --user restart $UNIT）
  换回 CPU      ./install.sh --revert
  体检          ./install.sh --check

EOF
