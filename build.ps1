param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath $PSScriptRoot).Path
if (-not $Python) { $Python = Join-Path $project '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { $Python = (Get-Command $Python -ErrorAction Stop).Source }
& $Python -c "import sys; assert sys.version_info[:2] == (3, 11), 'Use Python 3.11 and requirements-build.lock'"
if ($LASTEXITCODE -ne 0) { throw 'Unsupported Python environment.' }
Push-Location $project
try { & $Python -B -m unittest discover -s tests -v } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw 'Regression tests failed; release unchanged.' }
$work = Join-Path $project 'build\work'
$stage = Join-Path $project 'build\stage'
$release = Join-Path $project 'release'
New-Item -ItemType Directory -Force $work,$stage,$release | Out-Null
$pythonBase = (& $Python -c 'import sys; print(sys.base_prefix)').Trim()
$extraBinaries = @()
foreach ($relative in @('Library\bin\ffi.dll', 'Library\bin\tcl86t.dll', 'Library\bin\tk86t.dll')) {
    $candidate = Join-Path $pythonBase $relative
    if (Test-Path -LiteralPath $candidate) { $extraBinaries += @('--add-binary', "$candidate;.") }
}
& $Python -m PyInstaller @extraBinaries --noconfirm --onefile --windowed `
    --name yxmys_selected_level_retry --paths $project --distpath $stage `
    --workpath $work --specpath $work `
    --additional-hooks-dir (Join-Path $project 'packaging_hooks') `
    --exclude-module onnxruntime --exclude-module pytest `
    --add-data "$(Join-Path $project 'config\default.yaml');config" `
    --add-data "$(Join-Path $project 'assets\templates');assets\templates" `
    --add-data "$(Join-Path $project 'selected_level_retry\default.yaml');selected_level_retry" `
    --add-data "$(Join-Path $project 'selected_level_retry\templates');selected_level_retry\templates" `
    (Join-Path $project 'selected_level_retry_launcher.py')
if ($LASTEXITCODE -ne 0) { throw 'Build failed; release unchanged.' }
$artifact = Join-Path $stage 'yxmys_selected_level_retry.exe'
$check = Join-Path $stage 'self-test.json'
if (Test-Path -LiteralPath $check) { Remove-Item -LiteralPath $check }
$process = Start-Process -FilePath $artifact -ArgumentList @('--self-test', "`"$check`"") -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $check)) { throw 'Packaged self-test failed; release unchanged.' }
$result = Get-Content -LiteralPath $check -Raw | ConvertFrom-Json
if (-not $result.success) { throw "Packaged self-test failed: $($result.error)" }
$published = Join-Path $release 'yxmys_selected_level_retry.exe'
Copy-Item -LiteralPath $artifact -Destination "$published.pending" -Force
Move-Item -LiteralPath "$published.pending" -Destination $published -Force
$manifest = [ordered]@{version=(Get-Content -LiteralPath (Join-Path $project 'VERSION') -Raw).Trim(); commit=(& git -C $project rev-parse HEAD); sourceDirty=[bool](& git -C $project status --porcelain); sha256=(Get-FileHash -LiteralPath $published -Algorithm SHA256).Hash; bytes=(Get-Item -LiteralPath $published).Length; selfTest=$result; builtAt=(Get-Date).ToUniversalTime().ToString('o')}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $release 'release-info.json') -Encoding utf8
Get-Item -LiteralPath $published | Select-Object FullName,Length
