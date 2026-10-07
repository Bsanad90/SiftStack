<#
Runs the daily team KPI (Hubstaff + SmrtPhone APIs, read-only) and shows the result in a
pop-up. Scheduled as "SiftStack Team KPI Daily", every day at 20:00.

  powershell.exe -NoProfile -WindowStyle Hidden -File team_kpi_daily.ps1 [-Date 2026-10-06]

The report is also saved to output/team_kpi/daily/<date>.txt (log beside it).
#>
param([string]$Date = "")

$Repo   = "C:\Users\pc\Desktop\Galal Development\Sift Stack"
$Python = "C:\Users\pc\AppData\Local\Programs\Python\Python314\python.exe"
$env:PYTHONIOENCODING = "utf-8"

Set-Location $Repo
$pyArgs = @("src\scripts\team_kpi.py", "daily")
if ($Date) { $pyArgs += @("--date", $Date) }
$text = & $Python @pyArgs 2>&1 | Out-String
if (-not $text.Trim()) { $text = "Team KPI daily produced no output - check output\team_kpi\daily\*.log" }

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = "SiftStack - Team KPI"
$form.StartPosition = "CenterScreen"
$form.Width = 620
$form.Height = 380
$form.Topmost = $true
$form.MinimizeBox = $false
$form.MaximizeBox = $false

$box = New-Object System.Windows.Forms.TextBox
$box.Multiline = $true
$box.ReadOnly = $true
$box.ScrollBars = "Vertical"
$box.Dock = "Fill"
$box.Font = New-Object System.Drawing.Font("Consolas", 10)
$box.Text = $text -replace "`r?`n", "`r`n"

$ok = New-Object System.Windows.Forms.Button
$ok.Text = "OK"
$ok.Dock = "Bottom"
$ok.Height = 32
$ok.DialogResult = [System.Windows.Forms.DialogResult]::OK

$form.Controls.Add($box)
$form.Controls.Add($ok)
$form.AcceptButton = $ok
[void]$form.ShowDialog()
