# Launches code-review-graph MCP via conda run (stdio preserved for Cursor).
# Mirrors sts2-context-coach: GPU PyTorch + code-review-graph[embeddings] live in that env.
# Override env name: set user or process env CRG_MCP_CONDA_ENV (default sts2-context-coach).
$ErrorActionPreference = 'Stop'

function Get-CondaExe {
    if ($env:CONDA_EXE -and (Test-Path -LiteralPath $env:CONDA_EXE)) {
        return $env:CONDA_EXE
    }
    $cmd = Get-Command conda.exe -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) {
        return $cmd.Source
    }
    if ($env:CONDA_ROOT) {
        $p = Join-Path $env:CONDA_ROOT 'Scripts\conda.exe'
        if (Test-Path -LiteralPath $p) { return $p }
    }
    foreach ($base in @(
            "$env:USERPROFILE\miniconda3",
            "$env:USERPROFILE\miniconda",
            "$env:USERPROFILE\anaconda3",
            "$env:USERPROFILE\mambaforge",
            'D:\miniconda',
            'C:\ProgramData\miniconda3'
        )) {
        if (-not $base) { continue }
        $p = Join-Path $base 'Scripts\conda.exe'
        if (Test-Path -LiteralPath $p) { return $p }
    }
    throw @'
conda.exe not found for code-review-graph MCP.
Fix: install Miniconda/Anaconda, add ...\Scripts to your user PATH, or set user env var CONDA_EXE to ...\Scripts\conda.exe, then restart Cursor.
'@
}

$condaExe = Get-CondaExe
$envName = if ($env:CRG_MCP_CONDA_ENV) { $env:CRG_MCP_CONDA_ENV.Trim() } else { 'sts2-context-coach' }
$condaArgs = @(
    'run',
    '-n', $envName,
    '--no-capture-output',
    'python', '-m', 'code_review_graph',
    'serve'
)

& $condaExe @condaArgs
exit $LASTEXITCODE
