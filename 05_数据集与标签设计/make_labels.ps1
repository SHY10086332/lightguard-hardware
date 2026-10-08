# make_labels.ps1 —— 生成"光稳智检"质检标签图案（正常 + 5 种缺陷）与 A4 打印页
# ------------------------------------------------------------------
# 用法：  powershell -ExecutionPolicy Bypass -File .\make_labels.ps1
# 产出：
#   .\labels\label_<类型>.png      单个标签（60 × 40 mm @300dpi）
#   .\print\A4_<类型>.png          A4 打印页（每页 18 个标签 + 裁切标记 + 100mm 校验尺）
#
# 打印要点：A4、**缩放必须选「实际大小 / 100%」**，不要用「适应页面」，
#           否则标签尺寸会偏。打完用页脚的 100mm 校验尺量一下即可确认。
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$DPI    = 300
$LAB_W  = 60.0     # 标签宽 mm
$LAB_H  = 40.0     # 标签高 mm

function MM([double]$v) { [int][math]::Round($v / 25.4 * $DPI) }

function New-Canvas([double]$wmm, [double]$hmm) {
  $b = New-Object System.Drawing.Bitmap((MM $wmm), (MM $hmm))
  $b.SetResolution($DPI, $DPI)
  $g = [System.Drawing.Graphics]::FromImage($b)
  $g.SmoothingMode     = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
  $g.Clear([System.Drawing.Color]::White)
  return @{ bmp = $b; g = $g }
}

# ---------- 画一个"正常标签"的图案到指定 Graphics ----------
function Draw-LabelContent($g, [string]$serial) {
  $black = [System.Drawing.Brushes]::Black
  $pen   = New-Object System.Drawing.Pen([System.Drawing.Color]::Black, (MM 0.35))

  # 外框
  $g.DrawRectangle($pen, (MM 1.2), (MM 1.2), (MM ($LAB_W - 2.4)), (MM ($LAB_H - 2.4)))

  # 标题
  $fTitle = New-Object System.Drawing.Font("Microsoft YaHei", 12, [System.Drawing.FontStyle]::Bold)
  $g.DrawString("光稳智检", $fTitle, $black, [float](MM 3), [float](MM 3))

  # 右上：小方块阵列（模拟二维码，缺块即缺陷）
  $bw = MM 1.6
  for ($r = 0; $r -lt 6; $r++) {
    for ($c = 0; $c -lt 6; $c++) {
      if ((($r * 7 + $c * 3) % 5) -lt 3) {
        $px = MM (48 + $c * 1.9)
        $py = MM (3.5 + $r * 1.9)
        $g.FillRectangle($black, $px, $py, $bw, $bw)
      }
    }
  }

  # 两行小字
  $fSmall = New-Object System.Drawing.Font("Microsoft YaHei", 6.5)
  $g.DrawString("型号 LG-2026-01", $fSmall, $black, [float](MM 3),  [float](MM 13))
  $g.DrawString("批次 20261007",   $fSmall, $black, [float](MM 3),  [float](MM 17.5))
  $g.DrawString("全检合格",         $fSmall, $black, [float](MM 33), [float](MM 13))

  # 条码区（宽窄不一的竖条，断条/缺墨/划痕最容易在这里做缺陷）
  $x = 3.0
  $pattern = @(2,1,1,3,1,2,1,1,2,2,1,3,1,1,2,1,3,1,2,1,1,2,3,1,1,2,1,2)
  foreach ($w in $pattern) {
    $g.FillRectangle($black, (MM $x), (MM 22.5), (MM $w), (MM 7))
    $x += $w + 1.0
    if ($x -gt ($LAB_W - 5)) { break }
  }

  # 底部编号
  $fTiny = New-Object System.Drawing.Font("Microsoft YaHei", 5.5)
  $g.DrawString("No. $serial", $fTiny, $black, [float](MM 3),  [float](MM 32.5))
  $g.DrawString("100% 全检",   $fTiny, $black, [float](MM 45), [float](MM 32.5))
}

# ---------- 生成一个标签（正常或带缺陷） ----------
function New-Label([string]$kind, [string]$path, [string]$serial) {
  $c = New-Canvas $LAB_W $LAB_H
  Draw-LabelContent $c.g $serial

  switch ($kind) {
    'normal' { }

    'brokenline' {   # 断线：外框上边缺一段
      $g = $c.g
      $g.FillRectangle([System.Drawing.Brushes]::White, (MM 22), (MM 0.4), (MM 14), (MM 1.6))
    }

    'faint' {        # 缺墨：条码右半段颜色变浅
      $g = $c.g
      $gray = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 155, 155, 155))
      $x = 33.0
      $pattern = @(2,1,1,3,1,2,1,1,2,2,1,3,1,1,2,1,3,1,2,1,1,2,3,1,1,2,1,2)
      foreach ($w in $pattern) {
        if ($x -ge 33.0 -and $x -le 54.0) { $g.FillRectangle($gray, (MM $x), (MM 22.5), (MM $w), (MM 7)) }
        $x += $w + 1.0
        if ($x -gt ($LAB_W - 5)) { break }
      }
    }

    'stain' {        # 污点：一小块深色污渍盖住文字
      $g = $c.g
      $g.FillEllipse([System.Drawing.Brushes]::Black, (MM 20), (MM 14.5), (MM 5), (MM 3.2))
    }

    'scratch' {      # 划痕：白色划痕斜穿条码（2026-10-07 由 0.5mm 加粗到 0.9mm 并拉长，确保肉眼可见）
      $g = $c.g
      $p = New-Object System.Drawing.Pen([System.Drawing.Color]::White, (MM 0.9))
      $p.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
      $p.EndCap   = [System.Drawing.Drawing2D.LineCap]::Round
      $g.DrawLine($p, (MM 27), (MM 19.5), (MM 45), (MM 33.5))
    }

    'offset' {       # 偏位/重影：整个图案整体偏移印一次（颜色浅）
      $g = $c.g
      $ghost = New-Object System.Drawing.Bitmap($c.bmp)
      $ia = New-Object System.Drawing.Imaging.ImageAttributes
      $cm = New-Object System.Drawing.Imaging.ColorMatrix
      $cm.Matrix33 = 0.42
      $ia.SetColorMatrix($cm)
      $off = MM 0.7
      $rect = New-Object System.Drawing.Rectangle($off, $off, $c.bmp.Width, $c.bmp.Height)
      $g.DrawImage($ghost, $rect, 0, 0, $ghost.Width, $ghost.Height,
                   [System.Drawing.GraphicsUnit]::Pixel, $ia)
      $ghost.Dispose()
    }
  }

  $c.bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
  $c.g.Dispose(); $c.bmp.Dispose()
  return $path
}

