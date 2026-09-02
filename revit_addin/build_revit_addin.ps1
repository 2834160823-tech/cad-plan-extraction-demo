param(
    [string]$RevitInstallDir = "",
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$project = Join-Path $projectDir "AiRevitModeling.csproj"
$nugetConfig = Join-Path $projectDir "NuGet.Config"
if (-not $RevitInstallDir) {
    $candidates = @(
        "C:\Program Files\Autodesk\Revit 2026",
        "C:\Program Files\Autodesk\Revit 2025",
        (Join-Path $projectDir "lib")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path (Join-Path $candidate "RevitAPI.dll")) {
            $RevitInstallDir = $candidate
            break
        }
    }
}

if (-not $RevitInstallDir -or -not (Test-Path (Join-Path $RevitInstallDir "RevitAPI.dll"))) {
    throw "RevitAPI.dll was not found. Run: .\build_revit_addin.ps1 -RevitInstallDir 'C:\Program Files\Autodesk\Revit 2026'"
}

if (-not (Test-Path $nugetConfig)) {
    Set-Content -LiteralPath $nugetConfig -Encoding UTF8 -Value @"
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
  </packageSources>
</configuration>
"@
}

dotnet build $project -c $Configuration -p:RevitInstallDir="$RevitInstallDir" --configfile "$nugetConfig"
if ($LASTEXITCODE -ne 0) {
    throw "dotnet build failed with exit code $LASTEXITCODE"
}

$dll = Join-Path $projectDir "bin\$Configuration\net8.0-windows\AiRevitModeling.dll"
if (-not (Test-Path $dll)) {
    throw "Build finished but DLL was not found: $dll"
}

Write-Host "Built:"
Write-Host "  $dll"
