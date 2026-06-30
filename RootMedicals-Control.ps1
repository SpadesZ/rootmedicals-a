# 檔案路徑: rootmedicals-a/RootMedicals-Control.ps1
# 產生時間: 2026-06-25 15:35 +08:00
# 版本: v0.3-控制台主題入口
# 說明: RootMedicals-A 單一控制入口，用於啟停 server、thin capture client、HIS 與 RAG/LAVA，
#       並在啟動模式前選擇醫師提醒浮窗主題。這支腳本是交付時建議使用的唯一入口，
#       讓操作者不需要分別記住 server/client/RAG 的多支啟動腳本。
# 交付: 保留於交付包；若未來刪除，需先確認閉環 demo 與對應文件不再依賴。
# ----------------------------------------------------------------------------------------------------
# 版本紀錄:
# - v0.1: 建立 WinForms 控制台，集中 Live/DemoFixture/Stop/RAG 啟動。
# - v0.2: 指向 thin-capture-client；client 只截圖送出，OCR/RAG 由後端執行。
# - v0.3: 啟動 Live/Live+Synthetic/DemoFixture 前加入白色、微軟黑、黑色三種提醒浮窗主題選擇。
# 安全筆記:
# - 只呼叫 rootmedicals-a 內的啟停腳本，不修改 API key、LAVA binding、RAG 資料或病歷資料。
# - 主題選擇只透過 ROOTMEDICALS_ALERT_THEME 傳給 thin capture client，不影響 server 判斷或燈號邏輯。
# ----------------------------------------------------------------------------------------------------

param(
    [ValidateSet("Gui", "Status", "Live", "LiveSynthetic", "DemoFixture", "Stop", "StartRag")]
    [string]$Action = "Gui",
    [ValidateSet("white", "microsoft_dark", "black")]
    [string]$AlertTheme = "white"
)

$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path -LiteralPath (Split-Path -Parent $PSCommandPath)).Path
$ServerDir = Join-Path $RootDir "llmxx-server"
$ClientDir = Join-Path $RootDir "llmxx-client\apps\thin-capture-client"
$ClinicalDir = Join-Path $RootDir "llmxx-client\apps\clinicalguard-standalone"
$RagComposePath = Join-Path $RootDir "ebm-rag\docker-compose-rag.yml"
$ServerStart = Join-Path $ServerDir "scripts\Start-LLMXX-Server.ps1"
$ServerStop = Join-Path $ServerDir "scripts\Stop-LLMXX-Server.ps1"
$ClientStart = Join-Path $ClientDir "Start-ThinCapture-System.ps1"
$ClientStop = Join-Path $ClientDir "Stop-ThinCapture-System.ps1"
$ClientStatePath = Join-Path $ClientDir "runtime\thin_capture_processes.json"
$ServerHealthUrl = "http://127.0.0.1:8017/api/health"
$DoctorViewerUrl = "http://127.0.0.1:8017/demo/latest"
$RagHealthUrl = "http://127.0.0.1:33301/api/v1/rag/health"
$RagAdminUrl = "http://127.0.0.1:33301/admin"

function Add-LogLine {
    param(
        [string]$Message,
        $TextBox = $null
    )
    $line = "$(Get-Date -Format 'HH:mm:ss')  $Message"
    if ($TextBox) {
        $TextBox.AppendText($line + [Environment]::NewLine)
    } else {
        Write-Host $line
    }
}

function Get-RecentLogSummary {
    param([string[]]$Paths)
    $parts = @()
    foreach ($path in $Paths) {
        if (-not (Test-Path -LiteralPath $path)) {
            continue
        }
        try {
            $tail = (Get-Content -LiteralPath $path -Tail 24 -ErrorAction Stop) -join "`n"
            if (-not [string]::IsNullOrWhiteSpace($tail)) {
                # 維護筆記:
                # 控制面板是給現場 demo 用的，失敗訊息不能只說 failed。
                # 這裡只抓固定 runtime log 的尾端，不碰病歷 payload 或 API key。
                $parts += "[$path]`n$tail"
            }
        } catch {
        }
    }
    if ($parts.Count -eq 0) {
        return ""
    }
    return ($parts -join "`n`n")
}

function Invoke-LocalProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [string]$WorkingDirectory = $RootDir,
        [int]$TimeoutSeconds = 180
    )
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = ($ArgumentList | ForEach-Object { ConvertTo-CommandLineArgument -Value $_ }) -join " "
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $false
    $startInfo.RedirectStandardError = $false
    $process = [System.Diagnostics.Process]::Start($startInfo)
    $completed = $process.WaitForExit([Math]::Max(1, $TimeoutSeconds) * 1000)
    if (-not $completed) {
        try {
            $process.Kill()
        } catch {
        }
        throw "Process timed out after $TimeoutSeconds seconds: $FilePath"
    }
    return [ordered]@{
        ExitCode = [int]$process.ExitCode
        StdOut = ""
        StdErr = ""
    }
}

