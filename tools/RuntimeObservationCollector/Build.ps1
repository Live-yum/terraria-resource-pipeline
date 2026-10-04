param([switch] $SelfTest)
$ErrorActionPreference = 'Stop'
# Compiles only the original sources in this directory. No game/XNA references,
# downloads, installers, package restore, startup invocation or external writes.
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $csc)) { throw 'Microsoft .NET Framework C# compiler is unavailable.' }
$bin = Join-Path $PSScriptRoot 'bin'
New-Item -ItemType Directory -Force -Path $bin | Out-Null
$exe = Join-Path $bin 'RuntimeObservationCollector.exe'
$sources = @('Program.cs','FixedProfile.cs','FixedCollector.cs','PlayerObservation.cs','PrimitiveJson.cs','WindowsJob.cs','SelfTest.cs') | ForEach-Object { Join-Path $PSScriptRoot $_ }
& $csc /nologo /codepage:65001 /target:exe /platform:x86 /optimize+ /warn:4 /warnaserror+ "/out:$exe" /reference:System.dll /reference:System.Core.dll $sources
if ($LASTEXITCODE -ne 0) { throw 'Original collector compilation failed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'App.config') -Destination ($exe + '.config')
if ($SelfTest) {
    & $exe --self-test
    if ($LASTEXITCODE -ne 0) { throw 'Original-code-only self-test failed.' }
}
