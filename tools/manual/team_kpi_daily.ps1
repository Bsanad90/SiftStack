<#
Runs the team KPI (Hubstaff + SmrtPhone APIs, read-only) and opens the dashboard in the
default browser. Scheduled tasks:
  "SiftStack Team KPI Daily"   every day 20:00   -Mode daily
  "SiftStack Team KPI Weekly"  Sunday    20:05   -Mode weekly  (the 7 days ending that day)

  powershell.exe -NoProfile -WindowStyle Hidden -File team_kpi_daily.ps1 [-Mode weekly] [-Date 2026-10-06]

Output: output/team_kpi/<daily|weekly>/<stem>.html (+ .txt, .log). If no page was written,
a text pop-up shows whatever the script printed.
#>
param([ValidateSet("daily", "weekly")][string]$Mode = "daily", [string]$Date = "")

$Repo   = "C:\Users\pc\Desktop\Galal Development\Sift Stack"
$Python = "C:\Users\pc\AppData\Local\Programs\Python\Python314\python.exe"
$env:PYTHONIOENCODING = "utf-8"
# numpy's OpenBLAS reserves a buffer per CPU core at import; with the Windows commit limit nearly
# full it fails ("Memory allocation still failed after 10 retries", 2026-10-07 20:00). One thread is plenty.
$env:OPENBLAS_NUM_THREADS = "1"
$env:OMP_NUM_THREADS = "1"

Set-Location $Repo
# Mohamed Hisham's sheet KPI first (it also takes the daily snapshot the progress line needs)
$end = if ($Date) { [datetime]::Parse($Date) } else { Get-Date }
$start = if ($Mode -eq "weekly") { $end.AddDays(-6) } else { $end }
$dmArgs = @("src\scripts\data_manager_kpi.py", "--from", $start.ToString("yyyy-MM-dd"), "--to", $end.ToString("yyyy-MM-dd"))
if ($Mode -eq "weekly") { $dmArgs += "--account" }
& $Python @dmArgs *> "$Repo\output\team_kpi\data_manager\last_run.log"

$pyArgs = @("src\scripts\team_kpi.py", $Mode)
if ($Date) { $pyArgs += @("--date", $Date) }
$out = & $Python @pyArgs 2>&1 | Out-String

$html = ($out -split "`r?`n" | Where-Object { $_ -like "HTML: *" } | Select-Object -Last 1)
if ($html) {
    $path = $html.Substring(6).Trim()
    if (Test-Path $path) { Start-Process $path; exit 0 }
}

# fallback: show the raw output so a failure is never silent
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.Text = "SiftStack - Team KPI ($Mode) did not finish"
$form.StartPosition = "CenterScreen"; $form.Width = 680; $form.Height = 420; $form.Topmost = $true
$box = New-Object System.Windows.Forms.TextBox
$box.Multiline = $true; $box.ReadOnly = $true; $box.ScrollBars = "Vertical"; $box.Dock = "Fill"
$box.Font = New-Object System.Drawing.Font("Consolas", 10)
if (-not $out.Trim()) { $out = "No output - see output\team_kpi\$Mode\*.log" }
$box.Text = $out -replace "`r?`n", "`r`n"
$form.Controls.Add($box)
[void]$form.ShowDialog()