function ConvertTo-CommandLineArgument {
    param([string]$Value)
    if ($null -eq $Value) {
        return '""'
    }
    $escaped = [string]$Value
    if ($escaped.Length -eq 0) {
        return '""'
    }
    if ($escaped -notmatch '[\s"]') {
        return $escaped
    }
    return '"' + ($escaped -replace '\\', '\\' -replace '"', '\"') + '"'
}

function Invoke-ProjectPowerShell {
    param(
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [string[]]$ExtraArgs = @()
    )
    $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath) + $ExtraArgs
    return Invoke-LocalProcess -FilePath "powershell.exe" -ArgumentList $args -WorkingDirectory (Split-Path -Parent $ScriptPath)
}

function Get-JsonOrNull {
    param([string]$Url)
    try {
        return Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 3
    } catch {
        return $null
    }
}

function Test-PidRunning {
    param([int]$PidValue)
    if ($PidValue -le 0) {
        return $false
    }
    return $null -ne (Get-Process -Id $PidValue -ErrorAction SilentlyContinue)
}

function Get-RootMedicalsStatus {
    $server = Get-JsonOrNull -Url $ServerHealthUrl
    $rag = Get-JsonOrNull -Url $RagHealthUrl
    $clientRunning = $false
    $clientMode = "unknown"
    if (Test-Path -LiteralPath $ClientStatePath) {
        try {
            $state = Get-Content -LiteralPath $ClientStatePath -Raw | ConvertFrom-Json
            $trayPid = 0
            if ($state.PSObject.Properties.Name -contains "capture_tray_pid") {
                $trayPid = [int]$state.capture_tray_pid
            } elseif ($state.PSObject.Properties.Name -contains "ocr_tray_pid") {
                $trayPid = [int]$state.ocr_tray_pid
            }
            $clientRunning = Test-PidRunning -PidValue $trayPid
            if ($state.PSObject.Properties.Name -contains "demo_fixture_payload_marker") {
                $clientMode = if ([bool]$state.demo_fixture_payload_marker) { "Demo Fixture" } else { "Live RAG" }
            }
            if ($state.PSObject.Properties.Name -contains "alert_theme") {
                $clientMode = "$clientMode / $($state.alert_theme)"
            }
        } catch {
            $clientRunning = $false
        }
    }

    $serverOnline = $null -ne $server -and [bool]$server.ok
    $serverMode = "offline"
    if ($serverOnline) {
        $serverMode = "Live RAG"
        if ($server.checks -and ($server.checks.PSObject.Properties.Name -contains "demo_fixture") -and [string]$server.checks.demo_fixture -eq "enabled") {
            $serverMode = "Demo Fixture"
        } elseif ($server.checks -and ($server.checks.PSObject.Properties.Name -contains "rag_synthetic_fallback") -and [string]$server.checks.rag_synthetic_fallback -eq "enabled") {
            $serverMode = "Live RAG + Synthetic"
        }
    }

    $ragReady = $false
    if ($null -ne $rag) {
        if ($rag.PSObject.Properties.Name -contains "ready") {
            $ragReady = [bool]$rag.ready
        } elseif ($rag.PSObject.Properties.Name -contains "readiness" -and $rag.readiness -and ($rag.readiness.PSObject.Properties.Name -contains "ready")) {
            $ragReady = [bool]$rag.readiness.ready
        }
    }
    return [ordered]@{
        ServerOnline = $serverOnline
        ServerMode = $serverMode
        ClientRunning = $clientRunning
        ClientMode = $clientMode
        RagReady = $ragReady
        RagStatus = if ($null -eq $rag) { "offline" } elseif ($ragReady) { "ready" } else { "not ready" }
    }
}

function Stop-ServerAndClient {
    param($LogBox = $null)
    Add-LogLine "Stopping thin capture/HIS..." $LogBox
    if (Test-Path -LiteralPath $ClientStop) {
        [void](Invoke-ProjectPowerShell -ScriptPath $ClientStop)
    }
    Add-LogLine "Stopping llmxx-server..." $LogBox
    if (Test-Path -LiteralPath $ServerStop) {
        [void](Invoke-ProjectPowerShell -ScriptPath $ServerStop)
    }
    Add-LogLine "Stopped server/client stack." $LogBox
}

