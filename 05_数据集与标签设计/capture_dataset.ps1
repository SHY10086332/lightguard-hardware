# capture_dataset.ps1 —— 数据集自动采集（固定光照 + 等稳定 + 抓帧 + 记录）
# ------------------------------------------------------------------
# 为什么要用脚本：数据集必须"每张图的光照条件一致"。手拍容易拍着拍着亮度变了、
# 或者忘记录照度。这个脚本把流程固定死：
#     设定固定亮度 → 等 BH1750 读数稳定 → 抓帧 → 自动命名 → 把照度写进 CSV
#
# 用法示例：
#   powershell -ExecutionPolicy Bypass -File .\capture_dataset.ps1 -Class normal -Count 20
#   powershell -ExecutionPolicy Bypass -File .\capture_dataset.ps1 -Class brokenline -Count 15 -Brightness 25
#   powershell -ExecutionPolicy Bypass -File .\capture_dataset.ps1 -DryRun          # 自检，不采集
#   powershell -ExecutionPolicy Bypass -File .\capture_dataset.ps1 -Class normal -NoEsp -DryRun
#                                                                                  # 不接 ESP32 只测摄像头通路
#
# 采集流程（每张）：控制台提示"放入第 N 个样品" → 你放好标签 → 按回车 → 自动抓帧并记录
param(
    [string]$Class       = '',
    [int]   $Count       = 10,
    [int]   $Brightness  = 25,          # 固定亮度百分比（按实测增益 28.5 lx/% 换算：25% ≈ 760 lx，含约 46 lx 环境光底数；该换算只对 28.5 lx/% 那一 BH1750 摆放位置成立）
    [int]   $Shots       = 1,           # 每个样品拍几张
    [string]$Port        = 'COM5',
    [string]$Camera      = 'USB Camera',
    [string]$OutRoot     = '',
    [switch]$NoEsp,                     # 不连 ESP32（亮度需手动设定/或用于测通路）
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# 保险：某些电脑系统 PATH 缺 System32
if (-not (Get-Command cmd.exe -ErrorAction SilentlyContinue)) {
    $env:PATH = "C:\Windows\System32;C:\Windows;$env:PATH"
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $OutRoot) { $OutRoot = Join-Path $root 'images' }
$csvPath = Join-Path $root '采集记录表.csv'
$ffmpeg  = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
if (-not $ffmpeg) { Write-Host "找不到 ffmpeg，请先安装（winget install Gyan.FFmpeg）" -ForegroundColor Red; exit 1 }

function Say($m, $c = 'Gray') { Write-Host $m -ForegroundColor $c }

$useEsp = -not $NoEsp
$sp = $null

# ---------- 1. 连接 ESP32 并设定固定亮度 ----------
if ($useEsp) {
    Say "== 连接 ESP32（$Port）==" Cyan
    $sp = New-Object System.IO.Ports.SerialPort $Port, 115200, 'None', 8, 'One'
    $sp.ReadTimeout = 400
    try {
        $sp.Open()
    } catch {
        Say ("打不开串口 {0}：{1}" -f $Port, $_.Exception.Message) Red
        Say "  → 检查：ESP32 的 USB 是否插好？COM 口号是否是 $Port ？" Yellow
        Say "  → 想跳过 ESP32 只测摄像头通路：加 -NoEsp" Yellow
        exit 1
    }
    $sp.DtrEnable = $false; $sp.RtsEnable = $false
    Start-Sleep -Milliseconds 2500
    $sp.DiscardInBuffer()
}

function SendCmd($c, $sec) {
    if (-not $sp -or -not $sp.IsOpen) { return '' }
    $sp.DiscardInBuffer()
    if ($c) { $sp.Write($c + "`r`n") }
    $end = (Get-Date).AddSeconds($sec); $acc = ''
    while ((Get-Date) -lt $end) { try { $acc += $sp.ReadExisting() } catch {}; Start-Sleep -Milliseconds 100 }
    return $acc
}
function GetLux {
    $r = SendCmd "STATUS" 1.2
    if ($r -match '"lux":([0-9.]+)') { return [double]$Matches[1] } else { return -1 }
}
function LightsOff {
    if ($sp -and $sp.IsOpen) {
        try { $sp.Write("LIGHT 0`r`n"); Start-Sleep -Milliseconds 500 } catch {}
        $sp.Close(); $sp.Dispose()
        Say "灯带已关闭（0%）"
    }
}

$lux = -1
if ($useEsp) {
    $sp.Write("LIGHT $Brightness`r`n"); Start-Sleep -Milliseconds 800
    Say ("已设定固定亮度：{0}%" -f $Brightness) Green

    # 等照度稳定（连续 2 次读数差异 < 2%）
    $lux = GetLux
    $stable = $false
    for ($i = 0; $i -lt 12; $i++) {
        Start-Sleep -Milliseconds 400
        $l2 = GetLux
        if ($lux -gt 0 -and [math]::Abs($l2 - $lux) / $lux -lt 0.02) { $stable = $true; $lux = $l2; break }
        $lux = $l2
    }
    Say ("当前照度：{0} lx  ({1})" -f $lux, $(if ($stable) { '已稳定' } else { '未完全稳定' })) `
        $(if ($lux -ge 150 -and $lux -le 900) { 'Green' } else { 'Yellow' })
    if ($lux -lt 150) { Say "照度偏低：画面可能偏暗，建议提高 -Brightness" Yellow }
    if ($lux -gt 900) { Say "照度偏高：画面可能过曝（实测 100% 全白），建议降低 -Brightness" Yellow }
} else {
    Say "[NoEsp] 跳过 ESP32：请自行确保灯带亮度已固定（不要使用自动闭环）" Yellow
}

# ---------- 2. 自检摄像头 ----------
# ffmpeg 把设备列表写在 stderr，而 PS 5.1 会把原生命令的 stderr 渲染成带噪音的
# "错误记录"文本（含 CategoryInfo 等）。所以这里不解析文本，直接用正则把
# “"设备名" (video)” 里的设备名全部抠出来，彻底绕开噪音。
$devFile = Join-Path $env:TEMP 'dsh_cam_devs.txt'
$oldEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $ffmpeg -hide_banner -list_devices true -f dshow -i dummy 2> $devFile | Out-Null
$ErrorActionPreference = $oldEap
$devs = if (Test-Path $devFile) { (Get-Content $devFile -Raw) } else { '' }
$camNames = @([regex]::Matches($devs, '"([^"]+)"\s*\(video\)') | ForEach-Object { $_.Groups[1].Value })
if ($camNames -notcontains $Camera) {
    Say "找不到摄像头 '$Camera'，当前可用的视频设备：" Red
    if ($camNames.Count) { $camNames | ForEach-Object { Say "   - $_" } } else { Say "   （一个都没有，检查摄像头 USB 是否插好）" }
    Say "  → 用 -Camera 「设备名」 指定；或插好摄像头后重试。" Yellow
    LightsOff; exit 1
}
Say "摄像头就绪：$Camera" Green

if ($DryRun) {
    Say "`n[DryRun] 自检通过，未采集。" Green
    LightsOff; exit 0
}

if (-not $Class) { Say "必须用 -Class 指定类别（normal / brokenline / faint / stain / scratch / offset）" Red; LightsOff; exit 1 }

# ---------- 3. 准备目录与 CSV ----------
$dir = Join-Path $OutRoot $Class
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$idx = @(Get-ChildItem "$dir\*.jpg" -ErrorAction SilentlyContinue).Count
if (-not (Test-Path $csvPath)) {
    "文件名,类别,缺陷类型,照度lx,亮度%,时间" | Out-File $csvPath -Encoding utf8
}
Say ("`n类别：{0}   已有 {1} 张，本次采 {2} 张，每张 {3} 张照片" -f $Class, $idx, $Count, $Shots) Cyan
Say "提示：请使用 print\A4_$Class.png 打印并裁开的标签；每换一张样品按一次回车。" Yellow
Say ""

$tmp = Join-Path $env:TEMP 'dsh_cap'
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

for ($n = 1; $n -le $Count; $n++) {
    $idx++
    Write-Host ("[{0}/{1}] 放入第 {2} 个样品（{3}），放好后按回车…" -f $n, $Count, $idx, $Class) -ForegroundColor White
    [void](Read-Host)

    $lux = GetLux
    for ($s = 1; $s -le $Shots; $s++) {
        # 抓 5 帧取最后一帧：前几帧常发黑/曝光未收敛
        Remove-Item "$tmp\*.jpg" -ErrorAction SilentlyContinue
        $oldEap2 = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $ffmpeg -hide_banner -loglevel error -f dshow -rtbufsize 200M -i "video=$Camera" `
                  -frames:v 5 -y "$tmp\f%02d.jpg" 2>&1 | Out-Null
        $ErrorActionPreference = $oldEap2
        $last = Get-ChildItem "$tmp\f*.jpg" -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1
        if (-not $last) { Say "  抓帧失败，跳过这一张" Red; continue }
        $suffix = if ($Shots -gt 1) { "_$s" } else { '' }
        $name = "{0}_{1:D4}{2}.jpg" -f $Class, $idx, $suffix
        Copy-Item $last.FullName (Join-Path $dir $name) -Force
        # “缺陷类型”对正常样本写“无”，其余写类别名
        $defect = if ($Class -eq 'normal') { '无' } else { $Class }
        "{0},{1},{2},{3},{4},{5}" -f $name, $Class, $defect, [math]::Round($lux, 1), $Brightness, (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') |
            Out-File $csvPath -Append -Encoding utf8
        Say ("  OK  {0}   ({1} lx)" -f $name, [math]::Round($lux, 1)) Green
    }
}

Say "`n== 采集结束 ==" Cyan
Say ("类别 {0} 共 {1} 张，保存在 {2}" -f $Class, $idx, $dir) Green
Say ("记录表：{0}" -f $csvPath) Green
LightsOff
