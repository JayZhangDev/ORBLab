# Registers a Windows Task Scheduler job that starts the ORB paper runner every weekday.
# YOU run this once (it changes your Windows configuration):
#     powershell -ExecutionPolicy Bypass -File schedule_runner.ps1
#
# The runner waits for the 09:30 ET open by itself, so the task only has to start
# BEFORE 09:30 ET in your local time. Default 08:20 suits US Central (09:20 ET).
# Other time zones: pass -LocalTime "HH:mm" (e.g. 06:20 for Pacific).
param([string]$LocalTime = "08:20")

$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$python  = (Get-Command python).Source
$action  = New-ScheduledTaskAction -Execute $python -Argument "-m orblab.runner" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $LocalTime
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 9)
Register-ScheduledTask -TaskName "ORBLab-PaperRunner" -Action $action -Trigger $trigger -Settings $settings -Force
Write-Host "Scheduled ORBLab-PaperRunner for weekdays at $LocalTime local time. Remove with: Unregister-ScheduledTask ORBLab-PaperRunner"