function Start-Mode {
    param(
        [ValidateSet("Live", "LiveSynthetic", "DemoFixture")][string]$Mode,
        [ValidateSet("white", "microsoft_dark", "black")][string]$AlertTheme = "white",
        $LogBox = $null
    )
    Stop-ServerAndClient -LogBox $LogBox
    $serverArgs = @("-NoBrowser")
    $clientArgs = @()
    if ($Mode -eq "DemoFixture") {
        $serverArgs += "-DemoFixture"
        $clientArgs += "-DemoFixture"
    } elseif ($Mode -eq "LiveSynthetic") {
        $serverArgs += "-SyntheticFallback"
    }
    $clientArgs += @("-AlertTheme", $AlertTheme)
    Add-LogLine "Starting llmxx-server in $Mode mode..." $LogBox
    $serverResult = Invoke-ProjectPowerShell -ScriptPath $ServerStart -ExtraArgs $serverArgs
    if ($serverResult.ExitCode -ne 0) {
        $serverLogs = Get-RecentLogSummary -Paths @(
            (Join-Path $ServerDir "data\llmxx_server_8017.err.log"),
            (Join-Path $ServerDir "data\uvicorn_8017.err.log"),
            (Join-Path $ServerDir "data\llmxx_server_8017.out.log")
        )
        throw "llmxx-server start failed:`n$serverLogs"
    }
    Add-LogLine "Starting ClinicalGuard + thin capture in $Mode mode, alert theme=$AlertTheme..." $LogBox
    $clientResult = Invoke-ProjectPowerShell -ScriptPath $ClientStart -ExtraArgs $clientArgs
    if ($clientResult.ExitCode -ne 0) {
        $clientLogs = Get-RecentLogSummary -Paths @(
            (Join-Path $ClientDir "runtime\clinical_guard.err.log"),
            (Join-Path $ClientDir "runtime\clinical_guard.out.log"),
            (Join-Path $ClientDir "runtime\tray_client.err.log"),
            (Join-Path $ClientDir "runtime\tray_client.out.log"),
            (Join-Path $ClinicalDir "data\gui_launch_error.log")
        )
        throw "Thin capture start failed:`n$clientLogs"
    }
    Add-LogLine "$Mode mode is ready." $LogBox
}

function Start-RagStack {
    param($LogBox = $null)
    if (-not (Test-Path -LiteralPath $RagComposePath)) {
        throw "RAG compose file not found: $RagComposePath"
    }
    Add-LogLine "Starting RAG/LAVA Docker stack..." $LogBox
    $result = Invoke-LocalProcess -FilePath "docker" -ArgumentList @("compose", "-f", $RagComposePath, "up", "-d") -WorkingDirectory (Split-Path -Parent $RagComposePath)
    if ($result.ExitCode -ne 0) {
        throw "Docker compose failed: $($result.StdErr)"
    }
    Add-LogLine "RAG/LAVA Docker stack start requested." $LogBox
}

function Open-Url {
    param([string]$Url)
    Start-Process $Url
}

