# 在 Apple Silicon 上跑 10 Pro 固件模拟器（移植笔记）

上游：`DDlhy/Miband-qemu-simulator`（[米坛 29834](https://www.bandbbs.cn/threads/29834/)），
本地克隆在 `<工作区>/vendor/miband-qemu`。上游只交付 Windows 构建，
本笔记记录 macOS/arm64 的实际做法与当前阻塞点。

## 已经跑通的部分

上游的 Windows 耦合只在**打包层**（`build.cmd` + MSYS2 + `Launch.cmd` +
embeddable CPython），C 源码本身是跨平台的：`source/overlays/band10pro` 里
只有 27 处 `_WIN32`，且全部落在蓝牙 HCI 桥 / 宿主机文件通道上，
`miband_hci_bridge.c` 甚至自带 `#ifdef __APPLE__` 引 `IOKit/serial/ioss.h`。
`shared/` 与 `models/` 的 Python 零 Win32 调用。

1. 依赖：`brew install meson ninja pkgconf sdl2 dtc`（`libfdt` 由 `dtc` 提供，
   Homebrew 没有叫 `libfdt` 的 formula；`glib` / `pixman` / `zstd` 本机已有）。
2. 展开源码：`python3 tools/prepare_source.py --model band10pro --output <dir>/qemu`
   → 11,648 文件 SHA256 全通过。
3. 构建：`bash tools/build-qemu-macos.sh band10pro`（本仓库新增的包装脚本，
   去掉 `cygpath`、`TOOLCHAIN_ROOT`、`.exe` 后缀，注入 Homebrew 的
   `PKG_CONFIG_PATH` 与 `-I/-L`）。configure 一次通过，
   `ninja` 1483/1483 完成，**未改动任何一行 overlay C 代码**。
4. 机器型号：10 Pro 用 `-M bes1503`（不是 `xiaomi-q66nfc`，那是手环 11），
   在 `--without-default-devices` 下正常编入。
5. 镜像：`.venv/bin/pip install littlefs-python==0.19.0`（Python 3.15/arm64
   现编通过；Homebrew Python 受 PEP 668 限制需 venv），
   再 `python3 tools/create_images.py --model band10pro`
   → `nv.img`(128 KiB) + `nor-seed.bin`(16 MiB)。
6. 固件：`firmware/band10pro/firmware.zip.part00{1..4}` 直接 `cat` 合并即
   3.101.043 完整 OTA ZIP，SHA256 与 `manifest.json` 一致；解出
   `vela_ap/bl2/ota/resource.bin` 放进 `models/band10pro/firmware/`。

## 启动命令（无头 + QMP）

```bash
cd vendor/miband-qemu/models/band10pro
../../.build-macos/band10pro/build/qemu-system-arm \
  -M bes1503,hardware-config=config/hardware.json,\
resource-image=firmware/vela_resource.bin,\
nor-fs-image=config/factory/nor-seed.bin,\
flash-state=userdata/flash.journal,\
gpu-scanout-bridge=off,display-rotation=0 \
  -kernel firmware/vela_ap.bin \
  -L ../../.build-macos/band10pro/qemu/pc-bios \
  -display none -monitor none -qmp tcp:127.0.0.1:<port>,server=on,wait=off
```

`tools/run_p67_macos.py`（本仓库新增）把上面这套包成了带 QMP 重连、
`screendump` 出图的一次性脚本。

## 已解决：JIT SIGILL → 用 TCI 构建

`--enable-tcg-interpreter` 的构建（`.build-tci`）完全绕开了生成宿主机器代码这一层，
实测连续运行 120 秒以上不崩，且**成功渲染出原厂固件界面并接受了触摸注入**：

```
选择语言 / 简体中文 / 繁體中文 / English     ← 开机 150s 后
（点选语言后进入）小米手环10 Pro 0001
请在手机下载小米运动健康 App 以连接设备 / 上滑扫码
```

TCI 需要 libffi：Homebrew 的 libffi 是 keg-only，且 `pkg-config --cflags libffi`
会解析到 macOS SDK 的 `.../MacOSX26.sdk/usr/include/ffi`，
必须把 `pkg-config --cflags/--libs libffi` 一起塞进
`--extra-cflags/--extra-ldflags`，否则 `fatal error: 'ffi.h' file not found`。

触摸注入走 QMP `input-send-event`，绝对坐标按 `value = round(v*32767/(size-1))`
编码（与 `models/band10pro/tools/sensor_control.py:pointer()` 一致），
封装在 `tools/onboard_p67.py`。

对照上游 `docs/VERIFICATION.md` 的验收步骤 —— 「离线启动，等待语言页，
点选语言，再正常停止」—— **macOS 移植已全部达成**。

## 虚拟链接手机：数据通道已打通

`tools/face_debug_p67.py` 实现了不依赖手机、不依赖射频的安装回路：

```bash
python3 tools/face_debug_p67.py --fresh-journal \
  --face <绝对路径>/probe.face --tap 168,132 --boot-wait 170 --settle 22
```

实测：

* `bt-backend` 不设时**默认即 `synthetic`**（`miband.c:1161`）—— 内置无射频虚拟控制器。
* `install-file "16:<绝对路径>"` 返回 `ok`，`install-catalog` 回读
  `16:166518:7122e510…`（MASS 16 = 表盘、字节数、SHA256）。
  `miband.c:649` 把 `miband_install_catalog_commit` **同时**挂给蓝牙接收路径和
  `install-file`（`miband.c:992`），两条路共用同一个提交函数，
  所以这条注入与真机手机安装等价。
* QMP `input-send-event` 触摸有效：点语言页 (168,132) 成功进入绑定页。
* QMP `screendump` 稳定出 336×480 图。

两个必踩过的坑：

1. **换基础镜像必须删 `userdata/flash.journal`**，否则
   `miband flash store: wrong base image or state header (errno=22)` 直接 `exit(1)`。
   脚本的 `--fresh-journal` 为此而生。
2. **不要直接改 `vela_resource.bin` 里的表盘槽位。** 资源区是 littlefs，
   块不按 `.face` 边界排布；按 magic 偏移整槽覆写会连带打碎字体与本地化表 ——
   实测标题退化成 `language.setupwizard` 这类 key、中文全变豆腐块。
   注表盘请只走 `install-file`。

## 剩余阻塞：绑定页挡住「渲染」

表盘已进目录，但 guest UI 仍停在「请在手机下载小米运动健康 App 以连接设备 /
上滑扫码」，看不到渲染结果。

`diag-skip-binding` / `diag-bypass-binding-auth` 都在，但都需要
`MiBandBindingUiProfile`（`id / ap_sha256 / config_sha256 / flash_base /
2 × {address, guard_address, expected[8], replacement[8], guard[32]}`），
而 `bes_runtime_config.c:38` 里它是 `{{.id = ""}}` 占位，真实条目要从
`config/hardware.json` 的 `binding_ui` 键 LOAD 进来 —— 公开的 hardware.json
`machine_properties` 只有 `gpadc-model / pmu-status-model / pmu-rtc-model /
charger-model / gpu-render-index8 / gpu-scanout-bridge` 六项，**没有 `binding_ui`**，
band11 更是连 hardware.json 都没有。仓库里找不到任何分发的绑定 profile。

`miband_hci_synthetic.c` 只到控制器层（HCI 命令、LE 广播/扫描、Connection
Complete、SMP LE Encrypt 原语），注释写明 "intentionally no radio/link
simulation"，不会主动扮演手机。

要看到渲染只剩两条路，都不是小工程：

1. **自己逆导向导位点做 AP 补丁**：在 `vela_ap.bin` 定位绑定判断，
   再按 `MiBandBindingUiPatch` 格式填 profile。
2. **宿主侧写真虚拟手机**：用 `bt-synthetic-inject` /
   `bt-synthetic-pop-ap-packet` 手搓 GAP + ACL + L2CAP + ATT/GATT + SMP +
   小米 AuthKey 握手（AuthKey 已从真机读到过）。

### 路线 1 的四次尝试（全部落空，别重复）

已用模拟器实测到的 AP→内存映射（这条是**有效**的，后续可用）：

```
runtime = 0x0c000000 + ap_file_offset + 0xC0000      # 线性，四点验证一致
BEST1502P_XIP_BASE = 0x0c000000                      # best1502p.c:52
boot_pc = XIP + 0x000c0000 + 0x11                    # best1502p.c:1253
```

用 QMP `pmemsave`（**地址成员名是 `val`，不是 `start`**）抓了
`0x0c000000+13.9MB`、`0x1c000000+8MB`、`0x20000000+4MB` 三个窗口后：

| 尝试 | 结果 |
| --- | --- |
| 用 `refresh_bind_page_for_language` / `bind_state` / `get_bind` / `app_bind_manager` 反查 | 这些串聚在文件偏移 ~0xacfd00–0xacff00，是 **日志 tag 表**，不是绑定逻辑 |
| 在 AP/内存里找指向这些串的**绝对指针** | 0 命中 —— 代码用 PC 相对寻址 |
| 抓 guest UART 控制台看绑定决策 | 只有 2.1 KB 且多为擦除态噪声，app 层不打日志 |
| 找 NuttX `g_symtab` 符号表 | 无（14 个候选全是串碎片误报） |

所以路线 1 实际等于**裸反汇编 Cortex-M33 Thumb 的向导状态机**，
没有捷径元数据可用。

### 其他已排除的注入方式

* `bt-synthetic-inject` 只收 `0:`/`1:` 前缀的 **命令 / ACL 原始 hex**，是链路层，
  不提供 app 层注入口。
* `flash.journal` 的头是 `MBFLASH1`，是 QEMU 自己的检查点格式，**不是 littlefs**，
  不能用 littlefs-python 直接改出「已绑定」标志。

### 路线 2 也已实测证伪

完整属性表里还有 `bt-smp-just-works` / `bt-le-passkey` / `bt-passkey` /
`bt-synthetic-peer-passive`，看起来像「虚拟手机配对」的开关。实测：

* `-M ...,bt-smp-just-works=on,bt-synthetic-peer-passive=off` 启动并点过语言页后，
  **仍停在绑定页**，无变化。
* 更关键：轮询 `bt-synthetic-pop-ap-packet` 连续 40 次全部返回空的 `"0:"` ——
  **guest 在绑定页根本没有通过 synthetic 控制器发出任何 HCI**。
  也就是说没有链路可应答，宿主侧写真手机这条路在当前配置下没有输入可对接。

结论：`diag-skip-binding` 的 patch profile 是**唯一**已知可行的解绑途径，
而它需要对 AP 做裸 Thumb 反汇编来定位绑定判断 —— 没有符号表、没有绝对指针、
app 层不打串口日志，属于独立的多小时逆向工程，不是模拟器移植的剩余量。

## AP 补丁回路已建立并验证（可用的调试能力）

关键前提：**链接基址是 `0x2C0C0000`**（`FLASH_BASE=0x2C000000 | OTA_CODE_OFFSET=0xC0000`），
不是 QEMU 映射的 XIP 别名 `0x0C000000`。之前所有「找不到绝对指针」的结论
都是因为用错了基址 —— 换成 `0x2C0C0000 + 文件偏移` 后立刻命中：

```
persist.setup_wizard  -> 5 处引用
每个 *.setupwizard 页ID -> 各 1 处注册点
setupwizard 串簇      -> 33 处绝对引用（页表确实存在）
```

反汇编出的向导判据（文件 `0x3fe80c`）：

```
mov.w r1, #-1            ; 默认值
ldr r0, [pc, #0x248]     ; "persist.setup_wizard"
bl  #0x2c3dafe0          ; property_get_int(key, default)
cmp r0, #0
beq.w #0x2c4beaf0        ; 分支
sub.w r0, r0, #1 / clz / lsrs r0,#5   ; 判 ==1
strb r3, [r2]            ; 落 0 或 2
```

**补丁回路本身已用对照实验证明有效**：把 AP 里的 `CHIP=best1503` 改成
`CHIP=QEMUCONTROL` 再用 `-kernel` 启动，串口横幅确实打出 `CHIP=QEMUCONTROL`。
所以 `--ap <patched>` 是生效的。

但**强制 `persist.setup_wizard = 0/1/2 三种值，面板截图完全一致**
（三张 PPM sha256 相同）→ 这个点不是绑定前的向导门，
`persist.setup_wizard` 只是记录进度。

另一个必须知道的约束：**flash store 会校验基础 AP 镜像**，换 AP 不删 journal 会直接
`miband flash store: wrong base image or state header (errno=22)` 退出。
（这条在控制实验里踩过一次，`--fresh-journal` 必须带上。）

### 下一步的更好线索（尚未验证）

比向导页 ID 更可能直达绑定点的锚：`EVT_NEW_BIND`（文件 `0xac5238`）、
`bind_status`（`0xacefbb`）、`binding`（`0xa7c20b`）—— 这是 UI 事件名表，
绑定成功事件走的就是它。

## 根因级发现：guest 没有可用的持久存储

抓两路串口（`-serial file:u0 -serial file:u1`）后，uart1 有 19 KB 完整 NuttX 日志，
其中第 293 行是关键：

```
[ ERROR] [ap] lfs_dir_fetchmatch: Corrupted dir pair at {0x0, 0x1}
```

即 **guest 的 userdata littlefs 超级块对是擦除态**。而 AP 横幅里的
`__userdata_start=<##BASE##>` 是**小米固件自己的未替换占位符**
（在 overlay 和 config 里 grep `##BASE##` / `__userdata_start` 均无命中），
说明模拟器绕过了原厂 bootloader 的分区装配。

后果是链式的：`persist.*` 永远写不进去 → 向导进度每次重启都丢 →
**即使真手机来绑定，绑定状态也存不住**。这解释了之前反复观察到的
「每次开机都回到语言页」，也说明绑定页不是单纯缺一个补丁。

## NSH 存在但不会启动（UART 路线关闭）

AP 里确实有 shell：`nsh_main`、`NuttShell`、`nsh:`（16 处）、`nsh_console`、
`setenv`、`boardctl`、`/dev/console`。但两路串口日志里 **`nsh` 命中数为 0**
（uart0 2144 B、uart1 19326 B 全量 NuttX 日志），即固件根本没拉起 NSH。
所以「修 UART 输入模型 → 进 shell 跑 setenv」这条路**不成立**，
省掉了一次多小时的无用工程。

## 已确认可用：宿主直接读写 guest 的 NV littlefs

`config/factory/nv.img` 是**合法且可挂载**的 littlefs（偏移 0 处即
`00 00 00 00 f0 0f ff f7 6c 69 74 74 6c 65 66 73` = "littlefs" 魔数），
由 `initialize_factory.create_factory()` 生成。实测：

```python
fs = littlefs.LittleFS(context=littlefs.UserContextFile(str(img)), block_size=256)
fs.mount()                      # OK，根目录为空
fs.mkdir("/miwear")
fs.open("/miwear/probe.txt","wb").write(b"hello-from-host")   # 读回一致
```

即**宿主可以不经任何代码补丁，直接给 guest 的 NV 分区写文件**。
`tools/nv_lfs.py` 封装了 list/cat。这是「虚拟链接手机」目前最实在的一块地基：
只要知道固件读绑定/属性时用的文件名与格式，就能直接投放，而不是逆向补丁。

## 其他本轮确认的事实

* `miband_install_catalog_commit`（`miband_install_catalog.c:77`）**不做任何内容校验**，
  只 `miband_store_append` + restore。所以 `install-file` 不能当表盘格式校验器
  —— 这点是读源码确认的，省掉了一次无效启动。
* `bt-backend` 取值由 `miband_hci_backend_parse` 决定，分支为
  `MIBAND_HCI_SYNTHETIC` / `MIBAND_HCI_SERIAL(path,baud)` / `MIBAND_HCI_LINUX_RAW(device)`。
  全部是 **HCI 链路层**，所以虚拟手机必须自带 GAP/GATT/SMP，成本不变。
* AP 补丁回路经对照实验证明有效（`CHIP=QEMUCONTROL` 出现在串口横幅）。
* 强制 `persist.setup_wizard = 0/1/2` 三种值，面板截图 sha256 完全一致
  → 该点不是绑定前向导门。



### 向导其实由 persist 属性门控（新线索，未验证）

扫 AP 得到 **178 个 `persist.*` 键**，其中有
**`persist.setup_wizard`**（文件偏移 `0xac7d98`），以及整套向导页 ID：
`language.setupwizard`、`pairing.setupwizard`、`bind_success.setupwizard`、
`bind_failed.setupwizard`、`confirm_phone.setupwizard`、`usermode.setupwizard`、
`showmode.setupwizard` … —— 说明向导是**表驱动的页状态机**，
而且开关是属性，不只是绑定状态本身。相关键还有
`persist.back_dial_id`、`persist.delete_wf`、`persist.deco_install_count`、
`persist.watchface_edit_enable`。

### uart0 是只读控制台，没有 NSH

`tools/uart_shell_p67.py` 用 `-chardev socket,...,server=on,wait=off` 接上 uart0
（注意 chardev 后端名是 `socket`，写 `tcp` 会报 `'tcp' is not a valid char driver name`），
拿到了原厂启动横幅，顺带证实了前面量到的映射：

```
CHIP=best1503   KERNEL=NUTTX   BUILD_DATE=Aug 4 2026
FLASH_BASE=0x2C000000   FLASH_NC_BASE=0x28000000   FLASH_SIZE=0x1000000
OTA_CODE_OFFSET=0xC0000        <-- 正是实测的 AP 运行时偏移 delta
NV_REC_DEV_VER=2   USER_SEC_SIZE=0x2000   FACT_SEC_SIZE=0x2000
__userdata_start=<##BASE##>    <-- 占位未被模拟器填充
```

但发 `help` / `printenv` 只收回擦除态噪声 —— **uart0 是单向日志口，没有交互 shell**。
旁证：guest 一直在写 `pl011_write: Bad offset 0x54`，说明它用的是 BES 自己的 UART
寄存器布局（overlay 里有 `best1700_uart.c`），QEMU 的 PL011 模型收不到输入。
所以「进 guest shell 直接 `setenv persist.setup_wizard`」这条最省事的路，
要先修 UART 输入模型才可能成立。




在此之前，模拟器已可用于：容器格式校验、触摸/手势回归、安装目录验证，
以及任何不依赖绑定后 UI 的调试。

## 对表盘工程的影响

模拟器是**加速回路**，不是交付物。表盘打包器的正确性已由固件里 17 个原厂
`.face` 的 760 个栅格块逐字节往返验证独立证明；`-10001` 已在真机消除，
`design.face` 现为手环「当前表盘」。控件坐标语义（资源型 vs 控件型元素）
真机看下来排版正常。见 `miband10pro-face/native/FORMAT.md`。


## 2026-10-29 续：向导门 = `persist.setup_wizard`，且已定位到可改点位

### 之前「persist.setup_wizard 无效」的结论是错的

那次改的是文件偏移 `0x3fe80c` 的门，反汇编看清了它读的是
**`persist.miwear_companion_device_type`**（`ldr r0,[pc,#0x248]` 之后
`bl #0x2c3dafe0`，比较 `#0`）——不是 setup_wizard。属性名搞错，所以三种取值
面板截图完全一致是必然结果。

### 真正的状态位

```
0x2c4be54c  get_setupwizard_status():
   ldrb r0,[r3]   ; r3 = 0x2010c971
   bx   lr
```
调用方一律 `cmp r0,#1`，**1 = 已绑定**（`SETUPWIZARTD_STATUS_BONDED`）。

该全局在初始化时由属性直接写入（文件 `0x3fe7f0`）：

```
0x2c4be7f0 movs r1,#0
0x2c4be7f2 ldr  r0,[pc,#0x258]      ; "persist.setup_wizard"
0x2c4be7f4 bl   #0x2c3dafe0         ; property_get_int(key, 0)
0x2c4be804 strb r3,[r2]             ; -> 0x2010c971  status
0x2c4be806 strb r3,[r5]             ; -> 0x2010c970  prev
```

全镜像只有 4 处引用 `persist.setup_wizard`（`0x2c4be7f2`、`0x2c5ef6f0`、
`0x2c6ffa3a`、`0x2c70644c`），全部是**读**；写走通用属性通道，所以从代码侧
看不到 `setenv`。guest 里也没有 NSH（uart0 单向日志），因此无法用
`setenv persist.setup_wizard 1` 这条最干净的路。

### 已做的补丁

`vela_ap.bin` 文件 `0x3fe54c`：`80 b4 03 4b` → `01 20 70 47`
（`movs r0,#1 ; bx lr`），产物 `models/band10pro/firmware/vela_ap_bound.bin`
（sha256 `d68b0877…`，长度不变 13 795 728）。

### 结果：面板从 60s 到 240s 全黑（含触摸），原厂 AP 同时段显示「选择语言」

两种解释都还成立，正在跑对照实验：

1. 已过向导、进了正常表盘页，但熄屏且触摸唤不醒；
2. 补丁让 UI 线程崩了/挂住。

判据：原厂 AP + `--burst 8:15 --burst-tap 168,112`（点在「简体中文」上）。
若原厂画面因点击而变化，说明触摸注入有效，那么 bound 版的全黑就更可能是崩溃；
若原厂画面全程不变，触摸通道本身无效，黑屏无法区分两种情况，需要换判据
（例如读 `0x2010c971` 周边 RAM 心跳、或看 flash.journal 是否继续增长）。

### 顺带修掉的两个坑

* `flash.journal` 会记录基镜像；**换 AP 也必须换 journal**，否则
  `miband flash store: wrong base image or state header (errno=22)` 直接退出。
  `face_debug_p67.py` 现在把上次 AP 的 sha256 记在
  `userdata/flash.journal.apsha`，不一致就自动删 journal。
* 新增 `--burst N:GAP [--burst-tap X,Y]`：连拍而不是只抓一张迟到帧。

### 模拟器其实自带「虚拟手机」

`source/overlays/band10pro/hw/arm/` 里 `miband_bt.c`（2251 行）+
`miband_hci_synthetic.c` + `miband_hci_gatt.c` + `miband_xiaomi_protocol.c` +
`miband_xiaomi_pb.c` + `miband_xiaomi_mass.c` 是一整套：合成 HCI 控制器
（guest 发 `LE_Create_Connection` 时自动回 `LE Connection Complete`）、
小 ATT/GATT server（按 `ble_tx_uuid/rx_uuid` 对上特征）、以及小米
L1/L2 + MASS + PB(prepare/result) 协议栈。也就是说
**安装通道不是模拟出来的假象，而是固件自己在跑真协议**——
`install-file 16:design.face` 之后 `install-catalog` 回
`16:329441:9dfe1a22f99cdc65423a50d9ce6252e7ed913dfa7116f00addc412648f28d5d8`，
与真机安装时同一套提交函数（`miband_install_catalog_commit`）。

缺的只是「绑定」这一步：`miband_binding_ui.c` / `miband_binding_auth.c`
两个适配器（作者原本就是用来跳过绑定页和 AuthKey 的，日志原话
`binding UI adapter: %s site=%#x config=%s; no Bluetooth bond`）
对本固件只留了空 profile（`bes_runtime_config.c:38`
`miband_binding_ui_profiles[1] = {{.id = ""}}`），所以
`diag-skip-binding=on` 会 `exit(1)`。补齐 profile = 直接改这两个 .c 里
的 patch 表（地址我们已经量出来），比在 host 侧实现 miwear PSK 绑定
握手（ECDH-P256 + HMAC + CCM）便宜得多。

### 对照实验：触摸注入确实有效

原厂 AP + `--burst 8:15 --burst-tap 168,112`（点在「简体中文」上）：
`b00` 与 `b01..b07` 不同，且 `b01` 已是向导下一页 ——

> 小米手环10 Pro 0001
> 请在手机下载小米运动健康 App 以连接设备
> 上滑扫码

所以 `input-send-event` → Zinitix 触摸模型 → `touch_irq_pin` 边沿这条链是通的，
**向导可以靠点击往后走**。之前「facedebug 点了没反应」是因为点在了空白处。

### 于是 bound 版的全黑有两种解释，正在用第二次实验区分

* 熄屏：过了向导进正常表盘页后，面板 ~10 s 自动熄屏；`hardware.json` 里
  `touch_sleep_gesture=1`、`touch_double_tap_status=1`，说明这代固件支持
  双击亮屏，而 15 s 一次的**单击**不构成双击，所以永远唤不醒；
* 崩溃：把 `get_setupwizard_status()` 全局钉成 1，可能让别处去取并不存在的
  绑定密钥（`MIWEAR_TOKEN_KEY` / `miwear_bind_key`）而挂掉。

判据：`--burst-dbl`（每帧连打两下）。亮屏即说明已过向导。

### bound3/bound4：双击与抬腕都没能点亮面板

| 实验 | 动作 | 结果 |
| --- | --- | --- |
| bound3 | `--burst 8:12 --burst-dbl`（每帧连打两下，试双击亮屏） | 8 帧全黑，哈希一致 |
| bound4 | `--burst 8:12 --poke sensor-accel-y=9.8 --poke sensor-accel-z=0 --poke sensor-gyro-x=3.0` | 8 帧全黑 |

bound4 顺手读了 `sensor-state`，这是**判活的硬证据**：

```
channel 2  guest_reads 36   samples 49
channel 4  guest_reads 100  samples 135
channel 5  guest_reads 50…
```

也就是说 AP 没死，NuttX 的 ADC/传感器线程一直在采样，只是 UI 不出图。
所以「整个固件挂了」可以排除，剩下两种：
UI 线程单独挂住，或者面板确实熄屏且这代固件的亮屏源不是触摸/加速度
（也可能是 `sensor-accel-*` 写进去了但 IIO 侧没被 guest 读到——
`sensor-state` 只列了 ADC 通道，看不到 IMU 的读取计数）。

## 突破：模拟器已经进入绑定后 UI 并渲染出原厂表盘

### bound 版全黑的真正原因是「熄屏」，不是崩溃

`vela_ap_bound.bin` 只改了 `get_setupwizard_status()`（`0x2c4be54c`）一个出口，
但紧挨着的第二个访问器 `0x2c4be560` 读的是另一个全局 `0x2010c9b5`，
它依赖 `*(0x2010c971)==1` 才走 true 分支——于是**两个 API 说法不一致**。
改成在属性被消费的那一点统一置 1：

```
文件 0x3fe7fe:  db b2 (uxtb r3,r3)  ->  01 23 (movs r3,#1)
```

即 `property_get_int("persist.setup_wizard",0)` 的返回值在写进
`0x2010c971`（status）**和** `0x2010c970`（prev）之前就被钉成 1，
两处 `strb` 都吃到同一个 1。产物 `vela_ap_bound2.bin`（sha256 `dbb42e68…`）。

### 亮屏靠「插充电器」的**边沿**，不是电平

`--poke sensor-charging=true` 每帧重复写同一个值 = 没有边沿 = 不亮。
`face_debug_p67.py` 现在每帧先把 accel 归平、`sensor-charging` 归 false，
再施加本帧的 poke，于是每帧都有一次 `false -> true` 的插入事件。

bound7（`--boot-wait 40 --burst 12:6 --poke sensor-charging=true`）：

```
b00 83c7f4ec7b01   b01 7bdf658b2096   b02/b03 d2dc21dc54e0
b04..b11 4aab8a72f0bf   <-- 稳定画面
```

`b11.png` 是一张**原厂大数字表盘**（01:32 + 卡路里气泡，充电态的暗色 AOD 风格）。
也就是说：

* 向导已经被真正跳过；
* GPU/LCDC 出图链路在 macOS TCI 构建上是通的；
* `sensor-state` 里 ADC 通道 `guest_reads` 持续上涨，AP 全程存活。

### 模拟器调试回路的最终形态

```
.venv/bin/python -u tools/face_debug_p67.py \
  --ap models/band10pro/firmware/vela_ap_bound2.bin \
  --face ../../miband10pro-face/native/design.face \
  --boot-wait 45 --burst 6:6 --poke sensor-charging=true \
  --post-tap "lp:168,240,1.5" --post-tap up
```

`--post-tap` 是安装完成后才执行的手势（`lp:X,Y,SEC` 长按、`up/down/left/right` 滑动、
`X,Y` 单击），用来在装完表盘后进「表盘列表」并选中它。

### 顺带发现：AP 补丁可以完全声明式

`bes_runtime_config.c:137` 的 `LOAD("binding_ui", …)` 说明
`hardware.json` 支持 `binding_ui` / `binding_auth` 段，字段是
`id / ap_sha256 / config_sha256 / flash_base / patch_count / patches[]`，
每个 patch 是 `address / guard_address / patch_size / guard_size /
expected[8] / replacement[8] / guard[32]`。
也就是说上面那两个 2 字节补丁可以写进 `config/hardware.json`，
由 `-M …,diag-skip-binding=on` 在装载 AP 时自动打上，
不必再手工生成 `vela_ap_bound2.bin`（也不必重编 QEMU）。

## 模拟器现在是「可交互的绑定后真机」——但 install-file 不进表盘列表

face5 / face6 两轮把回路跑通了：

| 帧 | 动作 | 画面 |
| --- | --- | --- |
| p01 | 长按表盘中心 1.2 s | 进**表盘列表**，标题「幻境流彩」，底部垃圾桶 + 设置六边形 |
| p02/p03 | 左滑 | 切到「月白」（指针盘，周日 08\|16） |
| p05 | 再点一下 | 退出列表，进正常主屏卡片页（顶部状态栏 `01:55 10/29 周四`、活动三环卡、心率卡） |
| p06 | 左滑 | 切到「流云」（10:09:24 星期六 8/16 2560步） |
| p07 | 左滑 | **「打开手机APP 添加新表盘」** —— 列表到此为止 |

关键结论：**`install-file` 只写了模拟器侧的 catalog，没有进 guest 的表盘列表。**
`install-catalog` 回 `16:329441:9dfe1a22…` 且返回 ok，但列表里始终只有 5 张原厂表盘，
最后一张是「添加新表盘」占位页。所以之前「与真机安装等价」的判断要收窄：
两者共用 `miband_install_catalog_commit` 是事实，但真机上安装是
**guest 自己**在 PB prepare → MASS 分片 → 落盘 → 写数据库这条链上跑完的；
`install-file` 绕过了 guest，只让宿主记账，所以固件看不见它。

### 亮屏窗口约 12 秒

`--post-gap 1.2` 时 p01..p07 全部有画面；`--post-gap 4` 时只有 p01 有画面。
所以熄屏计时大约 10~12 s，且**触摸不重置计时**（只有插充电器的边沿能再点亮）。
连拍式操作要把间隔压到 1.5 s 以内，或者每步重新制造一次插拔边沿。

### 下一步的两条路（都还没验证）

1. **协议级虚拟手机**：用 `bt-synthetic-inject` 以「手机」身份推
   L1/L2 + PB prepare，让 guest 自己走 MASS 接收并落盘（`miband_bt.c` 里
   `miband_bt_gatt_write` 已经会解析 channel 2 / opcode 1 的 MASS 并调
   `mass_commit`，`miband_xiaomi_pb.c` 已经会编解码 prepare/result）。
   这是唯一能让表盘进 guest 列表的正路，但需要先有 BLE 连接。
2. **换 /resource romfs 里的原厂槽位**：`vela_resource.bin` 是只读 romfs
   （`-rom1fs-`，卷名 `rom 6a71ab02`，`size=0xa3cc270`），
   把某张原厂表盘就地换成 `design.face`（只改记录的 length 字段 + 覆盖数据区，
   前提是新表盘不超过该槽位的可用空间）。
   注意：这份 romfs 的记录布局**不是标准 romfs v1**——每条记录固定部分 16 字节
   （`+0` 未知 / `+4` 目录=首子节点、文件=兄弟指针 / `+8` length / `+12` 未知 /
   `+16` 名字），`tools/romfs_edit.py` 已能读出 `watchface/aod/362150007/resource.bin`
   这条链，但 `+0`/`+12` 两个字段的语义还没定，重建整镜像前要先补这一步。

### face7：`system_reset` 这条路不通

装完 `design.face` 后发 QMP `system_reset` 想逼固件重扫表盘列表，结果复位后
`r0-after-reset` 与之后 7 帧**全部纯黑**（哈希一致）。这台机器模型在 reset 后
显示桥不再出图，所以「重启重扫」这个探针本身失效，不能用来判断表盘有没有进列表。
已把两个死胡同补丁镜像删掉，只保留可用的 `vela_ap_bound2.bin`
（`vela_ap.bin` 仍为原厂 `519307675665e486…`，`vela_resource.bin` 为 `e80a8772935814af…`）。

## 找到并修掉一个真实的模拟器 bug：表盘 ID 偏移 +0x22 → +0x28

从 `/resource` romfs 里把原厂 `watchface/aod/362150007/resource.bin` 的头 64 字节
取出来，和 `reference/oracle/clean_362150007.face`、`face_00_362150007.face` 逐字节对比：
**三者完全一致，头部零差异**。也就是说 10 Pro 上 `.face` 与 `resource.bin`
**不是两种容器**，之前从 `Compiler.exe` 反编译得到的「`array[5]=10` + ID 在 40」
这条规则里的「10」标志位在这份固件里根本不参与区分。

关键是 ID 的位置：

```
+0x00  5a a5 34 12 00 00 01 00 00 00 00 00 00 00 00 00
+0x10  00 08 00 00 00 00 01 00 00 00 00 00 01 00 00 00
+0x20  68 03 00 00 00 00 00 00 33 36 32 31 35 30 30 30   <- "36215000" 从 +0x28 开始
+0x30  37 00 ...
```

而 `miband_nand_device.c:169` 写的是 `const uint8_t *id = watchface + 34;`（+0x22），
那里是 `00 00`，`count` 立刻归零 → `watchface_id` 为空 →
`/data/app/watchface/market/<id>/resource.bin` 这条目录链**从来没被建出来过**，
固件的表盘管理器只枚举 `market/*`，所以任何安装都不会进列表。
我们的 `design.face` 头部第 40 字节起就是 ASCII `167210065`（9 字符，和原厂同构），
打包器侧本来就是对的，错的是模拟器读的偏移。

已改：`source/overlays/.../miband_nand_device.c` 与展开构建树里的同名文件，
`+34` → `+40`，并把 `watchface_size >= 34` 收紧成 `>= 40 + 24`；
注释里写明了证据来源。`ninja qemu-system-arm` 增量重编只花了几秒。
`tools/face_debug_p67.py` 加了 `--install-early`（在固件枚举 market 之前就把表盘提交进去）。

## 但修完还不够：安装仍然不进列表（face8 实测）

face8：`--install-early --fresh-journal`，install 返回 ok、catalog 回读正确，
连拍里面板正常出图（b00..b13 三段不同画面），长按进列表、左滑 6 次仍是
幻境流彩 / 月白 / 流云 … 那 5 张原厂表盘，主屏最后停在一张原厂大数字盘上。

推理链（都有源码依据，但**尚未证实**）：
`miband_install_catalog_set_nand()` 把 overlay 放在
`resource_first_page + resource_page_count` = 页 102400 = NAND 偏移 0x0C800000，
且 `miband_nand_set_mass_files()` 第一件事是
`g_hash_table_remove_all(nand->overlay_pages)` —— 它是**整片替换**那个区域。
如果固件真正的 `/data` 不在页 102400，这个 overlay 对 guest 就是不可见的；
如果在，固件自己的 `/data` 内容会被抹掉、列表会坏掉。
实测列表没坏 → 倾向前者，即**作者猜的 /data 位置和本固件不一致**。

另外 `flash.journal` 里搜不到 `resource.bin` / `market` / `active.bin` 这些名字，
只有 design.face 自带的 `167210065` 出现一次 —— 说明 overlay 页是**纯内存**的，
不落 journal，也就无法从宿主侧静态验证它有没有被 guest 读到。

## 顺带挖到一个更干净的向导开关

AP 里除了 `persist.setup_wizard` 还有 **`persist.midemo_mode`**（文件 `0xab36f4`，
9 个字面量引用点：`0x37ae48 / 0x37f158 / 0x39d9ec / 0x3fea14 / 0x3ff514 /
0x5ed888 / 0x5edd80 / 0x63fbc8 / 0x64532c`），以及 `persist.system_mode`、
`persist.gauge_soc`。demo/展示模式是零售机上「不绑定也要出表盘」的那条路，
比把绑定状态钉成 1 更贴合真实行为，值得一试。

## 下一步（按性价比排序，都还没做）

1. 定出固件真正的 `/data` NAND 区域：`/dev/bes_flash` 是唯一块设备，
   分区表在固件自己的 flash driver 里按挂载点名 strcmp 匹配，
   需要从 `0x2c1c6640` 这条挂载路径往下挖；拿到真实页号后改
   `miband_install_catalog_set_nand()` 的入参，安装就能真正落进 guest。
2. 试 `persist.midemo_mode` 替代 `persist.setup_wizard` 钉 1。
3. 走 `bt-synthetic-inject` 的协议级虚拟手机（让 guest 自己写盘）——
   这条路和真机一致，但要先有 BLE 连接。

## 决定性证据：10 Pro 的 `/data` 根本不是 littlefs，所以模拟器的 overlay 是空转

`flash.journal` 的 `MBFLASH1` 格式被 `models/band10pro/tools/device_tools.py:journal_nor()`
完整写清楚了：64 字节头（`MBFLASH1` + `nor_size` + `page_size` + `page_count` + crc）、
初始 NOR 镜像、然后是不定长记录 `(kind, address, size, crc)`：
`1`=NOR 写、`2`=NAND 页写（`size == page_size == 2176`，含 OOB）、
`3`=NAND 块擦、`4`=MASS 记录（`address` 就是 mass type）。
用它解析 face8 的 journal：

```
nor_size=0x1000000  page_size=2176  page_count=0x40000
记录数: kind1=10  kind2=6588  kind4=1
NAND 页范围: 102400 .. 108987          <-- 正是 resource(0..102399) 之后
MASS: type=16 size=329441              <-- design.face 确实进了 journal
```

页 102400 起的内容：

```
page 102400: 03 00 00 00 01 00 00 00 ff ff 6f 66 66 6c 69 6e 65 6c 6f 67   "offlinelog"
page 102402: 01 00 00 00 01 00 00 00 ff ff 70 65 72 73 69 73 74 2e 64 62   "persist.db"
page 102404: 03 00 00 00 01 00 00 00 ff ff 64 66 78                        "dfx"
```

**没有 `littlefs` 超级块**（整片 guest NAND 数据里 `littlefs` 出现 0 次）。
这是小米/NuttX 自己的按名存储：每条 = `u32 类型 | u32 序号/长度 | u16 0xffff | 32 字节名 | 数据`。
把名字全解出来（125 种），关键条目：

```
market(1)  mass(2)  app(10)  aod(7)  builtin(14)  deleted(78)
362150001..362150007   120917385439 / 120917387777 / 120917387778 /
120917390547 / 120917390548 / 120917391135 / 120917392326 /
120917392328 / 120917392332 / 120917392528 / 120917392537
install_info_v1.db(50)   database.db  health.db  home.db  framework.db  bt_storage.db
config.json(31)          1.bin/2.bin/3.bin   1.png/2.png/3.png
persist.db               offlinelog          dfx
```

结论链：

1. 作者猜的 `/data` 位置（页 102400）**是对的**——guest 确实在那里读写。
2. 但 `miband_nand_set_mass_files()` 往那里手写 **littlefs** 页，10 Pro 的挂载器
   认不出来，于是 guest 自己按名存储重建了一遍，overlay 白写。
   （band11 大概真是 littlefs，所以那侧是能用的。）
3. 表盘列表来自 `/data` 里的 `market/<faceId>` + `install_info_v1.db`，
   而 `resource.bin` 这个名字在 journal 里出现 **0 次** —— 说明本固件在
   `/data` 里用的是 `1.bin/2.bin/3.bin` 这类名字，不是 `market/<id>/resource.bin`。
   所以要让安装的表盘进列表，得按**这个按名存储格式**写 `market/<id>` 和
   `install_info_v1.db` 条目，而不是照抄 band11 的 littlefs。

也就是说：`+34 → +40` 那个修复是必要但不充分的；真正的堵点是
`miband_nand_set_mass_files()` 的文件系统格式选错了。

## 两条已经量到底的下一步

* **A（推荐）**：把 `miband_nand_set_mass_files()` 对 10 Pro 改成写这个按名存储。
  格式已经能观测：`u32 type | u32 seq | 0xffff | char name[32] | payload`，
  页 2176 字节（2048 + 128 OOB），`deleted` 出现 78 次说明有墓碑机制。
  还缺的是 `install_info_v1.db` 的内部结构（50 个条目，值得先解出来，
  因为它才是列表的真源头）。
* **B（更快见效）**：走 `/resource` romfs 原地换槽。
  已经量到记录布局：`+0` 子树大小? / `+4` 目录=首子节点、文件=兄弟 /
  `+8` data 长度 / `+12` 校验? / `+16` 名字 / 数据紧跟其后。
  原厂 `362150007/resource.bin` 的 data 长度只有 **40,680 字节**
  （`face_00_362150007.face` 13,415 字节），而 `design.face` 是 329,441 字节 ——
  所以先把 `design.face` 压到目标槽位以内（深色底 + 少色索引 RLE 收益很大），
  再只改记录的 `+8` 长度字段 + 覆盖数据区，**不重建整镜像**，风险可控。

# ✅ 成了：模拟器把 design.face 画出来了

## 10 Pro 的 `/resource` 其实是「可原地换槽」的 romfs

先把记录布局用**穷举验证**钉死，而不是靠猜指针：在整张 171 MB 镜像里找
`"resource.bin\0"` 这个文件名字符串，记录头就在名字前 16 字节，
数据紧跟在名字（4 字节对齐）之后。判据是硬碰硬的——
`+8` 处的大端 u32 必须等于该 `.face` 的真实长度，且数据首 4 字节必须是 `5a a5 34 12`。
结果 **17/17 全部命中**，并把每个槽位和它的表盘 ID 对上：

```
id=120917385439(流沙)      len= 3,541,395  rec=0x034f76a0  data=0x034f76c0
id=120917387777            len=   681,292  rec=0x034510d0  data=0x034510f0
id=120917387778            len= 2,798,143  rec=0x031a5e10  data=0x031a5e30
id=120917390547(幻境流彩)  len= 3,838,034  rec=0x02dfcd30  data=0x02dfcd50
id=120917390548            len=   365,119  rec=0x02da3a70  data=0x02da3a90
... 共 17 张，最大 22,461,686
```

于是写成了 `tools/romfs_face_swap.py`：
**原地覆盖数据区 + 只改 `+8` 长度字段，不搬任何记录**，
所以镜像里所有指针天然保持有效，风险远低于重建 romfs 或改 littlefs NV。
脚本自带 `list` 与 swap 后的回读校验（17/17 仍可枚举）。

```
.venv/bin/python tools/romfs_face_swap.py swap \
    models/band10pro/firmware/vela_resource.bin \
    120917390547 ../../miband10pro-face/native/design.face \
    models/band10pro/firmware/vela_resource_df.bin
# swapped design.face (329,441 B) into 120917390547 (was 3,838,034 B) at 0x2dfcd50
```

原厂 `vela_resource.bin` 保持不动（SHA256 `e80a8772935814af…`），
改的是副本 `vela_resource_df.bin`，用 `-M …,resource-image=` 指过去即可。

## 实测结果

`--resource vela_resource_df.bin` 启动后，长按进表盘列表、左滑 3 次，
第 4 张卡片的标题就是 **DataDash**，内容是我们自己的排版：
电量 76%、`丙午年·九月十九`、`15:54`、`WED / 周三`、`10月28日 2026`、
天气卡（21° 晴·体感18° / 最高21° 最低14° / 湿度43% 东风3级）、
日出日落弧（06:06 → 17:09，日照 11h03m）、`8,432 步`（目标 12,000，70%）、
`站立 9/12`、页脚 `SHANGHAI · 浦东`。
截图：`models/band10pro/userdata/swap3/p04-swipeleft.png`。

**标题也是 DataDash** —— 说明固件是从我容器里的名称字段读的，
不是沿用 幻境流彩 的元数据，等于整条解析链（容器头 → 元素表 → 栅格 → 控件属性）
在真机固件上跑通了。

## 顺带修正一条旧结论

`--fresh-journal` 后默认表盘是 **流沙**（120917385439），
不是之前以为的 幻境流彩；swap1 里那张满屏大数字盘就是流沙，
所以「换了 120917390547 的槽但主屏没变」并不是没生效，
而是那张本来就不是当前表盘。

## /data 里的关键文件已经点出来（下一步的靶子）

把 live4 的 journal 按 `MBFLASH1` 全类型记录重放（`kind 1/2/3/4` 都要放行，
只认 kind 2 会在第一条 NOR 记录就断掉 —— 这是上一版脚本的 bug），
得到 2471 个 NAND 页、778 个具名条目，其中和表盘直接相关的是：

```
watchface_list.json     (10 个元数据页)
watchface.db            + watchface.db_unqlite_journal   <-- UnQLite
install_info_v1.db      (50 个元数据页)
persist.db              (20+ 个元数据页)
watchface               (目录节点)
```

元数据页头是 `u32 type=1 | u32 0x0163(=355) | u16 ffff | char name[32]`，
`0x163` 之后的字段像是数据块索引，所以这个存储和 littlefs 一样
**元数据块与数据块分离**，正文不在同名页里 —— 直接按名字捞 `watchface_list.json`
的 JSON 文本捞不到，得先解出它的块链。

这给了两条比「协议级虚拟手机」便宜得多的路：
把 `design.face` 写进 `market/` 并更新 `watchface_list.json` / `watchface.db`，
或者干脆改 `persist.db` 里的 `persist.back_dial_id` 指向我们的表盘。

## swap-all 反而黑屏（未解，但方向明确）

`romfs_face_swap.py swap-all` 把 12 个够大的槽位全换成 design.face 后，
`--boot-wait` 从 60 提到 130 秒，8 帧连拍仍全黑；
而 `sensor-state` 显示 ADC `guest_reads` 一路涨到 188，AP 活着。
对照：只换 120917390547 一个槽（df 镜像）时主屏正常出图、
列表里 DataDash 卡片也正常渲染。
所以不是启动挂了，而是**当前表盘恰好落在被换掉的槽位上时，全屏渲染失败，
但列表缩略图渲染成功** —— 这正是模拟器最该抓的那类 bug，值得单独查。

## 更正上一轮的判断：表盘列表里那张卡是「烘焙预览图」，不是实时渲染

对照时间就能看出来：列表卡片上 流沙 显示 `10:09`、`08/16 星期日 上午`，
而同一时刻模拟器主屏（充电页）的时钟是 `01:42` / `02:53`、日期 `10/29`。
卡片上的数字是**烧在 .face 容器里的预览栅格**，不是控件跑出来的活数据。
所以 swap3/p04 那张 DataDash 卡片证明的是：
**我们的预览块（索引色 + 字节 RLE）在真机固件上解码正确、排版正确、名称字段被正确读出**；
它还**没有**证明动态控件链路能在这条路径上出图。

## live6 / live7：把候选默认表盘换成 design.face 后主屏变黑

| 实验 | 换了哪些槽 | 主屏 |
| --- | --- | --- |
| live5 | 只换 流沙 120917385439 | 显示 幻境流彩（**默认表盘退到了另一张**） |
| live6 | 只换 幻境流彩 120917390547 | 显示 流沙（同样退让） |
| live7 | 两张都换 | **全黑** |
| live3/4 | 12 个够大的槽全换 | **全黑** |

live5/live6 的「换 A 显 B、换 B 显 A」是很干净的信号：
固件在 /resource 里加载我们这张容器时**判定不可用并回退**；
两张候选都不可用时就没有回退目标 → 黑屏。
注意这与真机不矛盾：真机上 design.face 是通过 `/data`（market 安装）成为当前表盘并正常出图的，
走的是另一条加载路径。

## 正在跑的对照实验（ctrl1）

把 **另一张原厂** `.face`（`clean_362150007.face`，40,680 字节）换进 幻境流彩 的槽位：

* 若主屏正常显示 362150007 的设计 → 换槽机制本身没问题，
  问题在 design.face 的容器内容不被 /resource 加载器接受；
* 若同样黑屏 → 是我们只改了 `+8` 长度、没同步 `+0`（下一个记录偏移链）
  或 `+12`（疑似校验和）导致的，需要把这两个字段一起算对。

顺带把 `+0` 的语义量清楚了：它是**下一条记录的绝对偏移**，
且 17 张表盘按 `+0` 串成一条**逆序链表**
（`120917392537 +0=23,628,962` → `120917392528` 记录在 `0x01688D00` 附近，
一路串到 `362150007 +0=40,914`）。`+12` 用 crc32 / adler32 / fnv1a / 32 位求和
（含带符号与含记录头的变体）**全部不匹配**，17/17 都不中，
所以它是小米自定义校验或者覆盖范围不是「纯 payload」。

## 对照实验把「问题在我们容器里」这条排除了

`tools/romfs_face_swap.py` 加了 `swap-keep` 模式：**只覆盖 payload，
一个记录字段都不改**（+0/+4/+8/+12 全部保持原值，尾部用 0 填回原尺寸），
这样 romfs 内部完全自洽。同一个槽位（120917390547）做三次：

| 实验 | 塞进去的内容 | 主屏结果 |
| --- | --- | --- |
| keep1 | `design.face` 329,441 B（容器内 id = 167210065） | 回退显示 流沙（活时钟 03:11） |
| keep2 | 原厂 `clean_362150007.face` 40,680 B（id = 362150007） | **全黑** |
| ctrl1 | 同上但改了 +8 长度 | **全黑** |

keep2 说明**换槽机制本身能让固件接受一张内容完全不同的原厂容器**，
`+12` 那个疑似校验和并没有拦住它（否则 keep2 应该像 keep1 一样回退到 流沙，
而不是把 362150007 顶上来）。keep2 之所以黑，最可能是
362150007 这张盘本身在大面积黑底 + 充电熄屏下就是全黑画面。

于是怀疑点收敛到一处：**容器里的 face id 必须和 romfs 目录名一致**。
keep1 里目录叫 `120917390547` 而容器头 +40 写的是 `167210065`，
固件按目录名去核对，对不上就判定这张盘不可用 → 回退。
`design.face` 的 id 只在 `+0x28` 出现一次（原厂容器也一样），
所以改 11 个字节就能改名。正在跑的 idmatch1 就是这个判决性实验：
把 `design.face` 的 id 改成 `120917390547` 再 swap-keep。

# ✅✅ idmatch1：固件把 design.face 当**当前表盘**实时画出来了

判决性实验成功。把 `design.face` 容器头 `+0x28` 的 face id 改成
它所在 romfs 目录名 `120917390547`，再 `swap-keep`（不动任何记录字段），
`--fresh-journal` 启动后主屏就是**我们自己的 DataDash**：

```
models/band10pro/userdata/idmatch1/b02.png   b07.png   （两帧完全一致 = 稳定态）
```

画面内容（左上是电量 `0 %`、右上 `丙午年 · 九月十九`、
巨大时间 `0 : 0`、`SUN / 周日`、`0 月0 日`、
天气卡太阳图标 + `0°` + 烘焙的 `最高 21°/最低 14°`/`湿度 43% · 东风 3 级`、
日出日落弧 `06:06 → 17:09` + `日照 11h03m`、
`今日步数 0 步` + `目标 12,000` + `70%`、`站立 9/12` 点阵、页脚 `SHANGHAI · 浦东`）。

**这是实时渲染而不是预览图**：我们容器里烘焙的预览是 `15:54 / 10月28日 / 21°`，
而屏幕上所有 DigitalNum 控件画的是 `0` —— 说明控件在运行期被数据源驱动了，
只是这台没有手机的模拟器里数据源全是 0。
背景、静态块、ImageList 的周日/SUN 图元也都按我们的排版正确落位。

## 于是模拟器直接复现了 Chase 报的那个真机症状

真机上他的原话是「数据是假的/不更新」，模拟器这边是
**排版全对、动态位全 0**。同一症状、两条路径（真机走 /data 安装、
模拟器走 /resource 槽位），说明这不是安装通道的问题，
而是**控件绑定的数据源在没有手机供数时读 0** ——
正是接下来要查的方向（哪些源在设备本地就有值、哪些必须手机推）。

## 可复现回路（三条命令）

```
.venv/bin/python tools/romfs_face_swap.py setid \
    ../../miband10pro-face/native/design.face 120917390547 /tmp/f.face
.venv/bin/python tools/romfs_face_swap.py swap-keep \
    models/band10pro/firmware/vela_resource.bin \
    models/band10pro/firmware/vela_resource_id.bin /tmp/f.face 120917390547
.venv/bin/python tools/face_debug_p67.py \
    --ap models/band10pro/firmware/vela_ap_bound2.bin \
    --resource models/band10pro/firmware/vela_resource_id.bin --fresh-journal \
    --boot-wait 70 --burst 8:10 --poke sensor-charging=true --out .../userdata/faceXX
```

改一次表盘 → 三条命令 → 拿到固件真实渲染的截图。原厂镜像始终不动。

## 为什么 `swap`（改 +8 长度）会失败而 `swap-keep` 不会

`+0` 是**下一条记录的绝对偏移**，17 张表盘按它串成链表。
把 `+8` 改小但不动 `+0`，固件按 `+0 - data` 认定的可用长度和声明长度不一致，
这张盘就被判不可用；`swap-keep` 保持槽位字节数不变、只覆盖内容，
容器自身是自描述的（尾部残留被忽略），所以整张镜像仍然自洽。
`+12`（疑似校验和）在 keep2 里被证明**不参与**载荷校验：
keep2 塞的是一张内容完全不同的原厂盘，固件照样接受并把它当默认表盘画。

## 虚拟手机会话：注入通道打通了，但绑定态下手环不应答

`bt-synthetic-inject` 的语法量清楚了（`miband.c:1276`）：
可选前缀 `<channel>:`，后面是**带 H4 类型字节的完整 HCI 包**的十六进制。
`miband_bt_inject_hci()` 里特别认 LE Meta Event：
`packet[1]==0x3e && packet[2]>=0x13 && packet[3]==0x01 && packet[4]==0`
→ 判定为 **LE Connection Complete**，并把 handle 登记为活动连接、
顺手 `miband_hci_gatt_connect(bt->gatt, handle)` 把合成 GATT server 挂上去。

构造好的两发（`--inject`，`face_debug_p67.py` 已支持，注入后自动截图 + `--btpeek-late` 复读观测属性）：

```
04 3e 1a 01 00 0000 01 00 010000100002 1800 0000 9001 bfee4dfedbff7b87
   ^事件 ^LEMeta ^plen ^子码 ^状态 ^handle ^role ^addrtype ^对端地址 ^间隔 ^延迟 ^超时 ^主控特性
02 0120 0700 0500 0400 0100f7
   ^ACL ^handle+PB ^总长 ^L2CAP长 ^CID=0x0004(LE ATT) ^Exchange Req ^MTU=247
```

link1 / link2 结果：两发注入都返回 **ok**，包确实进了控制器→主机的投递队列
（`bt-synthetic-last-bt-packet` 变成了我们注入的那条 ACL），
但 `bt-synthetic-pop-ap-packet` 连排 40 次全是空 `"0:"` ——
**guest 一个字节都没回**。面板画面也没任何变化（没有"已连接"提示）。

推断：`vela_ap_bound2.bin` 把 `persist.setup_wizard` 钉成 1 之后，
固件认为已经绑定完成，**根本不再广播**，
所以一条"凭空冒出来的连接完成事件"对它来说是非法状态，直接丢掉。
真实流程是：未绑定 → 广播 → 手机连接 → 绑定 → 才安装表盘。
于是 link3 在跑**未绑定态**（原厂 AP，停在配对页、正在广播）下的同一发注入，
看 guest 会不会应答。

## 下一步的岔路口

如果 link3 在未绑定态能应答，就顺着把 SMP/GATT 发现跑通，
再用 `bt-synthetic-inject` 推 L1/L2 + PB prepare + MASS 分片，
让 guest 自己落盘到 `/data` 的按名存储 —— 这才是完整的「虚拟链接手机」。
如果仍不应答，就得回到模拟器作者预留的两把钥匙：
`hardware.json` 的 `binding_ui` / `binding_auth` 段
（字段 `id/ap_sha256/config_sha256/flash_base/patch_count/patches[]`，
每个 patch `address/guard_address/patch_size/guard_size/expected[8]/replacement[8]/guard[32]`），
用 `diag-bypass-binding-auth=on` 免掉 AuthKey、
用 `diag-skip-binding=on` 只干掉绑定**页面**而保留未绑定态的广播，
这样「能连」和「能看表盘」就不再互斥。

## ✅ link3：虚拟手机的 ATT 链路真的通了

未绑定态（原厂 `vela_ap.bin`，停在配对页、正在广播）下注入那条
LE Connection Complete，前后对比是硬证据：

| 观测点 | 注入前 | 注入后 |
| --- | --- | --- |
| `bt-synthetic-last-ap-packet` | `""`（空） | `0200000b00070004001d0c000100ffff` |
| `bt-synthetic-last-bt-packet` | `040e0401092000`（普通命令完成） | `020020090005000400011d0c0006`（ACL/ATT） |

拆开 guest 主动发出来的那条：
`02`=ACL，handle 0，总长 0x0b，L2CAP 长 0x07，**CID 0x0004 = LE ATT 固定信道**，
ATT PDU `1d 0c 0001 00` = **Error Response**（对 Read By Type Request、句柄 0x0001）。
也就是说固件的 ATT 协议栈被这条连接唤醒，开始做服务发现方向的交互，
而模拟器的合成 GATT server 已经被 `miband_bt_inject_hci()` 挂到 handle 0 上。

**这是本项目第一次在没有真手机的情况下，让手环固件在蓝牙协议层"连上"了一个手机。**

## link4：加 `bt-smp-just-works=on` 反而更差

同一发注入，`bt-smp-just-works` 从 False 变 True 之后
`bt-synthetic-last-ap-packet` 仍是空 —— guest 不发了。
合理猜测：开了 just-works 后模拟器会对注入的 SMP 包做改写
（`miband_bt_rewrite_smp` / `rewritten[10]=0x03` 那条兜底路径），
而固件在等一个它认可的配对流程，ATT 层就没往下走。
所以 link3 的默认策略（不动 SMP）才是继续往前推的基线。

## 现在的完整状态

* 调试回路（表盘排版/栅格/控件）：**已可用**，三条命令，见上文 idmatch1。
* 虚拟手机链路：**ATT 层已连通**（link3），
  还差 SMP 配对 → 服务发现到小米的 TX/RX 特征 → 用 inject 推
  L1/L2 + PB prepare + MASS 分片，让 guest 自己把表盘写进
  `/data` 的按名存储并更新 `watchface.db` / `watchface_list.json`，
  以及把天气/步数/时间这些**数据源**喂上（这才是"数据不更新"的解药）。
* 两条路是互补的：romfs 换槽适合快速看排版，协议链路适合看活数据。

## 复现性核对：link3 是真的，但有两个必要条件

link3 之后连着三次重试（gatt1 / gatt2 / gatt3）都没有任何应答，一度怀疑是偶发。
逐变量回放后定出两个必需条件：

| 运行 | journal | 注入事件里的 Role 字节 | guest 是否回 ATT 包 |
| --- | --- | --- | --- |
| link3 | 自动新建（AP 换回原厂触发） | `01` = peripheral | **回** |
| gatt1 | 复用 | `01` | 不回 |
| gatt2 | 复用 | `01` | 不回 |
| gatt3 | 复用 | `00` = central | 不回 |
| gatt4 | `--fresh-journal` | `00` = central | 不回 |
| **gatt5** | `--fresh-journal` | `01` = peripheral | **回，且与 link3 逐字节相同** |

所以结论是：**必须 `--fresh-journal`（出厂未绑定态、正在广播）+ Role=0x01（本机为 peripheral）**。
Role=0x00 反而完全不应答——这版固件的连接完成事件只认「有中心连上我」这个方向。

gatt5 复现出来的那一发（和 link3 完全一致）：

```
bt-synthetic-last-ap-packet  02 0000 0b00 0700 0400 1d0c000100ffff
                             ACL  handle0 len11 L2CAP7 CID=0x0004(LE ATT)
                             ATT  0x1d = Error Response
bt-synthetic-last-bt-packet  02 0020 0900 0500 0400 011d0c0006
```

即：注入连接完成事件之后，**模拟器侧的合成 GATT server 先向手环发了一个 ATT 请求
（`01 1d 0c` = Exchange Request，MTU 0x0c1d=3101），手环的 ATT 协议栈应答了一个
Error Response（针对 Read By Type Request）**。
应答本身是「不支持的属性类型」这类正常拒绝，但链路是活的——
手环把这条注入的连接当成了真连接在处理，这是可复现的。

固化命令：

```
.venv/bin/python -u tools/face_debug_p67.py --fresh-journal \
  --boot-wait 100 --burst 3:8 --poke sensor-charging=true --settle 8 \
  --btpeek --btpeek-late \
  --inject 043e1a010000000100010000100002180000009001bfee4dfedbff7b87 \
  --inject 0201200700050004000100f7 \
  --out models/band10pro/userdata/gatt5
```

## 还没打通的那一段（下一轮的目标）

对端 GATT server 暴露的是 `hardware.json` 里那组 UUID
（`ble_service_uuid=1800`、`ble_tx_uuid=2a00`、`ble_rx_uuid=2a01`、
`miwear_service_uuid=1801`、`gattc_service_uuid=1802`），
而手环回的是 Error Response 而不是正常的 MTU 交换应答，
说明**属性表布局和固件期望的对不上**。要往下走需要：

1. 让对端 ATT 正确应答 Exchange Request（回 `03 <mtu>`），
   再让手环完成 Primary Service Discovery；
2. 手环订阅 TX 特征后，host 侧用 `bt-synthetic-inject` 推
   L1(`a5a5 …` + CRC16-ARC) / L2(channel,opcode) / PB prepare / MASS 分片，
   走 `miband_bt_gatt_write()` 里现成的接收与 `mass_commit`；
3. 让 guest 自己把表盘落进 `/data` 的按名存储并更新
   `watchface.db` / `watchface_list.json`，同时喂上天气/步数/时间数据源。

到那一步，「用模拟器调试数据不更新」才算完整闭环。

## 修掉第二个真实的模拟器 bug：对端不确认手环的 Handle Value Indication

重新按 ATT 规范拆包，之前那两条被读反了：

```
手环发出  02 0000 0b00 0700 0400 | 1d 0c00 0100ffff
                                  ^0x1d = Handle Value Indication（不是 Error Response）
                                  句柄 0x000c，值 01 00 ff ff
对端回    02 0020 0900 0500 0400 | 01 1d 0c00 06
                                  ^0x01 = ATT Error Response
                                  针对 0x1d，句柄 0x0c00，error 0x06 = Request Not Supported
```

也就是说 `miband_hci_gatt_acl()` 的 `switch` 里**根本没有 0x1d 这一支**，
手环的 indication 全部落到 `default:` 被回一个
`ATT Error (0x06 Request Not Supported)` —— 按规范，indication 收不到
Handle Value Confirmation 就会**永久停住**，这条链路之前是死的。

已在 `source/overlays/band10pro/hw/arm/miband_hci_gatt.c`（并同步到
`.build-tci/band10pro/qemu/hw/arm/`）加上 `case 0x1d`：
把值转给同一个 write 回调，然后回 `0x1e` Handle Value Confirmation。
重编后实测（gatt7 / smp1）：

```
bt-synthetic-last-bt-packet = 02 0020 0500 0100 0400 1e      <-- 确认已发出
```

对端语义现在是正确的了。

## 但下一道墙也量清楚了：这个状态下的手环「只推不理」

在链路确认已经修好的前提下，又试了两发主动请求（gatt8 / smp1，均 `--fresh-journal --phone-link`）：

| 注入 | 目的 | 手环反应 |
| --- | --- | --- |
| `02 0100 ffff 0028` Read By Type（Primary Service） | 让它吐出 GATT 服务表 | 无（`last-ap-packet` 仍是那条旧 indication） |
| SMP Pairing Request（CID 0x0006，IO=NoInputNoOutput）+ `bt-smp-just-works=on` | 建立加密 | 无 |

结论：未绑定态下这版固件把自己当**纯 peripheral**，只主动 indication，
不服务对端的 ATT 请求、也不接受对端发起的 SMP。
而模拟器的对端 GATT server（属性 1..9：generic access、device name、
`0xfe95` 服务、TX/RX 特征、CCCD）设计上是**给手环当客户端去连的**——
`miband_bt.c:1457` 那段也确实是「手环发 LE Create Connection 就自动回 Connection Complete」。

所以真正要触发的是**让手环主动连出去**（连已绑定手机 / beacon 回连那条路），
而不是我们从对端连进来。下一步该去 AP 里找它的 central 侧发起点
（`gattc_local_bind_login`、`miwear_bind_info_handler`、beacon 相关符号都在符号名表里），
或者反过来：先用 `/data` 按名存储伪造一个「已绑定手机」的记录，
让固件在开机时自己去回连，那时它才会以 client 身份去发现并写 RX 特征。

## 本轮净收益

* 又一个可复现的模拟器 bug 修好了（indication 无确认 → 链路死锁）。
* 虚拟手机链路从「能连上」推进到「ATT 语义双向正确」。
* 下一道墙定位明确：需要手环以 central 身份主动回连，方向从「注入」转成「伪造已绑定状态触发回连」。

# 🎯 模拟器抓到一个真 bug：我们的表盘数据绑定不工作，而原厂盘在同样条件下能拿到活数据

这是「用模拟器来调试」的第一个硬产出。用同一套 `setid + swap-keep` 回路，
把**原厂** `clean_362150007.face` 换进当前表盘槽位做对照（ctrl3）：

```
models/band10pro/userdata/ctrl3/b05.png
   10/29 THU          <-- 活的日期 + 星期
    04:39             <-- 活的时间（连拍里时间在走，帧哈希不同）
   👊 100%            <-- 活的电量
    👣 0              <-- 步数（模拟器计步器本来就没数据，0 是对的）
```

而我们的 `design.face` 在同一台模拟器、同一批数据源上（idmatch1）：
`0 : 0`、`0 月0 日`、`0°`、`0 步`，只有烘焙在背景里的静态文字是对的。

**关键排除项**（都逐字节比过了，不是这些原因）：

* **控件属性完全一致** —— 我们的
  `081102150000e803000000030000000000000000` 和原厂
  `clean_362150007.face` 里那条**一模一样**；
  `1011 / 0821 / 0841 / 1012 / 2012` 也都能在原厂盘里找到逐字节相同的样本。
* **元素表结构一致** —— 都是 `target=序号 | res_off | res_size=16` 的平铺形式。
* **字形数量够** —— 我们的 7 个 imagelist 里 5 个是 10 张（0-9），
  和原厂 `list[0..2] images=10` 同构。
* 10 Pro 上**根本没有 DigitalNum 控件**：17 张原厂盘 465 个控件全是 kind 7
  （ImageList），我们的 9 个控件也都是 kind 7 —— 之前 FORMAT.md 里按
  反编译常量表写的「DigitalNum」叫法在 10 Pro 上不成立，实际就是 ImageList。

所以差异一定在**栅格块本身的编码细节**（子图顺序 / list 块头 / 元素记录里的位置或标志），
而不是控件绑定表。下一步就是在模拟器里做二分：
把我们的某个 imagelist 换成 362150007 的对应 list，看那一位数字是否变活 ——
一次 boot 就能砍掉一半可能性。

## 顺带修正 FORMAT.md 的一处措辞

「DigitalNum 20 字节」这种说法来自 `XiaomiWatch.Common` 的反编译常量表，
在 10 Pro 的真实容器里对应的就是 kind 7 的 20 字节属性形式。
`0x09` 这个 kind 在 17 张原厂盘里出现 0 次。

# ✅✅ 根因找到并修好了：元素记录要用 0x07000000|控件，不是 0x03000000|列表

对照 ctrl3（原厂 362150007 在模拟器里是**活**的：`10/29 THU`、`04:39`、`100%`）
和 idmatch1（我们的盘同一台模拟器上全是 `0`），把两边的 16 字节元素数据记录
逐字节摊开比较，差异一眼可见：

```
原厂 clean_362150007 的元素数据记录首字：
   0x09000000  0x09000001  0x07000006  0x09000004  0x09000005
   ^^^^^^^^ kind=9/7 + 控件表下标，后面跟真实 x/y，res_off=res_size=0

我们的 design.face（修复前）：
   0x02000000..0x02000006   kind=2 图片
   0x03000000..0x03000006   kind=3 图像列表  <-- 9 个数据控件全用这个
```

也就是说**我们所有数据控件都按「静态资源」形式挂的**：
`0x03000000|list` 直接指向一块栅格，固件照原样画一张图就完事；
而 `0x07000000|widget` 才是指向控件表某一行，
固件会按那一行绑定的数据源**重绘**。
控件表我们其实生成了（9 行、属性和原厂逐字节相同），
但**没有任何一条元素记录引用它** —— 所以那些属性等于白写，
运行时永远画下标 0 的字形。

这就是 Chase 在真机上看到的「数据是假的/不更新」的根因。

## 修复与验证

`native/xwf_build.py` 加了元素约定开关，并且**默认就是控件形式**
（`XWF_WIDGET_ELEMENTS=0` 可回退到旧行为）。重新生成后：

```
design.face               element data kinds = {2: 7, 7: 9}     <-- 修复后
/tmp/design_before.face   element data kinds = {2: 7, 3: 9}     <-- 修复前
```

再走一遍 `setid → swap-keep → boot`，结果在
`models/band10pro/userdata/fix1/b04.png`：

```
04:47        <-- 活的时间（连拍两帧哈希不同，时间在走）
100%         <-- 活的电量
10月29日      <-- 活的日期
周四 / THU    <-- 活的星期
00000 步      <-- 活的步数（模拟器计步器无数据，画 0 是对的）
丙午年·九月十九 / 最高21°最低14° / 湿度43% 东风3级 / 日照11h03m / 站立9 —— 烘焙静态位，照旧
```

**从「全 0」到「时间/日期/星期/电量/步数全活」，只改了元素记录的类型字节。**
模拟器在这里完成了它被搭建起来的目的：不用手机、不用真机，
十几分钟一轮地把一个真机上的疑难 bug 定位并验证掉。

## 待办

真机复装验证这一版（`design.face` 大小不变 329,441 字节），
以及把 `native/FORMAT.md` 里「DigitalNum」的说法改成
「10 Pro 只有 kind 7 控件，元素记录用 0x07000000|控件下标 才会被数据驱动」。

# 回路固化成一条命令：`tools/face_render.py`

把「改 id → 原地换槽 → 过向导启动 → 保持亮屏 → 抓屏」五步收成一个脚本，
输入就是任意一个 `.face`：

```
cd vendor/miband-qemu
.venv/bin/python tools/face_render.py ../../miband10pro-face/native/design.face
```

实测输出（`userdata/face-render/b02.png`）：固件画出我们的 DataDash，
时间是活的 `06:03`，`100%`、`10月29日`、`周四/THU`、`丙午年·九月十九`、
天气卡、日出日落弧、`00000 步`、`站立 9/12` 全部到位。

几个设计点值得记：

* **默认换的是 `120917390547`（幻境流彩）而不是最大的槽位**。
  第一版自动挑了 22 MB 的最大槽位 `120917392537`，结果主屏照旧显示默认表盘
  （`face-render/b03.png` 里是 `05:58` 的大数字盘），白跑一轮。
  出厂 `/data` 重建后固件默认画的就是幻境流彩，所以要顶的是它。
  放不下时脚本会明确提示，并回退到最大可用槽位。
* 原厂 `vela_resource.bin` 只读不改，换槽镜像写到
  `firmware/vela_resource_render.bin`，跑完自动删（`--keep-image` 保留）。
* `--slot` 可以顶掉别的原厂表盘，配合 `face_debug_p67.py --post-tap`
  在表盘列表里选中它。
* 打包过程中踩到两个小坑，已修：`subprocess.run` 不能同时给
  `capture_output` 和 `stdout`；槽位表要用正则解析（face id 可能是空串，
  按 `split()` 取列会错位）。

## 宿主能不能替手机喂传感器数据？—— 试到底了，目前不能

给 `face_debug_p67.py` 加了 `--walk N`（按 ~1.6 Hz 步频摆 accel + gyro）和
`--bpm`（`sensor-pulse-bpm` / `sensor-pulse-amplitude`），想不连手机把
步数和心率这两个源推起来。probe5（`--fresh-journal --burst 5:20 --walk 30 --bpm 88`）
连拍 4 帧，`probe_diff.py` 的结果是：

```
2 of 36 cells changed:
   tile 6  1811  second   changed px per step [105, 160, 54]
   tile 7  1011  minute   changed px per step [66, 66, 38]
```

**只有秒和分在动。** 步数 `0821`、卡路里 `0823`、站立 `0824`、
心率 `1022/2021` 全都没有因为宿主灌进去的加速度波而变。
所以这台模拟器的计步/心率链路不吃 `sensor-accel-*` 这条注入路径
（大概是固件的计步器要 sensor HAL 的 FIFO 波形和持续时长，不是瞬时重力矢量；
也可能是这个状态下 IIO 侧根本没在采样）。

**这条负面结果对表盘工程有实际意义**：
凡是「必须手机/传感器供数」的源（天气、步数、卡路里、站立、心率），
**模拟器现在验证不了**，只能真机测。模拟器能验证的是
排版、栅格解码、时间/日期/星期/电量这一族，以及任何纯本机的源。
也就是说「虚拟链接手机」这件事不是锦上添花，而是这个回路目前真正的缺口。

## 顺带修掉两个自己写的坑（都是会让结论失真的那种）

1. `--walk` 的循环变量 `k` 把外层连拍的帧号 `k` **遮蔽**了，
   导致 5 帧全写进同一个 `b29.ppm`，看起来像「只跑了一帧」。
2. 亮屏边沿必须在抓屏**前一刻**制造。原来顺序是
   park→pokes(插充电器)→walk(18 秒)→dump，
   充电页只亮几秒，等 dump 时又熄了，probe3/probe4 因此整轮全黑，
   差点被误判成「灌传感器把系统搞挂了」。改成 walk→park→pokes→tap→dump 就正常了。

# 交接：当前状态与下一步（轮次暂停时用这份恢复）

## 一句话状态

**「用模拟器调试」已经成立并产出三个实质结果；「虚拟链接手机」到 ATT 层可复现，
完整手机会话未通。** 目标未标完成。

## 已验证、不要重新推导的事实

| 事项 | 结论 | 证据 |
| --- | --- | --- |
| Apple Silicon 构建 | TCI 解释器可用，JIT 会 SIGILL | `.build-tci/band10pro/build/qemu-system-arm` |
| 过向导 | `persist.setup_wizard` 在文件 `0x3fe7fe` 被消费；`uxtb r3,r3`(`dbb2`)→`movs r3,#1`(`0123`) 即一致地报「已绑定」 | `firmware/vela_ap_bound2.bin` |
| 渲染自定义表盘 | `/resource` romfs 原地换槽：`setid`（容器 `+0x28` 的 face id 必须等于目录名）+ `swap-keep`（不改任何记录字段，`+0` 是下一条记录的绝对偏移） | `userdata/face-render/b02.png` 活的 `06:03` |
| 一条命令回路 | `tools/face_render.py <face>` | 同上 |
| 数据不更新的根因 | 元素记录必须用 `0x07000000\|控件序`；`0x03000000\|图集序` 只画一次 | 修复前 `idmatch1/b02` 全 0，修复后 `fix1/b04` 全活 |
| 模拟器能喂的源 | 只有时/分/秒、月/日/星期、电量 | `native/PROBE_RESULTS.md` |
| 模拟器喂不动的 | 步数/卡路里/站立/心率/天气（灌 accel 1.6 Hz + pulse-bpm 无效） | `probe5` + `probe_diff.py` |
| 已修的模拟器 bug ×2 | ① face id 读 `+0x22` 应为 `+0x28`；② `miband_hci_gatt_acl()` 缺 `case 0x1d`，indication 收不到确认导致链路死锁 | `miband_nand_device.c` / `miband_hci_gatt.c` |
| 安装通道为何不通 | `miband_nand_set_mass_files()` 手写 littlefs，但 10 Pro 的 `/data` 是按名存储 | `flash.journal` 重放：页 102400 起无 `littlefs` 超级块，条目名有 `market`/`install_info_v1.db`/`watchface.db`/`watchface_list.json`/`persist.db`，`resource.bin` 出现 0 次 |

## 已排除、别再试的路径

1. 注入 LE Connection Complete 时 `Role=0x00`（central）—— 完全无应答。
2. 注入 LE Advertising Report 想触发主动回连 —— 无 `LE_Create_Connection`。
3. 宿主灌 `sensor-accel-*` / `sensor-pulse-bpm` 推步数心率 —— 一动不动。
4. `diag-skip-binding` / `diag-bypass-binding-auth` —— profile 是空占位，`exit(1)`。
5. 改 `persist.back_dial_id` 的 12 个引用点 —— 面太大，不如直接换槽。
6. macOS 走真射频当手机 —— `raw_hci_bridge.py` 只有 `LinuxHci`/`WindowsUsb`。
7. `system_reset` 后想继续抓屏 —— 复位后显示桥不再出图。

## 下一步（按性价比）

**A. 解 `/data` 按名存储的块链格式**（推荐，约 4–6 轮）
元数据页头 `u32 type | u32 0x0163 | u16 ffff | char name[32]`，`0x163` 像数据块索引；
正文不在同名页里。入口命令：

```
cd vendor/miband-qemu
# 用 device_tools.py:journal_nor() 的格式重放（必须放行 kind 1/2/3/4 全部记录）
python3 - <<'PY'   # 见 macsim-BUILD-macOS.md 里的现成片段
PY
```
做完能同时解决「装进去的表盘进 guest 列表」和「伪造已绑定记录触发回连」。

**B. 真做 host 侧 SMP + GATT client**（更贴近真手机，量级更大）
需要实现小米 PSK 绑定握手（ECDH-P256 + HMAC + CCM），或先用 `binding_auth`
的 `hardware.json` 声明式补丁免掉 AuthKey（字段表已抄在上面）。

## 需要用户侧动作的一件事

**真机复装 `native/design.face`（控件型修复版，329,441 B）**，确认时间/日期/星期/电量
在硬件上同样变活。若真机仍不活，说明 `/data` 安装路径与 `/resource` 路径的绑定语义
存在差异——那本身就是下一条关键线索。

---

# 2026-10-29 追加：虚拟手机「协议会话」已能从宿主完整重放

目标里「虚拟链接手机」这一半，本轮把**宿主侧能做的部分全部做完了**，并拿到了一条
新的关键证据：帧不是被模拟器丢掉，而是被**转发给 guest AP** 了。

## 新增能力：`--phone-session`

`vendor/miband-qemu/tools/face_debug_p67.py` 现在能把 Mi Fitness 装表盘的那套
BLE 帧完整重放一遍（纯 Python 实现，与固件侧 C 解码器逐字段对齐）：

```
cd vendor/miband-qemu
.venv/bin/python -u tools/face_debug_p67.py \
  --ap models/band10pro/firmware/vela_ap_bound2.bin \
  --phone-session ../../miband10pro-face/native/design.face \
  --poke sensor-charging=true --post-tap 168,240 --boot-wait 160 --settle 10 \
  --out models/band10pro/userdata/phonesession1
```

它做的事：LE Connection Complete → ATT Write Request(handle 0x0006) 承载
`L1(a5a5, type=3 DATA, seq, crc16-ARC)` → `L2(1,1)` + protobuf
`Request{1:4, 2:4, 6:WatchFaceInfo{1:id, 2:size}}` → 再把整个 `.face` 封成
MASS 包（`00 10 | md5 | u32 len | payload | crc32`）按 1024 B 切片，每片
`L2(2,1) + u16 total + u16 index` 逐帧注入。

## 实测结果（userdata/phonesession1/report.json）

- 322 个 MASS 分片全部被 `bt-synthetic-inject` 接受，**failed: 0**。
- 注入 prepare 后 `bt-synthetic-last-bt-packet` =
  `020000260022000400120600a5a5030117000e3e010108041004320f0a0931363732313030363510e18d14`
  —— 这正是我们发出去的那一帧（ATT 0x12 / handle 6 / L1 seq=1 / L2(1,1) / pb 原样）。
- 最后一个分片同样原样回显。

**这条回显是本轮最有价值的发现**：`bt-synthetic-last-bt-packet` 记的是
「控制器 → AP」方向，说明 QMP 注入的手机帧被**投递给 guest 固件**，
而不是由宿主侧 `miband_bt_gatt_write()` 消费。对照 `miband_bt.c:1696` 的注释
（"the guest firmware remains the sole Xiaomi protocol and installation consumer"），
这条路径是按设计走的。

因此 `install-catalog` 读回来是**空串**就不奇怪了：宿主模型没消费，
guest 消费与否在模拟器里看不到（`install-catalog` 只反映宿主侧 catalog）。
`--face`/`install-file` 走的是另一条（catalog + NAND overlay），两者互不相通。

## 于是「还差什么」被重新定义了

不是「手机帧进不去」，而是：

1. **guest 收到帧后要不要认**——它自己的 ATT server 已经在链路上主动
   indication（handle 0x000c，`01 00 ff ff`），说明链路层是活的；但 app 层
   要不要处理这条写，取决于固件的绑定/鉴权状态（AuthKey 那套）。
2. **宿主 catalog 与 guest 列表是两套账**——overlay 那半被
   `nand_load_page()` 的双重优先级挡死：先查 `nand->pages`（journal 回放页），
   再查 `erased_blocks`（擦除块直接返回全 0xff），最后才轮到 `overlay_pages`。
   guest 一旦自己写过 `/data`（journal 里就有这些页），overlay 永远看不见。
   这条是**静态读码得出的结论**，解释了「install-file 到不了 guest」。

## 下一步（更新版）

**A′. 让 overlay 抢在 journal 之前**：在 `miband_nand_restore_page()` 里让
`overlay_pages` 优先，或把 overlay 的写入点从「page 102400 起」改成 guest
`/data` 里已存在的 `market/<id>/resource.bin` 对象槽位。改动小、可当晚验证，
一旦成立，`--phone-session` 与 `--face` 两条路就都通了。

**B. 真做 host 侧 SMP + GATT client**（不变，量级更大）。

**C. 新选项：直接问 guest**。既然手机帧会投给 AP，可以用
`--machine-extra` 打开 `LOG_UNIMP`（`-d unimp`）再跑一次 `--phone-session`，
看 guest 侧有没有回 L1 ACK / pb 响应 —— 一次启动就能判定「差鉴权」还是「差消费」。

## 补测（同一晚，一次启动就出结论）：宿主模型根本没消费这些帧

`--qemu-extra "-d unimp -D /tmp/mi_unimp.log"` 重跑 `--phone-session`：
日志 290,789 行，**`miband BT Xiaomi …` 一条都没有**。

也就是说 `miband_bt_gatt_write()`（L1 解码 → pb prepare 响应 → MASS 接收 →
`install_catalog_commit`）整条宿主路径**没被触发**。结合上一节那条原样回显，
结论收敛为：

> QMP 注入的手机 ATT 帧只走「控制器 → AP」这一支，交给 **guest 固件**消费；
> 宿主侧那套 Xiaomi 协议实现只在别的后端（真 HCI bridge / passive 模式）下才跑。
> 所以 `install-catalog` 永远读不到 `--phone-session` 的成果，这不是 bug，
> 是两套互不相通的账。

于是「虚拟链接手机」剩下的唯一未知，被压缩成一个 guest 侧问题：
**固件 app 层收到这条写，是没鉴权丢弃，还是压根没接进 watchface 数据源**。
这两个假设在 QOM 属性上不可分辨，下一步只能上 `pmemsave`/gdb 看固件的
数据缓冲（`--memdump ADDR:SIZE` 通道已经打通），或者按 A′ 让 overlay 抢在
journal 之前，先走通「宿主写 → guest 读」这条更短的路。
