<#
.SYNOPSIS
    编译 + 烧录 ESP32 固件，并自动等待你手动进入下载模式。
    专治"这块板没有自动下载电路，必须按 BOOT"的问题。

.DESCRIPTION
    本脚本会：找到 arduino-cli 与 esptool -> 自动识别串口 -> 编译 -> 打印提示 ->
    持续重试连接（默认 300 次）。你只要在它运行期间做一次：
        按住 BOOT 不放 -> 按一下 EN/RST -> 松开 BOOT
    它就会自动连上并完成烧录，不需要你掐时间点"上传"。

.PARAMETER Sketch
    草图目录（必填），例如 .\bh1750_test

.PARAMETER DryRun
    只做预检 + 编译，不烧录（用来确认环境没问题）

.EXAMPLE
    .\flash_manual.ps1 -Sketch .\esp32_blink_test
    .\flash_manual.ps1 -Sketch .\light_pwm_test -ExtraFlags "-DMODE=1"
    .\flash_manual.ps1 -Sketch .\bh1750_test -DryRun

.NOTES
    实测环境：ESP32-D0WD-V3 / 4MB / COM5 / esp32:esp32@3.3.12 / esptool 5.3.1
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Sketch,
    [string]$Port = '',
    [string]$Board = 'esp32:esp32:esp32',
    [int]$Baud = 460800,
    [int]$ConnectAttempts = 300,
    [string]$ExtraFlags = '',
    [switch]$DryRun
)

$ErrorActionPreference = 'Continue'

# 保险：有些电脑的系统 PATH 缺少 C:\Windows\System32，会让 arduino-cli 报
# exec: "cmd": executable file not found in %PATH%。这里自己补齐，不依赖系统设置。
if (-not (Get-Command cmd.exe -ErrorAction SilentlyContinue)) {
    $env:PATH = "C:\Windows\System32;C:\Windows;$env:PATH"
}

function Say($msg, $color = 'Gray') { Write-Host $msg -ForegroundColor $color }

# ---------- 0. 参数检查 ----------
$sketchDir = (Resolve-Path -LiteralPath $Sketch -ErrorAction SilentlyContinue)
if (-not $sketchDir) { Say "找不到草图目录：$Sketch" Red; exit 1 }
$sketchDir = $sketchDir.Path
$inoName = (Get-Item $sketchDir).Name + '.ino'
if (-not (Test-Path (Join-Path $sketchDir $inoName))) {
    Say "目录里没有 $inoName（Arduino 要求 .ino 文件名与文件夹同名）" Red; exit 1
}
Say "草图：$sketchDir" Cyan

# ---------- 1. 找 arduino-cli ----------
$cli = "D:\arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"
if (-not (Test-Path $cli)) {
    $cmd = Get-Command arduino-cli -ErrorAction SilentlyContinue
    if ($cmd) { $cli = $cmd.Source } else { Say "找不到 arduino-cli（未安装 Arduino IDE 2.x？）" Red; exit 1 }
}
$cfg = "$env:USERPROFILE\.arduinoIDE\arduino-cli.yaml"
$cfgArgs = @()
if (Test-Path $cfg) { $cfgArgs = @('--config-file', $cfg) }
Say "cli ：$cli"

# ---------- 2. 找 esptool（优先用开发板支持包自带的） ----------
$esptool = $null
$cand = Get-ChildItem "$env:LOCALAPPDATA\Arduino15\packages\esp32\tools\esptool_py" `
        -Recurse -Filter 'esptool.exe' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($cand) { $esptool = $cand.FullName }
if (-not $esptool) {
    $c = Get-Command esptool -ErrorAction SilentlyContinue
    if ($c) { $esptool = $c.Source }
}
if (-not $esptool) { Say "找不到 esptool.exe（请先在开发板管理器安装 esp32 支持包）" Red; exit 1 }
Say "esptool：$esptool"

# ---------- 3. 自动识别串口 ----------
if (-not $Port) {
    $json = & $cli @cfgArgs board list --format json 2>$null | Out-String
    try {
        $detected = ($json | ConvertFrom-Json).detected_ports
        $serial = $detected | Where-Object {
            $_.port.protocol -eq 'serial' -and $_.port.address -like 'COM*' -and
            $_.port.label -notmatch '蓝牙|Bluetooth'
        }
        if ($serial) { $Port = $serial[0].port.address }
    } catch { }
}
if (-not $Port) {
    Say "没有识别到串口设备。请确认：USB 数据线已插好（不是只能充电的线）、驱动已装。" Red
    & $cli @cfgArgs board list
    exit 1
}
Say "串口：$Port" Cyan

# ---------- 4. 编译 ----------
Say "`n== 编译 ($Board) ==" Cyan
$compileArgs = @($cfgArgs + @('compile', '--fqbn', $Board, '--json'))
# 用 compiler.cpp.extra_flags 追加宏定义：这样不会覆盖平台自带的 build.extra_flags
if ($ExtraFlags) { $compileArgs += @('--build-property', "compiler.cpp.extra_flags=$ExtraFlags") }
$compileArgs += $sketchDir

