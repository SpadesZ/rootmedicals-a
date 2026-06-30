# File Path: rootmedicals-a/legacy/launchers/README.md
# Timestamp: 2026-06-17 15:05 +08:00
# Version: v0.1
# Description:
#   Historical double-click CMD wrappers moved out of active module folders so
#   RootMedicals-A has a single public startup entry.
# Safety Notes:
#   These wrappers are retained only for traceability. Normal demos should start
#   from rootmedicals-a/RootMedicals-Control.cmd.
# ----------------------------------------------------------------------------------------------------

# Legacy Launchers

Use the root control panel instead:

```powershell
cd "<rootmedicals-a>"
.\RootMedicals-Control.cmd
```

The files below are old module-level CMD wrappers. They are not part of the
recommended operator path because the control panel already coordinates server,
client, RAG/LAVA, mode switching, and shutdown.
