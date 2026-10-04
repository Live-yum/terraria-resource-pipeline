param(
    [Parameter(Mandatory)][ValidateSet('amd64', 'arm64')][string]$Architecture,
    [string]$Image = 'terraria-resource-pipeline:local'
)
$ErrorActionPreference = 'Stop'
$go = (Get-Command go -ErrorAction Stop).Source
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
$oldGOOS = $env:GOOS
$oldGOARCH = $env:GOARCH
$oldCGO = $env:CGO_ENABLED
try {
    New-Item -ItemType Directory -Path '.runtime/container' -Force | Out-Null
    $env:GOOS = 'linux'
    $env:GOARCH = $Architecture
    $env:CGO_ENABLED = '0'
    & $go build -trimpath '-ldflags=-s -w' -o .runtime/container/trp ./cmd/trp
    if ($LASTEXITCODE -ne 0) { throw 'Go build failed' }
    # Stage only the prebuilt executable and helper sources. Docker on Windows
    # may enumerate inaccessible links before applying ignore rules; a minimal
    # context also prevents private uploads and audit snapshots entering a build.
    $context = Join-Path $root ('.runtime/container-context-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path (Join-Path $context '.runtime/container') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $context 'tools/RuntimeExtractor') -Force | Out-Null
    try {
        Copy-Item -LiteralPath '.runtime/container/trp' -Destination (Join-Path $context '.runtime/container/trp')
        Copy-Item -LiteralPath 'Dockerfile.prebuilt' -Destination (Join-Path $context 'Dockerfile')
        Get-ChildItem -LiteralPath 'tools/RuntimeExtractor' -Filter '*.cs' -File |
            Copy-Item -Destination (Join-Path $context 'tools/RuntimeExtractor')
        & docker build --platform "linux/$Architecture" -t $Image $context
        if ($LASTEXITCODE -ne 0) { throw 'Docker build failed' }
    } finally {
        $resolvedContext = [IO.Path]::GetFullPath($context)
        $contextParent = [IO.Path]::GetFullPath((Join-Path $root '.runtime')) + [IO.Path]::DirectorySeparatorChar
        if (-not $resolvedContext.StartsWith($contextParent, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Build context escaped the runtime directory'
        }
        Remove-Item -LiteralPath $resolvedContext -Recurse -Force
    }
} finally {
    $env:GOOS = $oldGOOS
    $env:GOARCH = $oldGOARCH
    $env:CGO_ENABLED = $oldCGO
    Pop-Location
}