# ---------- 把某个标签排成 A4 打印页 ----------
function New-Sheet([string]$labelPath, [string]$outPath, [string]$title) {
  $c = New-Canvas 210 297
  $g = $c.g
  $label = [System.Drawing.Image]::FromFile($labelPath)

  $fBig  = New-Object System.Drawing.Font("Microsoft YaHei", 11, [System.Drawing.FontStyle]::Bold)
  $fNote = New-Object System.Drawing.Font("Microsoft YaHei", 7.5)
  $g.DrawString($title, $fBig, [System.Drawing.Brushes]::Black, [float](MM 13), [float](MM 7))
  $g.DrawString("打印设置：A4 纸、缩放选「实际大小 / 100%」（不要选「适应页面」）。裁切按标签边线剪开即可。",
                $fNote, [System.Drawing.Brushes]::Black, [float](MM 13), [float](MM 12.5))

  $cols = 3; $rows = 6
  $mx = 13.0; $my = 18.0; $gapx = 2.0; $gapy = 2.0
  $mark = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 120, 120, 120), 1)
  $n = 0
  for ($r = 0; $r -lt $rows; $r++) {
    for ($col = 0; $col -lt $cols; $col++) {
      $x = $mx + $col * ($LAB_W + $gapx)
      $y = $my + $r   * ($LAB_H + $gapy)
      $g.DrawImage($label, (MM $x), (MM $y), (MM $LAB_W), (MM $LAB_H))
      # 四角裁切标记
      foreach ($dx in 0, -2.5) {
        foreach ($dy in 0, -2.5) {
          $g.DrawLine($mark, (MM ($x + $dx)), (MM ($y + $dy)), (MM ($x + $dx + 1.2)), (MM ($y + $dy)))
          $g.DrawLine($mark, (MM ($x + $dx)), (MM ($y + $dy)), (MM ($x + $dx)), (MM ($y + $dy + 1.2)))
        }
      }
      $n++
    }
  }

  # 页脚：100mm 校验尺
  # 注意：最后一排标签底部 = 18 + 6*40 + 5*2 = 268 mm，
  # 所以说明文字必须画在 268 mm 以下（原先写在 267 mm，会压住标签，已修正）。
  $y = 281.0
  $g.DrawString("尺寸校验尺（量出来应该是 100 mm；不对说明打印缩放选错了）",
                $fNote, [System.Drawing.Brushes]::Black, [float](MM 13), [float](MM ($y - 6)))
  $p = New-Object System.Drawing.Pen([System.Drawing.Color]::Black, (MM 0.4))
  $g.DrawLine($p, (MM 13), (MM $y), (MM 113), (MM $y))
  for ($i = 0; $i -le 10; $i++) {
    $xx = 13 + $i * 10
    $len = if ($i % 5 -eq 0) { 4.0 } else { 2.0 }
    $g.DrawLine($p, (MM $xx), (MM $y), (MM $xx), (MM ($y + $len)))
  }
  $g.DrawString("本页共 $n 个标签（每页 18 个）。", $fNote,
                [System.Drawing.Brushes]::Black, [float](MM 13), [float](MM ($y + 7)))

  $c.bmp.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
  $label.Dispose(); $c.g.Dispose(); $c.bmp.Dispose()
  return $outPath
}

# ================= 主流程 =================
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$dirL = Join-Path $root 'labels'
$dirP = Join-Path $root 'print'
New-Item -ItemType Directory -Force -Path $dirL, $dirP | Out-Null

$kinds = @(
  @{ k = 'normal';     t = '正常（合格品）' },
  @{ k = 'brokenline'; t = '缺陷：断线（外框缺一段）' },
  @{ k = 'faint';      t = '缺陷：缺墨（条码变浅）' },
  @{ k = 'stain';      t = '缺陷：污点（黑斑）' },
  @{ k = 'scratch';    t = '缺陷：划痕（白色斜线）' },
  @{ k = 'offset';     t = '缺陷：偏位重影（整体重印）' }
)

foreach ($v in $kinds) {
  $lp = Join-Path $dirL ("label_" + $v.k + ".png")
  New-Label $v.k $lp '202610070001' | Out-Null
  $sp = Join-Path $dirP ("A4_" + $v.k + ".png")
  New-Sheet $lp $sp ("光稳智检 · 标签样张 —— " + $v.t) | Out-Null
  Write-Output ("已生成: labels\label_{0}.png  +  print\A4_{0}.png" -f $v.k)
}
Write-Output ""
Write-Output "打印建议：先打 A4_normal.png 一张，用页脚校验尺确认尺寸，再批量打其余 5 张。"
