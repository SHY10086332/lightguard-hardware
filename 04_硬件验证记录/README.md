# lightguard-hardware-demo

「光稳智检」项目的**独立硬件调试库**。

> **边界声明**：本目录只做硬件验证，**不修改、不引用、不覆盖**任何已提交的比赛系统代码
> （软件提交包 `aic AI+软件/软件项目源代码/01_可运行系统` 保持原样）。
> 硬件全部跑通后，再单独按串口协议接入主系统。

---

## 一、这个库要解决什么问题

把"硬件能不能用"和"软件/模型对不对"彻底分开。硬件问题只在**本目录 + 一块板子 + 一根 USB 线**的范围内排查，
避免同时排查硬件、模型、云服务三类问题。

## 二、目录结构

> **注意：本节写的是"原始调试库"布局。放进本次提交包后，文件位置按下表对应：**

| 原始调试库 | 本提交包中的位置 |
|---|---|
| `firmware/esp32_blink_test/` | `01_ESP32固件/esp32_blink_test/` |
| `firmware/bh1750_test/` | `01_ESP32固件/bh1750_test/` |
| `firmware/light_pwm_test/` | `01_ESP32固件/light_pwm_test/` |
| `firmware/indicator_test/` | `01_ESP32固件/indicator_test/` |
| `firmware/mos_polarity_probe/` | `01_ESP32固件/mos_polarity_probe/` |
| `wiring/接线速查卡_一页版.md` | `02_硬件安装与接线/接线速查卡_一页版.md` |
| `wiring/接线图.md` | `02_硬件安装与接线/接线图.md` |
| `wiring/引脚表.md` | `02_硬件安装与接线/引脚表.md` |
| `README.md`（本文件） | `04_硬件验证记录/README.md` |
| `hardware-test-log.md` | `04_硬件验证记录/hardware-test-log.md` |
| `测试固件说明_硬件验证.md` | `01_ESP32固件/测试固件说明_硬件验证.md` |
| `flash_manual.ps1` | `01_ESP32固件/flash_manual.ps1` |

```
lightguard-hardware-demo
├── firmware
│   ├── esp32_blink_test      # 第一步：只验证板子本身（已通过 ✅）
│   ├── bh1750_test           # 第二步：验证 BH1750 读照度
│   └── light_pwm_test        # 第三步：验证 PWM 调光（可切 BH1750 闭环）
├── wiring
│   ├── 接线图.md              # 每个器件的接线图 + 上电检查清单
│   └── 引脚表.md              # 引脚分配 + 禁用引脚 + 本机环境
├── README.md
└── hardware-test-log.md      # 逐项测试记录（含实测数据）
```

## 三、已确定的硬件参数（实测，不是抄文档）

| 项 | 值 | 来源 |
|---|---|---|
| 芯片 | ESP32-D0WD-V3 rev v3.1，双核 + LP 核，240MHz | esptool `flash-id` |
| Flash | **4MB**（manufacturer 0x5e, device 0x4016） | esptool `flash-id` |
| 晶振 | 40MHz | esptool |
| MAC | `D4:B1:97:A9:9B:20` | 固件 `ESP.getEfuseMac()` |
| 串口芯片 | CP2102（Silicon Labs CP210x，COM5） | 设备管理器 |
| SDK | v5.5.5（ESP-IDF） | 固件运行时打印 |
| 下载模式 | **无自动下载电路**：esptool 的 DTR 无法拉低 GPIO0 | 实测：`default_reset` 报 `Wrong boot mode (0x13)` |

## 四、测试顺序（一步一验证，不要跳）

| 顺序 | 固件 | 接什么 | 通过标准 |
|---|---|---|---|
| 1 | `esp32_blink_test` | 只接 USB | 板载 LED 每 0.5s 闪；串口打印芯片信息 |
| 2 | `bh1750_test` | + BH1750 | 串口持续输出 `LUX:xxx`，遮光变小、照光变大 |
| 3 | `light_pwm_test`（MODE 0） | + MOSFET + 灯带 | 上电锁定 0%（安全启动）；发送 `B:` 或 `A` 后按 0→25→50→75→100% 循环，无闪烁 |
| 4 | `light_pwm_test`（MODE 1） | 2+3 一起 | 串口同时输出 `LUX:` 与 `BRIGHTNESS:`，照度稳定在目标值附近 |
| 5 | 红绿指示 | 共阴 RGB 模块 + 有源蜂鸣器（`indicator_test`） | 绿灯=正常 / 红灯=异常 / 异常短鸣；该固件不做板载 LED 替代验证 |

## 五、怎么编译和烧录

### 方式 A：Arduino IDE（图形界面）

