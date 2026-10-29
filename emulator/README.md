# emulator/ — 用 QEMU 模拟器调试表盘（不用手机、不用射频）

这一层把[米坛 29834 帖](https://www.bandbbs.cn/threads/29834/)的
[DDlhy/Miband-qemu-simulator](https://github.com/DDlhy/Miband-qemu-simulator)
移植到 Apple Silicon，让它成为表盘的**渲染后端**：改一次 `.face`，
一条命令就能看到真固件（3.101.043）画在 336 × 480 面板上的样子。

完整过程、失败路径和逐条证据在仓库根的
[`../macsim-BUILD-macOS.md`](../macsim-BUILD-macOS.md)；本目录只放可复用的东西。

## 目录

```
tools/       我们写的脚本，需要放进模拟器克隆的 tools/ 下使用
patches/     对上游 overlay C 代码的两处修复（真 bug），用 git apply 打
screenshots/ 模拟器产出的实拍帧
```

## 1. 克隆并打补丁

```bash
git clone https://github.com/DDlhy/Miband-qemu-simulator.git vendor/miband-qemu
cd vendor/miband-qemu
cp <this-repo>/emulator/tools/*.py tools/          # 以及 build-qemu-macos.sh
git apply <this-repo>/emulator/patches/0001-gatt-answer-handle-value-indication.patch
git apply <this-repo>/emulator/patches/0002-watchface-id-at-0x28.patch
```

两个补丁各自解决一个会让结论失真的真 bug：

- **`0001`**：`miband_hci_gatt_acl()` 没有 `case 0x1d`。手环是 ATT server，
  连上后会在自己的特征上发 Handle Value Indication；服务端拿不到
  Handle Value Confirmation 就会**永久卡住整条 indication 管道**，而模拟器
  回的是 `ATT Error 0x06 Request Not Supported`。补上确认（并把值继续交给
  同一个 write callback）后链路才是活的。
- **`0002`**：`miband_nand_set_mass_files()` 从 `+0x22` 读 24 字节表盘 id，
  实际在 **`+0x28`**（原厂 `/resource` 里 `watchface/aod/362150007/resource.bin`
  的 id 就在 offset 40）。读错位置导致 `market/<id>/` 从来没被建出来，
  固件的表盘管理器自然看不到已安装的表盘。

## 2. 构建（Apple Silicon）

```bash
brew install meson ninja pkgconf sdl2 dtc libffi
python3 tools/prepare_source.py --model band10pro --output <dir>/qemu   # 11,648 文件，SHA256 校验
tools/build-qemu-macos.sh                                               # 关键：--enable-tcg-interpreter
```

**必须用 TCI。** 普通 TCG JIT 在 Apple Silicon 上会在 10–25 s 内
`EXC_BAD_INSTRUCTION` / exit 132（已排除 MTTCG 竞态、TB 切分、entitlements、
整树 -O1）。TCI 需要 libffi，且 `pkg-config --cflags libffi` 会解析到 macOS SDK，
`--cflags` 和 `--libs` 都要塞进 `--extra-cflags` / `--extra-ldflags`。
代价是启动慢：约 60–150 s 才到界面。

镜像：`littlefs-python==0.19.0`（建议 venv，Homebrew Python 受 PEP 668 限制），
然后 `python3 tools/create_images.py --model band10pro` → `nv.img` + `nor-seed.bin`。

## 3. 渲染回路

```bash
.venv/bin/python tools/face_render.py ../../miband10pro-face/native/design.face
```

`face_render.py` 把三步串成一条命令，且**从不改动原始固件**：

1. `romfs_face_swap.py setid` —— 把容器的 face id 改成目标 romfs 目录名
   （固件会交叉校验，不一致就静默回落到别的表盘）；
2. `romfs_face_swap.py swap-keep` —— 原地覆盖该槽位的栅格，**不动任何记录字段**
   （`/resource` 每条记录的 `+0` 是下一条记录的绝对偏移，改长度就废）；
3. 启动模拟器 → 过掉首启向导 → 用**充电器插入边沿**点亮面板 → QMP `screendump`。

默认顶掉的槽位是 `120917390547`（幻境流彩）——**全新 `/data` 上主屏画的就是它**。
第一版自动挑最大槽位，结果白跑一次启动：主屏仍显示默认表盘。`--slot` 可覆盖，
`--keep-image` 保留打过补丁的 romfs 副本。

配套：`tools/probe_diff.py` 把 `native/probe.face` 的 4×9 网格截图变成
「哪些数据源在变」的表（用**变化像素计数**，不要用平均灰度差——数字是暗灰底
深字，真变化的均值反而小于 1）。

## 4. 虚拟手机链路

`tools/face_debug_p67.py` 有两个开关：

- `--phone-link`：注入 LE Connection Complete（`Role=0x01` peripheral，
  且必须 `--fresh-journal`；`Role=0x00` 完全无应答）。
- `--phone-session <face>`：**在宿主侧完整重放 Mi Fitness 装表盘的帧序**——
  ATT Write Request 打到特征句柄 6，承载 `L1(a5a5/type3/crc16-ARC)` →
  `L2(1,1)` + protobuf `Request{1:4, 2:4, 6:WatchFaceInfo{id,size}}`，
  再把整个 `.face` 封成 MASS 包（`00 10 | md5 | u32 len | payload | crc32`）
  按 1024 B 切片，每片 `L2(2,1) + u16 total + u16 index`。
  实测 `design.face` 329,441 B → **322 片全部接受、零失败**。

这一轮得到的关键结论（有日志与回显字节为证）：注入的手机帧走的是
**「控制器 → AP」**方向，交给 guest 固件消费；宿主侧那套
`miband_bt_gatt_write()` / `install_catalog_commit` 完全没被触发
（`-d unimp` 跑出 290,789 行日志，`miband BT Xiaomi` 一条都没有）。
所以 `install-catalog` 读回空串不是 bug——宿主 catalog + NAND overlay 与手机
会话是两套账。剩下的未知只在 guest 侧：固件 app 层收到这条写，是没鉴权丢弃，
还是没接进表盘数据源。

## 已排除，不必重试

1. `Role=central` 注入 —— 无应答。
2. 注入 LE Advertising Report 想触发主动回连 —— 没有 `LE_Create_Connection`。
3. 宿主灌 `sensor-accel-*` / `sensor-pulse-bpm` 推步数心率 —— 一动不动。
4. `diag-skip-binding` / `diag-bypass-binding-auth` —— profile 是空占位，`exit(1)`。
5. 改 `persist.back_dial_id` 的 12 个引用点 —— 面太大。
6. macOS 用真射频当手机 —— `raw_hci_bridge.py` 只有 `LinuxHci` / `WindowsUsb`。
7. `system_reset` 后继续抓屏 —— 复位后显示桥不再出图。

## 操作坑

- **换基线镜像必须换 journal**：改 AP 或 `/resource` 后复用旧
  `userdata/flash.journal` 会得到
  `miband flash store: wrong base image or state header (errno=22)` 并 `exit(1)`。
  `face_debug_p67.py` 会同时哈希两者并自动丢弃。
- **面板只认充电器的「边沿」**：反复写 `sensor-charging=true` 无效，
  必须先 `false` 再 `true`；触摸**不**重置息屏计时（约 10–12 s 息屏）。
  抓屏前一刻才制造边沿，否则整轮全黑会被误判成系统挂掉。
- 充电页会吞掉所有手势：点亮之后要先拔掉充电器再操作。
- QMP `pmemsave` 的地址成员是 **`val`**，不是 `start`；chardev 后端名是
  **`socket`**，不是 `tcp`。
- `qom-get bt-synthetic-pop-ap-packet` 空时会一直返回 `"0:"`，drain 循环要按它判停。
