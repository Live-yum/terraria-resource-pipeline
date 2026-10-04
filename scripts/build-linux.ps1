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
    & docker build --platform "linux/$Architecture" -f Dockerfile.prebuilt -t $Image .
    if ($LASTEXITCODE -ne 0) { throw 'Docker build failed' }
} finally {
    $env:GOOS = $oldGOOS
    $env:GOARCH = $oldGOARCH
    $env:CGO_ENABLED = $oldCGO
    Pop-Location
}
