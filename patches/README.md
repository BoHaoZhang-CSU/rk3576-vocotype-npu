# 补丁

## 0001-fcitx5-toggle-hotkey.patch

**把 VocoType 的语音热键从「按住说话」改成「按一下开始、再按一下结束」。**

这个是**可选**的。不打它，NPU 加速照样能用，只是热键仍要按住。

### 打之前

- 补丁是相对 VocoType-linux 的 `f56eb9e9a0ffef993a10f348cad16e71daea60c8` 做的。
  上游改了这段代码就可能打不上（`git apply` 会告诉你）。
- 会改三个热键的行为：F9（主键）、Shift+F9（AI 润色）、Ctrl+F9（编辑）。
- 打了以后 `armPendingPttRelease` / `replayShortTapAsRegularKey` / `ptt_suppressed_`
  这条「按住」路径就没人调了，变成死代码。**故意留着**的，方便 revert。

### 怎么打

```bash
cd /path/to/VocoType-linux
git apply /path/to/vocotype-npu/patches/0001-fcitx5-toggle-hotkey.patch

cmake --build build/fcitx-native-user -j4
cp build/fcitx-native-user/vocotype.so ~/.local/lib/fcitx5/vocotype.so

# 重载 fcitx5。注意必须重定向输出，否则守护进程继承 stdout，命令永不返回。
setsid fcitx5 -r -d </dev/null >>/tmp/fcitx5.log 2>&1
```

### 为什么要有那个 400ms 防抖

改完之后，「正在录音」状态下收到的**每一次按下**都等于「停止」。而有些前端会把
「按住不放」报成一串 press/release 事件 —— 那样按久一点就会立刻停录。

所以加了个 `TOGGLE_STOP_DEBOUNCE_US = 400000`：开始录音后 400ms 内收到的按下
一律当自动重复丢掉。400ms 比最短录音时长（1000ms）小，所以正常的「快速按两下」
不受影响。

### 打完怎么回退

```bash
cd /path/to/VocoType-linux
git apply -R /path/to/vocotype-npu/patches/0001-fcitx5-toggle-hotkey.patch
# 然后照上面重新编译 + 重载
```

### 注意

这是**改上游源码**，不是配置。以后 `git pull` 或者重新编译 VocoType 都会把它
覆盖掉，得重打一遍。
