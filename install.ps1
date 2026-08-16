param(
    [ValidateSet("cli", "app", "all")]
    [string]$Component = "all",
    [switch]$Local,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$Repository = if ($env:PURPORY_REPOSITORY) { $env:PURPORY_REPOSITORY } else { "sehwan505/purpory" }
$Version = if ($env:PURPORY_VERSION) { $env:PURPORY_VERSION } else { "latest" }
$ReleaseBase = $env:PURPORY_RELEASE_BASE_URL
$BinDir = if ($env:PURPORY_BIN_DIR) { $env:PURPORY_BIN_DIR } else { Join-Path $env:LOCALAPPDATA "Purpory\bin" }
$AppDir = if ($env:PURPORY_APP_DIR) { $env:PURPORY_APP_DIR } else { Join-Path $env:LOCALAPPDATA "Programs\Purpory" }
$AppTarget = Join-Path $AppDir "Purpory.exe"
$Shortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "Purpory.lnk"
$ScriptDir = $PSScriptRoot
$AppFullPath = [IO.Path]::GetFullPath($AppDir)
if ($AppFullPath -eq [IO.Path]::GetPathRoot($AppFullPath)) { throw "purpory install: refusing to use a drive root as the app directory" }
$Architecture = switch ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture) {
    "Arm64" { "arm64" }
    "X64" { "amd64" }
    default { throw "purpory install: unsupported architecture" }
}

function Remove-Cli {
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $BinDir "purpory.exe")
    Write-Host "Removed CLI: $BinDir\purpory.exe"
}

function Remove-App {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $AppDir
    Remove-Item -Force -ErrorAction SilentlyContinue $Shortcut
    Write-Host "Removed app: $AppTarget"
}

if ($Uninstall) {
    if ($Component -in @("cli", "all")) { Remove-Cli }
    if ($Component -in @("app", "all")) { Remove-App }
    Write-Host "Kept project data in ~/.purpory."
    exit 0
}

$TempDir = Join-Path ([IO.Path]::GetTempPath()) ("purpory-install-" + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $TempDir | Out-Null

function Get-ReleaseUrl([string]$Asset) {
    if ($ReleaseBase) {
        return $ReleaseBase.TrimEnd("/") + "/" + $Asset
    }
    if ($Version -eq "latest") {
        return "https://github.com/$Repository/releases/latest/download/$Asset"
    }
    return "https://github.com/$Repository/releases/download/$Version/$Asset"
}

function Get-Asset([string]$Asset) {
    $Destination = Join-Path $TempDir $Asset
    Invoke-WebRequest -UseBasicParsing -Uri (Get-ReleaseUrl $Asset) -OutFile $Destination
    $Checksums = Join-Path $TempDir "checksums.txt"
    if (-not (Test-Path $Checksums)) {
        Invoke-WebRequest -UseBasicParsing -Uri (Get-ReleaseUrl "checksums.txt") -OutFile $Checksums
    }
    $ChecksumLine = Get-Content $Checksums | Where-Object { $_ -match "\s$([regex]::Escape($Asset))$" } | Select-Object -First 1
    if (-not $ChecksumLine) { throw "purpory install: checksum missing for $Asset" }
    $Expected = ($ChecksumLine -split "\s+")[0]
    $Actual = (Get-FileHash -Algorithm SHA256 $Destination).Hash.ToLowerInvariant()
    if ($Actual -ne $Expected.ToLowerInvariant()) { throw "purpory install: checksum mismatch for $Asset" }
    return $Destination
}

function Install-Cli {
    $Executable = Join-Path $TempDir "purpory.exe"
    if ($Local) {
        if (-not (Test-Path (Join-Path $ScriptDir "go.mod"))) { throw "purpory install: -Local must run from the Purpory source directory" }
        Push-Location $ScriptDir
        try {
            & go build -o $Executable ./cmd/purpory
            if ($LASTEXITCODE -ne 0) { throw "purpory install: CLI build failed" }
        } finally {
            Pop-Location
        }
    } else {
        $Asset = "purpory-cli-windows-$Architecture.zip"
        Expand-Archive -Force (Get-Asset $Asset) $TempDir
    }
    if (-not (Test-Path $Executable)) { throw "purpory install: CLI executable is missing" }
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    Copy-Item -Force $Executable (Join-Path $BinDir "purpory.exe")
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $Entries = @($UserPath -split ";" | Where-Object { $_ })
    if ($BinDir -notin $Entries) {
        [Environment]::SetEnvironmentVariable("Path", (($Entries + $BinDir) -join ";"), "User")
        Write-Host "Added CLI directory to your user PATH. Open a new terminal to use it."
    }
    Write-Host "Installed CLI: $BinDir\purpory.exe"
}

function Install-App {
    $Executable = Join-Path $TempDir "purpory-desktop.exe"
    if ($Local) {
        if (-not (Test-Path (Join-Path $ScriptDir "wails.json"))) { throw "purpory install: -Local must run from the Purpory source directory" }
        Push-Location $ScriptDir
        try {
            & wails build
            if ($LASTEXITCODE -ne 0) { throw "purpory install: app build failed" }
        } finally {
            Pop-Location
        }
        Copy-Item (Join-Path $ScriptDir "build\bin\purpory.exe") $Executable
    } else {
        $Asset = "purpory-desktop-windows-$Architecture.zip"
        Expand-Archive -Force (Get-Asset $Asset) $TempDir
    }
    if (-not (Test-Path $Executable)) { throw "purpory install: app executable is missing" }
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    Copy-Item -Force $Executable $AppTarget
    $Shell = New-Object -ComObject WScript.Shell
    $Link = $Shell.CreateShortcut($Shortcut)
    $Link.TargetPath = $AppTarget
    $Link.WorkingDirectory = $env:USERPROFILE
    $Link.Save()
    Write-Host "Installed app: $AppTarget"
}

try {
    if ($Component -in @("cli", "all")) { Install-Cli }
    if ($Component -in @("app", "all")) { Install-App }
} finally {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $TempDir
}
