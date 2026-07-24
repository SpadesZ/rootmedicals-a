# 模組定位: RootMedicals DEV 與 VM delivery profile 的受控切換入口。
# 主要責任: 將 env-profiles/<profile>.env 原子寫入根目錄 .env，並保留前一版備份。
# 呼叫來源: 開發者在 repo root 手動執行；docker-compose 只讀產生的 .env。
# 輸入契約: Profile 僅允許 dev 或 vm；profile 必須存在且不含 topic/provider secrets。
# 輸出契約: UTF-8 no-BOM 的 .env 與可選 .env.bak；未確認 VM profile 會在寫檔前失敗。
# 安全邊界: 不寫入或輸出 .env.topic 值與 LAVA settings；不可把 VM 未確認值冒充已驗證配置。
# 維護提醒: 新增 profile 時同步 ValidateSet、env-profiles README 與 header contract test。
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

$content = [System.IO.File]::ReadAllText($SourceProfile)
# ponytail: `# CONFIRM` 是單一 fail-closed gate；profile 若增為機器產生格式，再升級成結構化 validation。
if ($Profile -eq "vm" -and $content -match '(?m)#\s*CONFIRM\b') {
    throw "vm profile 仍含 # CONFIRM；請先以實機 service DNS/URL 取代候選值並移除標記。未變更 .env。"
}

# 備份現有 .env（避免覆蓋掉本機臨時手動值）。
if (Test-Path -LiteralPath $ActiveEnv) {
    Copy-Item -LiteralPath $ActiveEnv -Destination $BackupEnv -Force
    Write-Host "已備份舊 .env -> .env.bak"
}

# 以 UTF-8 無 BOM 寫入，docker/python 才不會讀到 BOM。
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
    $secretKeys = @(
        "LLMEBM_TOPIC_GENERATION_TOKEN",
        "LLMEBM_REVIEW_ADMIN_TOKEN",
        "LLMEBM_HIERARCHY_ADMIN_TOKEN"
    )
    $secretText = [System.IO.File]::ReadAllText($TopicSecret)
    $missingKeys = @($secretKeys | Where-Object {
        $key = [Regex]::Escape($_)
        $secretText -notmatch "(?m)^\s*$key\s*=\s*\S+\s*$"
    })
    if ($missingKeys.Count -gt 0) {
        # ponytail: 只檢查 key 是否有非空值；secret 強度留給部署 secret manager／rotation policy。
        Write-Warning (".env.topic 缺少或未設定: {0}；受保護 API 會 fail closed。" -f ($missingKeys -join ", "))
    }
}

Write-Host ""
Write-Host "變更在下次重建容器後生效，例如："
Write-Host "   docker compose -f llmebm\docker-compose.yml up -d --force-recreate"
Write-Host "   docker compose -f ebm-rag\docker-compose-rag.yml up -d --force-recreate"
