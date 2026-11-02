# Regenerates the README screenshots in docs\images\ from tools\screenshots.html.
# Start tools\serve.ps1 first, then from the repo root:
#   powershell -ExecutionPolicy Bypass -File tools\screenshots.ps1 [-Port 8765] [-Only thermostat,schedule]
#
# Each shot is opened in headless Edge at 2x, then cropped to the card plus a 16px margin.
param([int]$Port = 8765, [string[]]$Only)

Add-Type -AssemblyName System.Drawing
$repo = Split-Path $PSScriptRoot -Parent
$out = Join-Path $repo 'docs\images'
New-Item -ItemType Directory -Force $out | Out-Null
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
  Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { throw 'Microsoft Edge not found.' }
$profileDir = Join-Path $env:TEMP 'comfort-schedule-shots'

$shots = 'thermostat', 'thermostat-dial', 'thermostat-compact', 'thermostat-hold', 'thermostat-light',
  'schedule', 'schedule-edit', 'health', 'health-problem', 'runtime', 'runtime-week'
if ($Only) { $shots = $shots | Where-Object { $Only -contains $_ } }

foreach ($shot in $shots) {
  $raw = Join-Path $env:TEMP "comfort-shot-$shot.png"
  Remove-Item $raw -ErrorAction SilentlyContinue
  Start-Process -FilePath $edge -Wait -WindowStyle Hidden -ArgumentList '--headless=new', '--disable-gpu', '--hide-scrollbars',
    '--force-device-scale-factor=2', '--window-size=900,1400', "--user-data-dir=`"$profileDir`"", '--virtual-time-budget=8000',
    "--screenshot=`"$raw`"", "http://localhost:$Port/screenshots.html?shot=$shot"
  if (-not (Test-Path $raw)) { Write-Output "$shot`: no screenshot"; continue }

  # Crop to everything that isn't the page background (the top-left pixel), plus a margin.
  $img = [System.Drawing.Bitmap]::FromFile($raw)
  $bg = $img.GetPixel(0, 0).ToArgb()
  $minX = $img.Width; $minY = $img.Height; $maxX = -1; $maxY = -1
  for ($y = 0; $y -lt $img.Height; $y += 2) {
    for ($x = 0; $x -lt $img.Width; $x += 2) {
      if ($img.GetPixel($x, $y).ToArgb() -ne $bg) {
        if ($x -lt $minX) { $minX = $x }; if ($x -gt $maxX) { $maxX = $x }
        if ($y -lt $minY) { $minY = $y }; if ($y -gt $maxY) { $maxY = $y }
      }
    }
  }
  $pad = 32
  $left = [Math]::Max(0, $minX - $pad); $top = [Math]::Max(0, $minY - $pad)
  $rect = New-Object System.Drawing.Rectangle $left, $top,
    ([Math]::Min($img.Width, $maxX + $pad + 2) - $left), ([Math]::Min($img.Height, $maxY + $pad + 2) - $top)
  $crop = $img.Clone($rect, $img.PixelFormat)
  $img.Dispose()
  $crop.Save((Join-Path $out "$shot.png"), [System.Drawing.Imaging.ImageFormat]::Png)
  $crop.Dispose()
  Remove-Item $raw
  Write-Output ("{0,-20} {1}x{2}" -f $shot, $rect.Width, $rect.Height)
}
