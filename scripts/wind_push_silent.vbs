' Silent wrapper for wind_push_daily.bat:
' runs the batch with a hidden window (SW_HIDE = 0), waits for it to finish,
' and forwards its exit code to Task Scheduler.
' Debug lines go to wind_push_silent_debug.log next to this script.
On Error Resume Next
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
batPath = Replace(WScript.ScriptFullName, "wind_push_silent.vbs", "wind_push_daily.bat")
Set dbg = fso.OpenTextFile(Replace(WScript.ScriptFullName, ".vbs", "_debug.log"), 8, True)
dbg.WriteLine Now & " | script=" & WScript.ScriptFullName
dbg.WriteLine Now & " | batPath=" & batPath & " exists=" & fso.FileExists(batPath)
rc = sh.Run("""" & batPath & """", 0, True)
If Err.Number <> 0 Then
    dbg.WriteLine Now & " | RUN ERROR " & Err.Number & ": " & Err.Description
    dbg.Close
    WScript.Quit 1
End If
dbg.WriteLine Now & " | bat finished rc=" & rc
dbg.Close
WScript.Quit rc
