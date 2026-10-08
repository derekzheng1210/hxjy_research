' diagnostic twin of wind_push_silent.vbs: verify path derivation,
' file existence and that Shell.Run hidden execution is not blocked
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
batPath = Replace(WScript.ScriptFullName, "_test_silent_echo.vbs", "wind_push_daily.bat")
WScript.Echo "batPath=" & batPath
WScript.Echo "exists=" & fso.FileExists(batPath)
rc = sh.Run("cmd /c exit 7", 0, True)
WScript.Echo "hidden-run rc=" & rc & " (expect 7)"
