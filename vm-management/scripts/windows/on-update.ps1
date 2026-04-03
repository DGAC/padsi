# this script updates the system
# and generates a report

# refer to https://www.partitionwizard.com/partitionmagic/powershell-windows-update.html

Install-Module -Name PSWindowsUpdate -Force

Get-WindowsUpdate -AcceptAll -Install -AutoReboot