$raw = & $cli @compileArgs 2>&1 | Out-String
$code = $LASTEXITCODE
if ($code -ne 0) {
    Say "编译失败：" Red
    Say $raw
    Say "提示：若报 exec: `"cmd`": executable file not found in %PATH%，说明系统 PATH 缺少 C:\Windows\System32" Yellow
    exit 1
}
$buildPath = $null
try { $buildPath = ($raw | ConvertFrom-Json).builder_result.build_path } catch { }
if (-not $buildPath -or -not (Test-Path $buildPath)) { Say "编译成功但没拿到 build_path，无法继续" Red; exit 1 }
Say "编译成功，产物目录：$buildPath" Green

# ---------- 5. 组装烧录参数（用 Arduino 生成的 flash_args，避免手写偏移量出错） ----------
$flashArgsFile = Join-Path $buildPath 'flash_args'
if (-not (Test-Path $flashArgsFile)) { Say "缺少 flash_args，无法确定烧录偏移" Red; exit 1 }

$tokens = @()
foreach ($line in (Get-Content $flashArgsFile)) {
    $line = $line.Trim()
    if ($line -eq '' -or $line.StartsWith('--')) { continue }   # 跳过 --flash-mode 等参数行
    $parts = $line -split '\s+'
    if ($parts.Count -ge 2) { $tokens += $parts[0]; $tokens += (Join-Path $buildPath $parts[1]) }
}
Say ("待烧录 {0} 个镜像：{1}" -f ($tokens.Count / 2), (($tokens | Where-Object { $_ -notlike '0x*' } | ForEach-Object { Split-Path $_ -Leaf }) -join ', '))

if ($DryRun) { Say "`n[DryRun] 预检全部通过，未执行烧录。" Green; exit 0 }

# ---------- 6. 等下载模式并烧录 ----------
Say "`n== 现在请操作硬件 ==" Yellow
Say "   1) 按住 BOOT 键不要松" Yellow
Say "   2) 按一下 EN / RST 键（有的板子写 RST）" Yellow
Say "   3) 松开 BOOT 键" Yellow
Say "   脚本已经在等你了，连上就自动开始烧录（最长等 $ConnectAttempts 次尝试）`n" Yellow

$espArgs = @(
    '--chip', 'esp32', '--port', $Port, '--baud', $Baud,
    '--before', 'no-reset', '--after', 'hard-reset',
    '--connect-attempts', $ConnectAttempts,
    'write-flash', '-z', '--flash-mode', 'dio', '--flash-freq', '80m', '--flash-size', '4MB'
) + $tokens

& $esptool @espArgs
$rc = $LASTEXITCODE

if ($rc -eq 0) {
    Say "`n烧录成功。串口监视器请用 115200 打开 $Port 查看输出。" Green
} else {
    Say "`n烧录失败（退出码 $rc）。排查顺序：" Red
    Say "   1) BOOT 按键时序：必须先按住 BOOT，再按 EN，最后松 BOOT" Red
    Say "   2) 换一根确定能传数据的 USB 线 / 换一个 USB 口（避开前面板与扩展坞）" Red
    Say "   3) 关掉 Arduino IDE 的串口监视器（串口被占用时无法烧录）" Red
    Say "   4) 确认 -Port 选对了（本机为 COM5，CP210x）" Red
}
exit $rc
