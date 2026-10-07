# Local test server for the Comfort Schedule test pages (no Python or Node needed).
#   powershell -ExecutionPolicy Bypass -File tools\serve.ps1 [-Port 8765]
# Then open http://localhost:8765/ in a browser. Ctrl+C stops it.
#
# Files are looked up in tools\ first, then in custom_components\comfort_schedule\, so the
# pages always test the files in this repo. It only listens on localhost and refuses `..` paths.
param([int]$Port = 8765)

$tools = $PSScriptRoot
$repo = Split-Path $tools -Parent
$integration = Join-Path $repo 'custom_components\comfort_schedule'
$types = @{
  '.html' = 'text/html; charset=utf-8'
  '.js'   = 'text/javascript; charset=utf-8'
  '.json' = 'application/json; charset=utf-8'
}

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://localhost:$Port/")
$listener.Start()
Write-Host "Comfort Schedule test pages: http://localhost:$Port/  (Ctrl+C to stop)"
try {
  while ($listener.IsListening) {
    $ctx = $listener.GetContext()
    $path = [Uri]::UnescapeDataString($ctx.Request.Url.AbsolutePath.TrimStart('/'))
    if ($path -eq '') { $path = 'index.html' }
    $file = $null
    if ($path -notmatch '\.\.' -and $path -notmatch ':') {
      foreach ($dir in @($tools, $integration)) {
        $candidate = Join-Path $dir $path
        if (Test-Path $candidate -PathType Leaf) { $file = $candidate; break }
      }
    }
    if ($file) {
      $bytes = [IO.File]::ReadAllBytes($file)
      $ext = [IO.Path]::GetExtension($file).ToLower()
      $ctx.Response.ContentType = if ($types.ContainsKey($ext)) { $types[$ext] } else { 'text/plain; charset=utf-8' }
      $ctx.Response.Headers.Add('Cache-Control', 'no-store')
      $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    } else {
      $ctx.Response.StatusCode = 404
    }
    $ctx.Response.Close()
  }
} finally {
  $listener.Stop()
}
