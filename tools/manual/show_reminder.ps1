<#
Shows a SiftStack reminder popup with "OK" and "Remind Me Later" buttons.
"Remind Me Later" re-schedules a one-shot Task Scheduler job to show the
same reminder file again after -SnoozeMinutes (default 60).

Usage (matches the shape of the existing "SiftStack Reminder <date>" tasks):
  powershell.exe -NoProfile -WindowStyle Hidden -File show_reminder.ps1 -ReminderFile "<path>"
#>
param(
    [Parameter(Mandatory = $true)][string]$ReminderFile,
    [int]$SnoozeMinutes = 60
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$text = Get-Content -Raw $ReminderFile

$form = New-Object System.Windows.Forms.Form
$form.Text = "SiftStack reminder"
$form.StartPosition = "CenterScreen"
$form.Width = 560
$form.Height = 440
$form.Topmost = $true
$form.MinimizeBox = $false
$form.MaximizeBox = $false

$textBox = New-Object System.Windows.Forms.TextBox
$textBox.Multiline = $true
$textBox.ReadOnly = $true
$textBox.ScrollBars = "Vertical"
$textBox.Dock = "Fill"
$textBox.Text = $text
$textBox.Font = New-Object System.Drawing.Font("Segoe UI", 9)

$panel = New-Object System.Windows.Forms.Panel
$panel.Dock = "Bottom"
$panel.Height = 46

$laterButton = New-Object System.Windows.Forms.Button
$laterButton.Text = "Remind Me Later"
$laterButton.DialogResult = [System.Windows.Forms.DialogResult]::Retry
$laterButton.Width = 140
$laterButton.Height = 28
$laterButton.Location = New-Object System.Drawing.Point(280, 9)

$okButton = New-Object System.Windows.Forms.Button
$okButton.Text = "OK"
$okButton.DialogResult = [System.Windows.Forms.DialogResult]::OK
$okButton.Width = 80
$okButton.Height = 28
$okButton.Location = New-Object System.Drawing.Point(430, 9)

$panel.Controls.Add($laterButton)
$panel.Controls.Add($okButton)

$form.Controls.Add($textBox)
$form.Controls.Add($panel)
$form.AcceptButton = $okButton

$result = $form.ShowDialog()

if ($result -eq [System.Windows.Forms.DialogResult]::Retry) {
    $fireAt = (Get-Date).AddMinutes($SnoozeMinutes)
    $taskName = "SiftStack Reminder Snooze $($fireAt.ToString('yyyyMMddHHmmss'))"
    $scriptPath = $MyInvocation.MyCommand.Path
    $arg = "-NoProfile -WindowStyle Hidden -File `"$scriptPath`" -ReminderFile `"$ReminderFile`" -SnoozeMinutes $SnoozeMinutes"
    schtasks /Create /TN $taskName /TR "powershell.exe $arg" /SC ONCE /SD $fireAt.ToString('MM/dd/yyyy') /ST $fireAt.ToString('HH:mm') /F | Out-Null
}
