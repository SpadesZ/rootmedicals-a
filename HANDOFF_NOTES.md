# Agent Handoff: One-Click Demo Workflow & Local Start Fixes

This document serves as a transition guide for the next agent to take over the task.

## 1. User Requirements
The user wants to streamline the EBM Demo flow in the local environment:
* **One-Click Trigger**: In the `llmxx-client` Mock HIS GUI (`clinicalguard-standalone`), when clicking the "綠燈" (Green), "黃燈" (Yellow), or "橘燈" (Orange) buttons, the system should execute three actions automatically:
  1. Input the respective SOAP case data into the HIS textboxes. (Already exists)
  2. Capture the window screenshot and send it to the `llmxx-server` for OCR and RAG/EBM evaluation.
  3. Display the alert light and comment popup widget (`DoctorAlertWidget`).
* **Offline Testing (No GCP VM)**: Test this flow locally using the built-in offline mock loop (`DemoFixture` mode) without starting the remote VM.

---

## 2. Work Done & Modifications

### A. One-Click Automation
We updated the `_apply_demo_scenario` method in the Mock HIS code:
* **File**: `llmxx-client/apps/clinicalguard-standalone/src/clinical_guard_app/gui_app.py`
* **Changes**: At the end of the method, we added code to simulate the global hotkey press (`Ctrl+Alt+G`) using the Windows `win32api.keybd_event` API after refreshing the Tkinter UI and sidecar files.
* **Why**: The Mock HIS (`gui_app.py`, Tkinter) and the Thin Capture Client (`tray_app.py`, PySide6) run in separate Python processes. Simulating the hotkey is the cleanest way to trigger the capture and PyQt popup window across processes.

### B. PowerShell Health Check Fixes
Windows PowerShell 5.1 sometimes fails to auto-parse JSON responses from FastAPI `/api/health`, returning raw strings instead of objects. This caused `[bool]$response.ok` to return `$false`, resulting in fake "start failed" errors even though uvicorn was running.
* **Files Modified**:
  * `llmxx-server/scripts/Start-LLMXX-Server.ps1`
  * `RootMedicals-Control.ps1`
  * `llmxx-client/RootMedicals-Control.ps1`
* **Changes**: Updated `Test-Health` and `Get-JsonOrNull` functions to handle both parsed PSCustomObjects and raw JSON strings.

### C. File Encoding Fixes
The PowerShell control scripts had traditional Chinese characters (`"白色"`, `"微軟黑"`), but were saved in UTF-8 without BOM. On traditional Chinese Windows systems, PowerShell 5.1 interpreted them as Big5, leading to broken quote characters and parser syntax errors (`UnexpectedToken`).
* **Changes**: Converted both `RootMedicals-Control.ps1` and `llmxx-client/RootMedicals-Control.ps1` to **UTF-8 with BOM** (`utf-8-sig`) using Python. The parser syntax errors are now resolved.

---

## 3. Current Issues & Blocks

Despite the script fixes, the user still encountered the `llmxx-server start failed` popup.
* **Reason**: When the server failed to pass the health check in previous runs, the Python background process (e.g., PID `37836`) was not cleaned up and remained as a zombie process occupying port `8017`.
* **Symptom**: When trying to restart, uvicorn failed to bind to port `8017` (or the status check hit the old zombie process, which failed the health check logic).

---

## 4. Immediate Next Steps for the Next Agent

1. **Clean up Port 8017**:
   Force-terminate any process currently occupying port `8017` on the user's machine. Run this in PowerShell:
   ```powershell
   Stop-Process -Id (Get-NetTCPConnection -LocalPort 8017 -ErrorAction SilentlyContinue).OwningProcess -Force -ErrorAction SilentlyContinue
   taskkill /f /im python.exe
   ```
2. **Start Demo Fixture**:
   Launch the control panel via `RootMedicals-Control.cmd` and click **`Start Demo Fixture`**. Verify that `llmxx-server` status turns green (online) without error popups.
3. **Verify One-Click Flow**:
   Open the Mock HIS, click the "綠燈" (Green) button, and verify that the S/O/A/P text is filled, a screenshot is taken, and the PyQt green alert popup appears on the desktop automatically.