1. 打开 `D:\arduino IDE\Arduino IDE.exe`
2. 工具 → 开发板 → esp32 → **ESP32 Dev Module**
3. 工具 → 端口 → **COM5**
4. 打开对应 `.ino`，点"上传"
5. **出现 `Connecting......` 时按住 BOOT**，看到 `Writing at 0x00001000...` 再松开

### 方式 B：命令行（快速重复测试）

```powershell
# 命令行工具（IDE 自带的 arduino-cli）
# 路径按本机安装位置调整
$cli = "<Arduino IDE 安装目录>\resources\app\lib\backend\resources\arduino-cli.exe"
$cfg = "$env:USERPROFILE\.arduinoIDE\arduino-cli.yaml"
$fw  = "<材料包>\aic AI+硬件\01_ESP32固件"        # 本包内的固件目录

# 编译
& $cli --config-file $cfg compile --fqbn esp32:esp32:esp32 "$fw\bh1750_test"

# 烧录（先手动进下载模式：按住 BOOT → 按一下 EN → 松开 BOOT）
# ★ 必须加 --before no-reset：本板没有自动下载电路，用默认的 default-reset
#   会报 Wrong boot mode detected (0x13)（见 hardware-test-log.md §2.2）。
& $cli --config-file $cfg upload -p COM5 --before no-reset --fqbn esp32:esp32:esp32 "$fw\bh1750_test"
```

**推荐的免手速方案**（脚本会一直等，你按完 BOOT 就自动烧，不用掐时间）：
```powershell
cd "<材料包>\aic AI+硬件\01_ESP32固件"
powershell -ExecutionPolicy Bypass -File .\flash_manual.ps1 -Sketch .\bh1750_test
# 闭环模式： -ExtraFlags "-DMODE=1"      只预检不烧录： -DryRun
```

### ★ 本板烧录必读：为什么一定要按 BOOT

ESP32 上电瞬间采样 GPIO0：**高电平 = 运行 Flash 里的程序；低电平 = 进下载模式**。
正规 DevKit 用两个三极管自动完成这件事，**这块板没做**，所以：

- 只按一次 BOOT 不够——必须让"复位瞬间"和"BOOT 按住"同时发生；
- 最稳的手势：**按住 BOOT 别松 → 点上传 → 看到 `Writing at 0x...` 再松开**；
- 命令行方式：按住 BOOT → 按一下 EN/RST → 松开 BOOT，然后**用 `--before no-reset` 烧**。

## 六、串口协议（★ 两套，别搞混）

**正式固件 `lightguard_esp32.ino` 用的是 JSON 协议**，网页就是按这套对接的（不要改）：

| 方向 | 报文 | 含义 |
|---|---|---|
| PC → ESP32 | `PING` | 探测设备 → `{"ok":true,"device":"LIGHTGUARD_ESP32"}` |
| PC → ESP32 | `STATUS` / `LUX?` | 查询状态 |
| PC → ESP32 | `LIGHT 72` | 设定亮度 0~100 |
| PC → ESP32 | `RESULT NORMAL` / `RESULT ABNORMAL` / `RESULT IDLE` | 绿灯 / 红灯+短鸣 / 全部熄灭 |
| ESP32 → PC | `{"ok":true,"lux":450.2,"brightness":72,"result":"idle"}` | 每条命令后回一行 JSON |

**本验证库的验证固件用的是简化文本协议**（`indicator_test` 例外，用 JSON 但只实现 PING/T/RESULT）（只为串口监视器里肉眼调试方便，不是给网页用的）：
输出 `LUX:450.2`、`BRIGHTNESS:72`；输入 `B:72`、`T:500`、`A`。

> 所以：用 `bh1750_test` 验证时串口看到的是 `LUX:xxx`；烧回正式固件、网页连接后看到的是 JSON。
> **两者不通用**——不要用网页去连验证固件，也不要用文本命令去控正式固件。

设计原则：**照度稳定后再拍照**。串口异常时上位机返回明确错误（HTTP 4xx/5xx 与 JSON 说明），**不会自动降级为模拟演示**；转口断开场景尚未实测（见任务清单 T-406）。

## 七、环境注意事项（本机踩过的坑）

1. **系统 PATH 曾经缺 `C:\Windows\System32`**，导致 ESP32 编译报
   `exec: "cmd": executable file not found in %PATH%`。已于 2026-10-04 修复
   （修改前已把 HKLM 的 Path 原值备份留档，可随时回滚）。**改完必须重启 Arduino IDE 才生效**。
2. GitHub 直连极慢（约 1.3 KB/s），装开发板/库时请开代理（clash 混合端口 127.0.0.1:7897）。
3. 本仓库里的验证固件都**不依赖任何第三方库**（BH1750 驱动是自带的），克隆下来即可编译。
