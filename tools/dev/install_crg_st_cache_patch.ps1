# Copy crg_st_model_cache.py + .pth bootstrap into a conda env's site-packages.
# Default env: sts2-context-coach (same as sts2-context-coach repo). Override with -EnvName.
param(
    [string]$CondaBase = "",
    [string]$EnvName = "sts2-context-coach"
)
$ErrorActionPreference = "Stop"
$env:CONDA_NO_PLUGINS = "true"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
if (-not $CondaBase) {
    $condaExe = (Get-Command conda -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
    if (-not $condaExe) { $condaExe = "D:\miniconda\Scripts\conda.exe" }
    $CondaBase = (& $condaExe info --base).Trim()
}
$SitePackages = Join-Path $CondaBase "envs\$EnvName\Lib\site-packages"
$Src = Join-Path $RepoRoot "tools\dev\crg_st_model_cache.py"
$DstPy = Join-Path $SitePackages "crg_st_model_cache.py"
$DstPth = Join-Path $SitePackages "zzz_crg_st_model_cache.pth"

if (-not (Test-Path $SitePackages)) {
    Write-Error "site-packages not found: $SitePackages"
}
Copy-Item -Force $Src $DstPy
Set-Content -Path $DstPth -Value "import crg_st_model_cache" -Encoding ascii
Write-Host "Installed CRG ST cache patch to $SitePackages (enable via CRG_APPLY_ST_CACHE_PATCH=1 in MCP env)."
