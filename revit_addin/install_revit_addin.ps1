param(
    [string]$RevitVersion = "2026",
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$dll = Join-Path $projectDir "bin\$Configuration\net8.0-windows\AiRevitModeling.dll"

if (-not (Test-Path $dll)) {
    throw "DLL was not found. Build first with .\build_revit_addin.ps1"
}

$addinDir = Join-Path $env:APPDATA "Autodesk\Revit\Addins\$RevitVersion"
New-Item -ItemType Directory -Force -Path $addinDir | Out-Null

$addinPath = Join-Path $addinDir "AiRevitModeling.addin"
$assemblyPath = $dll.Replace("&", "&amp;")

$xml = @"
<?xml version="1.0" encoding="utf-8"?>
<RevitAddIns>
  <AddIn Type="Command">
    <Name>AI Revit Modeling</Name>
    <Assembly>$assemblyPath</Assembly>
    <AddInId>8C4D6F6B-44D4-46FA-8D0C-FDA7BE4F7B45</AddInId>
    <FullClassName>AiRevitModeling.AiRevitModelingCommand</FullClassName>
    <VendorId>CODX</VendorId>
    <VendorDescription>AI structured data to Revit API modeling demo</VendorDescription>
  </AddIn>
</RevitAddIns>
"@

Set-Content -LiteralPath $addinPath -Value $xml -Encoding UTF8

Write-Host "Installed add-in manifest:"
Write-Host "  $addinPath"
Write-Host "Restart Revit, then open Add-Ins -> External Tools -> AI Revit Modeling."