if ($Action -eq "Status") {
    Get-RootMedicalsStatus | ConvertTo-Json -Depth 4
    exit 0
}
if ($Action -eq "Stop") {
    Stop-ServerAndClient
    exit 0
}
if ($Action -eq "Live") {
    Start-Mode -Mode "Live" -AlertTheme $AlertTheme
    exit 0
}
if ($Action -eq "LiveSynthetic") {
    Start-Mode -Mode "LiveSynthetic" -AlertTheme $AlertTheme
    exit 0
}
if ($Action -eq "DemoFixture") {
    Start-Mode -Mode "DemoFixture" -AlertTheme $AlertTheme
    exit 0
}
if ($Action -eq "StartRag") {
    Start-RagStack
    exit 0
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = [System.Windows.Forms.Form]::new()
$form.Text = "RootMedicals Control"
$form.StartPosition = "CenterScreen"
$form.Size = [System.Drawing.Size]::new(720, 530)
$form.MinimumSize = [System.Drawing.Size]::new(660, 480)
$form.Font = [System.Drawing.Font]::new("Microsoft JhengHei UI", 10)

$title = [System.Windows.Forms.Label]::new()
$title.Text = "RootMedicals Control"
$title.Font = [System.Drawing.Font]::new("Microsoft JhengHei UI", 16, [System.Drawing.FontStyle]::Bold)
$title.Location = [System.Drawing.Point]::new(20, 18)
$title.Size = [System.Drawing.Size]::new(520, 32)
$form.Controls.Add($title)

$subtitle = [System.Windows.Forms.Label]::new()
$subtitle.Text = "One entry to switch Live RAG / Live + Synthetic / Demo Fixture, alert theme, and system status."
$subtitle.Location = [System.Drawing.Point]::new(22, 55)
$subtitle.Size = [System.Drawing.Size]::new(650, 24)
$form.Controls.Add($subtitle)

$statusLabel = [System.Windows.Forms.Label]::new()
$statusLabel.Location = [System.Drawing.Point]::new(22, 92)
$statusLabel.Size = [System.Drawing.Size]::new(660, 74)
$statusLabel.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
$statusLabel.BackColor = [System.Drawing.Color]::FromArgb(248, 250, 252)
$form.Controls.Add($statusLabel)

$btnLive = [System.Windows.Forms.Button]::new()
$btnLive.Text = "Start Live RAG"
$btnLive.Location = [System.Drawing.Point]::new(22, 185)
$btnLive.Size = [System.Drawing.Size]::new(150, 42)
$form.Controls.Add($btnLive)

$btnDemo = [System.Windows.Forms.Button]::new()
$btnDemo.Text = "Start Live + Synthetic"
$btnDemo.Location = [System.Drawing.Point]::new(188, 185)
$btnDemo.Size = [System.Drawing.Size]::new(170, 42)
$form.Controls.Add($btnDemo)

$btnFixture = [System.Windows.Forms.Button]::new()
$btnFixture.Text = "Start Demo Fixture"
$btnFixture.Location = [System.Drawing.Point]::new(374, 185)
$btnFixture.Size = [System.Drawing.Size]::new(170, 42)
$form.Controls.Add($btnFixture)

$btnStop = [System.Windows.Forms.Button]::new()
$btnStop.Text = "Stop Server + Client"
$btnStop.Location = [System.Drawing.Point]::new(22, 245)
$btnStop.Size = [System.Drawing.Size]::new(150, 36)
$form.Controls.Add($btnStop)

$btnRefresh = [System.Windows.Forms.Button]::new()
$btnRefresh.Text = "Refresh"
$btnRefresh.Location = [System.Drawing.Point]::new(560, 185)
$btnRefresh.Size = [System.Drawing.Size]::new(120, 42)
$form.Controls.Add($btnRefresh)

$btnRag = [System.Windows.Forms.Button]::new()
$btnRag.Text = "Start RAG/LAVA"
$btnRag.Location = [System.Drawing.Point]::new(188, 245)
$btnRag.Size = [System.Drawing.Size]::new(150, 36)
$form.Controls.Add($btnRag)

$btnViewer = [System.Windows.Forms.Button]::new()
$btnViewer.Text = "Open Doctor Viewer"
$btnViewer.Location = [System.Drawing.Point]::new(374, 245)
$btnViewer.Size = [System.Drawing.Size]::new(170, 36)
$form.Controls.Add($btnViewer)

$btnAdmin = [System.Windows.Forms.Button]::new()
$btnAdmin.Text = "Open RAG Admin"
$btnAdmin.Location = [System.Drawing.Point]::new(560, 245)
$btnAdmin.Size = [System.Drawing.Size]::new(120, 36)
$form.Controls.Add($btnAdmin)

$logBox = [System.Windows.Forms.TextBox]::new()
$logBox.Location = [System.Drawing.Point]::new(22, 300)
$logBox.Size = [System.Drawing.Size]::new(658, 170)
$logBox.Multiline = $true
$logBox.ScrollBars = [System.Windows.Forms.ScrollBars]::Vertical
$logBox.ReadOnly = $true
$logBox.BackColor = [System.Drawing.Color]::FromArgb(15, 23, 42)
$logBox.ForeColor = [System.Drawing.Color]::FromArgb(226, 232, 240)
$form.Controls.Add($logBox)

function Set-ButtonsEnabled {
    param([bool]$Enabled)
    foreach ($button in @($btnLive, $btnDemo, $btnFixture, $btnStop, $btnRefresh, $btnRag, $btnViewer, $btnAdmin)) {
        $button.Enabled = $Enabled
    }
}

function Refresh-StatusUi {
    $status = Get-RootMedicalsStatus
    $serverText = if ($status.ServerOnline) { "online / $($status.ServerMode)" } else { "offline" }
    $clientText = if ($status.ClientRunning) { "running / $($status.ClientMode)" } else { "stopped" }
    $ragText = $status.RagStatus
    $statusLabel.Text = " llmxx-server: $serverText`r`n Thin capture + HIS: $clientText`r`n RAG/LAVA: $ragText"
}

function Run-UiAction {
    param([scriptblock]$Work)
    try {
        Set-ButtonsEnabled -Enabled $false
        & $Work
    } catch {
        Add-LogLine ("ERROR: " + $_.Exception.Message) $logBox
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, "RootMedicals Control", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
    } finally {
        Refresh-StatusUi
        Set-ButtonsEnabled -Enabled $true
    }
}

function Show-AlertThemeDialog {
    $dialog = [System.Windows.Forms.Form]::new()
    $dialog.Text = "選擇醫師提醒視窗主題"
    $dialog.StartPosition = "CenterParent"
    $dialog.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
    $dialog.MaximizeBox = $false
    $dialog.MinimizeBox = $false
    $dialog.Size = [System.Drawing.Size]::new(360, 230)
    $dialog.Font = [System.Drawing.Font]::new("Microsoft JhengHei UI", 10)

    $label = [System.Windows.Forms.Label]::new()
    $label.Text = "請選擇本次醫師端浮窗顏色："
    $label.Location = [System.Drawing.Point]::new(20, 18)
    $label.Size = [System.Drawing.Size]::new(300, 24)
    $dialog.Controls.Add($label)

    $rbWhite = [System.Windows.Forms.RadioButton]::new()
    $rbWhite.Text = "白色"
    $rbWhite.Tag = "white"
    $rbWhite.Checked = $true
    $rbWhite.Location = [System.Drawing.Point]::new(28, 55)
    $rbWhite.Size = [System.Drawing.Size]::new(280, 24)
    $dialog.Controls.Add($rbWhite)

    $rbMicrosoft = [System.Windows.Forms.RadioButton]::new()
    $rbMicrosoft.Text = "微軟黑"
    $rbMicrosoft.Tag = "microsoft_dark"
    $rbMicrosoft.Location = [System.Drawing.Point]::new(28, 86)
    $rbMicrosoft.Size = [System.Drawing.Size]::new(280, 24)
    $dialog.Controls.Add($rbMicrosoft)

    $rbBlack = [System.Windows.Forms.RadioButton]::new()
    $rbBlack.Text = "黑色"
    $rbBlack.Tag = "black"
    $rbBlack.Location = [System.Drawing.Point]::new(28, 117)
    $rbBlack.Size = [System.Drawing.Size]::new(280, 24)
    $dialog.Controls.Add($rbBlack)

    $ok = [System.Windows.Forms.Button]::new()
    $ok.Text = "開始"
    $ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $ok.Location = [System.Drawing.Point]::new(160, 155)
    $ok.Size = [System.Drawing.Size]::new(75, 30)
    $dialog.Controls.Add($ok)

    $cancel = [System.Windows.Forms.Button]::new()
    $cancel.Text = "取消"
    $cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $cancel.Location = [System.Drawing.Point]::new(245, 155)
    $cancel.Size = [System.Drawing.Size]::new(75, 30)
    $dialog.Controls.Add($cancel)

    $dialog.AcceptButton = $ok
    $dialog.CancelButton = $cancel
    $result = $dialog.ShowDialog($form)
    if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
        return ""
    }
    foreach ($radio in @($rbWhite, $rbMicrosoft, $rbBlack)) {
        if ($radio.Checked) {
            return [string]$radio.Tag
        }
    }
    return "white"
}

