# 小米手环 10 Pro · 原生 macOS 打包核心

不依赖 Wine/CrossOver/EasyFace GUI。纯 Python + Pillow 直接生成 `.face`。

## 已验证

- 底图-only：与官方 EasyFace Compiler 4.23 输出 **SHA-256 完全一致**
- 底图 + DigitalNum（小时）：逐字节一致
- 底图 + ImageList（星期）：逐字节一致
- `design.face`：底图 + 7 个额外小图 + 9 个控件（时/分/步数/电量/天气温度/月/日/星期中英）

## 文件

- `xwf_build.py`：通用打包器（image 元素 + DigitalNum 控件）
- `xwf_face.py`：按当前设计生成 `design.face` 和 `design_mock.png`
- `design.face`：可直接安装的表盘文件
- `design_mock.png`：合成预览（示例值 15:54 / 8432 / 76%）
- `header_template.bin`：256 字节头模板（从官方输出提取）
- `assets/`：底图/预览

## 用法

```bash
pip3 install pillow
python3 xwf_face.py          # 生成 design.face + design_mock.png
```

通用打包：

```bash
python3 xwf_build.py \
  --background assets/background-date.png \
  --preview assets/preview.png \
  --digits /path/to/d \
  --out out.face --title DataDash
```

`--digits` 是前缀，需有 `d0.png`…`d9.png` 十张同尺寸数字图。

## 格式摘要（10 Pro，336×480）

- 全局头 168 字节：magic `5A A5 34 12`，版本 2048，face 数，预览偏移，ID，标题@104
- 每面 88 字节：元素/图片/图片列表/控件表计数与偏移
- 元素表 16 字节/项，元素数据 16 字节：type@+3（2=image，7=DigitalNum），X@+4，Y@+6
- 图片表 16 字节/项：type 2，偏移，块长
- 图片列表表 16 字节/项：type 3，偏移，块长
- 控件表 16 字节/项：type 7，属性偏移，属性长度（DigitalNum=20）
- DigitalNum 属性：数据源大端@+0，位数@+2，flags@+3，ImageId@+8，字距@+13
- 图片块：4 字节空头 + W/H + 长度 + **BGRA 原始像素**（10 Pro 不压缩）
- 图片列表块：4 字节头（字节 1=张数）+ W/H + 长度 + 连续 BGRA
- 预览块同图片块，偏移写在全局头 +32 和面头 +4
- 数据顺序：元素数据 → 控件属性 → 图片块 → 图片列表块 → 预览

## 模拟器调试回路（不用手机，一条命令）

任何 `.face` 都能直接让**原厂固件**画出来看：

```
cd ../vendor/miband-qemu
.venv/bin/python tools/face_render.py ../../miband10pro-face/native/design.face
# 截图落在 vendor/miband-qemu/userdata/face-render/b0*.png
```

原理与前置条件见 `../macsim-BUILD-macOS.md`。要点：脚本会把容器里的 face id
改成它要顶替的原厂槽位名（固件会交叉核对，不一致就静默回退到默认表盘），
然后**原地覆盖 `/resource` romfs 的槽位、不改任何记录字段**，再用打过
2 字节补丁的 AP（`vela_ap_bound2.bin`，等价于 `persist.setup_wizard=1`）
跳过绑定向导，并用「插充电器的边沿」把熄屏的面板叫醒后抓屏。
原厂镜像只读，临时镜像跑完自动删。

配套工具：

- `tools/romfs_face_swap.py` — `list` / `setid` / `swap-keep` / `swap`
- `tools/face_debug_p67.py` — 启动 + 触摸/手势 + 连拍 + `--poke` 传感器 +
  `--phone-link`（注入 LE 连接完成）+ `--phone-session <face>`（把 Mi Fitness
  装表盘的整条 BLE 帧序在宿主侧重放：pb prepare + 322 片 MASS）
- `tools/probe_diff.py` — 把 `probe.face` 的截图变成「哪些数据源在变」的表

## 模拟器能验证什么、不能验证什么

**能**：排版、栅格解码（索引色 + 字节 RLE）、容器结构、控件绑定是否真的被驱动、
时间/日期/星期/电量这些本机数据源。

**不能**：天气、步数、卡路里、站立、心率。宿主往 `sensor-accel-*` /
`sensor-pulse-bpm` 灌 1.6 Hz 步频波和心率，probe 上这些源**一动不动**
（实测见 `PROBE_RESULTS.md`）。协议侧的进展是：手机帧现在能**完整注入并被接受**
（`--phone-session`，322/322 片零失败），且这些帧是按设计**投递给 guest 固件**
消费，不是宿主 catalog；guest 收帧后认不认（差鉴权还是差消费）尚未判定，
下一步用 `-d unimp` 跑一轮即可分辨。在那之前，天气族/运动族仍须真机带手机测。

## 当前状态

- 控件全部走 **kind 7 ImageList + `0x07000000|控件序` 元素记录**——这是
  10 Pro 上唯一会被数据源驱动的形式；用 `0x03000000|图集序` 会被当静态图
  画一次就再也不更新（真机「数据不更新」的根因，已在 `xwf_build.py` 修好，
  并在模拟器里验证：时间/日期/星期/电量全活）。
- 已动态：时、分、秒、月、日、星期（中英）、电量、天气温度。
- 仍静态：年、第几天、农历、日出日落、最高/最低、湿度/风、站立、天气图标。
- 数据源实测读数与判读见 `PROBE_RESULTS.md`。

## 下一步

1. **真机复装这一版 `design.face`**，确认控件型修复在硬件上同样生效
   （模拟器已经证明时间/日期/星期/电量活了）。
2. 真机带手机跑一次 `probe.face`，把天气族
   （`7831 b031 b831 c031 c831 d031 3031 5031 2031`）和
   步数/站立/心率族（`0821 0822 0823 0824 0825 0826 0828 0829 1021 1022`）钉死。
3. 天气图标（`3031` 作 ImageList）与站立（`0824`）接上。
4. Mac 蓝牙直装（逆向 SPP/BLE 协议）——与「虚拟链接手机」缺的是同一套协议栈。
