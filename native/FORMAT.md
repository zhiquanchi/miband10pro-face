# Band 10 Pro `.face` 容器格式（实测逆向结论）

依据：`DDlhy/Miband-qemu-simulator` 仓库自带的 10 Pro `3.101.043` 参考固件
`vela_resource.bin`，其中内嵌 17 个原厂表盘。全部 760 个栅格块经
`python3 native/xwf_rle.py` 解码→重编码→再解码，逐字节还原。

## 顶层

| 偏移 | 长度 | 内容 |
| --- | --- | --- |
| 0 | 168 | 全局头，magic `5A A5 34 12`；`[6]=1`、`[22]=1`（原厂表盘必置）；`[40:56]` ASCII 表盘 ID；`[104:162]` UTF-8 标题 |
| 168 | 88 | 表盘条目：`[0]=0x80000000` 标志；`[4]` 数据区末尾（预览偏移）；随后 10 组 `(count, offset)`：元素表 / 图片表 / 图集表 / 3 张空表 / 控件表 / 按钮表 / 附加表 |
| 256… | | 各 16 字节表行 `(id, 0, offset, size)` |

## 栅格块：8 位索引色 + 字节 RLE

10 Pro **不用** EasyFace 4.23 的 `PassCompressor` 裸 BGRA（那是 `GetCompressor`
缺 567 分支的产物，装机会被判 `表盘格式错误 -10001`）。原厂块一律：

```
image : 10 04 00 00 | u16 w | u16 h | u32 len | stream
list  : 10  n  04 00 | u16 w | u16 h | u32 len | u32 sub_len * n | sub * n
stream: E0 21 A5 5A | u32 ((w*h + 1024) << 4) | 1 | byteRLE(palette + index)
```

* `palette` = 256 项 **BGRA**，共 1024 字节；`index` = `w*h` 字节。
* `byteRLE`：`flag < 0x80` → 把后一个字节重复 `flag` 次；
  `flag >= 0x80` → 原样拷贝 `flag & 0x7F` 字节（`0x80` 视作 1）。
* `list` 的 `len` 只统计子块，不含 `n × u32` 尺寸表。

## 元素记录（16 字节）

`u32 target | u16 x | u16 y | u32 res_off | u32 res_size`

两种写法都存在，但**只有控件型会被数据驱动**（在 QEMU 里用原厂
`362150007` 作对照实测出来的，见 `../macsim-BUILD-macOS.md`）：

1. **资源型**（如 `120917385439` 流沙的静态块）：`target = 0x02000000|图序` 或
   `0x03000000|图集序`，`res_off/size` 指向该栅格块，`x=y=0`，位置烘进图块本身。
   固件照原样画一张图，**不会再更新它**。
2. **控件型**（如 `362150007` 的时钟）：`target = 0x07000000|控件序`，
   `x/y` 即坐标，`res_off = res_size = 0`。固件按控件表那一行绑定的数据源重绘。

**任何绑定了数据源的控件都必须用控件型记录引用**，否则控件表写了也没人读，
运行时永远画下标 0 的字形 —— 表现就是「排版全对但数据不更新」。
`xwf_build.py` 默认输出控件型（`XWF_WIDGET_ELEMENTS=0` 可退回旧行为）。

注意 10 Pro 的 17 张原厂表盘里 **kind 0x09 只出现在元素记录的类型字节上**，
控件表本身 465 个条目全是 kind 7；反编译常量表里的「DigitalNum」
在 10 Pro 上就是 20 字节属性的 kind 7 控件。

## 控件属性

DigitalNum（20 字节）与 ImageList（16 或 `16+4N` 字节）已与原厂逐字节一致：

```
DigitalNum: u16 source(BE) | u8 digits | u8 0x15 | u32 1000 | u32 0x03000000|图集序 | u8 pad | u8 spacing
ImageList : u16 source(BE) | u8 0 | u8 0x20 | u32 0 | u32 0x03000000|图集序 [| u32 512 | u32 value * N]
```

`source` 高位在前：时 `0811`、分 `1011`、月 `1012`、日 `1812`、星期 `2012`、
步数 `0821`、电量 `0841`、气温 `2031`、天气类型 `3031`。

## 数据源 ID（十进制速查）

`WatchType.MiBand10Pro = 567`，分辨率 `336×480`（另有 `230×328` 息屏档）。
