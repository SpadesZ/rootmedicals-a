# 檔案路徑: rootmedicals-a/Use-Environment.ps1
# 說明: 在 DEV(本機) 與 VM(交付) 兩份環境 profile 之間切換。
#       把 env-profiles/<profile>.env 複製成根目錄的 .env（docker-compose 讀的檔）。
#       只動 .env / .env.bak；secret 檔 .env.topic 完全不碰。
# 用法:
#   .\Use-Environment.ps1 -Profile dev
#   .\Use-Environment.ps1 -Profile vm
#   .\Use-Environment.ps1            # 不帶參數 -> 只顯示目前是哪個 profile
# ----------------------------------------------------------------------------------------------------
param(
    [ValidateSet("dev", "vm")]
    [string]$Profile
)

$ErrorActionPreference = "Stop"

$RootDir      = (Resolve-Path -LiteralPath (Split-Path -Parent $PSCommandPath)).Path
$ProfileDir   = Join-Path $RootDir "env-profiles"
$ActiveEnv    = Join-Path $RootDir ".env"
$BackupEnv    = Join-Path $RootDir ".env.bak"
$TopicSecret  = Join-Path $RootDir ".env.topic"
$TopicExample = Join-Path $RootDir ".env.topic.example"

function Get-ActiveProfileLabel {
    if (-not (Test-Path -LiteralPath $ActiveEnv)) { return "(none — .env 不存在)" }
    $line = Select-String -LiteralPath $ActiveEnv -Pattern '^\s*ROOTMEDICALS_ENV\s*=\s*(.+)\s*$' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($line) { return $line.Matches[0].Groups[1].Value.Trim() }
    return "(unknown — .env 無 ROOTMEDICALS_ENV)"
}

function Write-NoBom {
    param([string]$Path, [string]$Text)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $utf8NoBom)
}

# 不帶 -Profile：只回報現況，不動任何檔。
if (-not $Profile) {
    Write-Host "目前 active profile: $(Get-ActiveProfileLabel)"
    Write-Host "可切換: dev / vm  ->  .\Use-Environment.ps1 -Profile dev"
    exit 0
}

$SourceProfile = Join-Path $ProfileDir ("{0}.env" -f $Profile)
if (-not (Test-Path -LiteralPath $SourceProfile)) {
    throw "找不到 profile 檔: $SourceProfile"
}

# 備份現有 .env（避免覆蓋掉本機臨時手動值）。
if (Test-Path -LiteralPath $ActiveEnv) {
    Copy-Item -LiteralPath $ActiveEnv -Destination $BackupEnv -Force
    Write-Host "已備份舊 .env -> .env.bak"
}

# 以 UTF-8 無 BOM 寫入，docker/python 才不會讀到 BOM。
$content = [System.IO.File]::ReadAllText($SourceProfile)
Write-NoBom -Path $ActiveEnv -Text $content
Write-Host "已切換 active profile -> $Profile   (env-profiles\$Profile.env -> .env)"

# 提醒 secret 檔狀態（不自動塞值）。
if (-not (Test-Path -LiteralPath $TopicSecret)) {
    Write-Warning ".env.topic 不存在（跨服務 topic token）。"
    if (Test-Path -LiteralPath $TopicExample) {
        Write-Host "   可從範本複製後填入長隨機字串: Copy-Item .env.topic.example .env.topic"
    }
} else {
    Write-Host ".env.topic 已存在（secret 未更動）"
}

if ($Profile -eq "vm") {
    Write-Warning "vm.env 內含標記為 # CONFIRM 的 host/URL 值，交付前請確認 VM 實際服務位址。"
}

Write-Host ""
Write-Host "變更在下次重建容器後生效，例如："
Write-Host "   docker compose -f llmebm\docker-compose.yml up -d --force-recreate"
Write-Host "   docker compose -f ebm-rag\docker-compose-rag.yml up -d --force-recreate"