function Start-ModeFromUi {
    param([ValidateSet("Live", "LiveSynthetic", "DemoFixture")][string]$Mode)
    $theme = Show-AlertThemeDialog
    if ([string]::IsNullOrWhiteSpace($theme)) {
        Add-LogLine "Start cancelled before launch." $logBox
        return
    }
    Run-UiAction { Start-Mode -Mode $Mode -AlertTheme $theme -LogBox $logBox }
}

$btnLive.Add_Click({ Start-ModeFromUi -Mode "Live" })
$btnDemo.Add_Click({ Start-ModeFromUi -Mode "LiveSynthetic" })
$btnFixture.Add_Click({ Start-ModeFromUi -Mode "DemoFixture" })
$btnStop.Add_Click({ Run-UiAction { Stop-ServerAndClient -LogBox $logBox } })
$btnRefresh.Add_Click({ Run-UiAction { Add-LogLine "Status refreshed." $logBox } })
$btnRag.Add_Click({ Run-UiAction { Start-RagStack -LogBox $logBox } })
$btnViewer.Add_Click({ Open-Url -Url $DoctorViewerUrl })
$btnAdmin.Add_Click({ Open-Url -Url $RagAdminUrl })

Refresh-StatusUi
Add-LogLine "Control panel ready." $logBox
[void]$form.ShowDialog()
