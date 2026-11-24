<#
    打包一个「完整版」发布压缩包：代码 + tests + 表情包。

    表情包不进 git（resources/emotions 被 .gitignore 排除），所以和上游一样，
    完整可运行的包挂在 Release 附件里供下载。

    用法：
        pwsh -File tools\pack_release.ps1
        pwsh -File tools\pack_release.ps1 -Version 2.1.0 -Out D:\tmp

    产物默认在 %TEMP%\tomato-chat-v<version>.zip，之后到 GitHub Release
    页面「Attach binaries」上传即可（或用 gh / API 上传）。
#>
param(
    [string]$Version = "",
    [string]$Out = $env:TEMP
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not $Version) {
    $versionFile = Join-Path $root "bot_plugins\ai_chat\_version.py"
    $line = Get-Content $versionFile | Where-Object { $_ -match "__version__" } | Select-Object -First 1
    $Version = ($line -replace '.*"([^"]+)".*', '$1').Trim()
}

$zipPath = Join-Path $Out "tomato-chat-v$Version.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

$excludeDirs = @('.git', '.venv', 'venv', 'data', 'logs', 'GPT-Speak-main',
                 '__pycache__', '.pytest_cache', '.ruff_cache', '.github', 'deploy')
$excludeFiles = @('webui_config.json', 'webui_prompts.json', 'emotions-pack.zip',
                  'launcher.out.log', 'launcher.err.log')

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::Open($zipPath, 'Create')
$count = 0
try {
    Get-ChildItem $root -Recurse -File -Force | ForEach-Object {
        $rel = $_.FullName.Substring($root.Length + 1)
        $parts = $rel -split '[\\/]'
        if ($parts | Where-Object { $excludeDirs -contains $_ }) { return }
        if ($excludeFiles -contains $parts[-1]) { return }
        if ($rel -like '*.pyc') { return }
        [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip, $_.FullName, ($rel -replace '\\', '/'))
        $count++
    }
}
finally {
    $zip.Dispose()
}

$mb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Host "打包完成：$zipPath"
Write-Host "  版本 $Version，$count 个文件，$mb MB"
Write-Host "  下一步：到 GitHub Release 页面把该 zip 作为附件上传"
