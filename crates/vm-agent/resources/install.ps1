#
# Run this script to install the PADSI agent in a Windows VM
#

$svcename="padsi-agent"
$installdir="C:\Program Files\PADSI\agent"
$files=@(
    "padsi-agent.exe",
    "user-session-opened.ps1"
)

foreach ($f in $files) {
    if (-not (Test-Path $f)) {
        Write-Output "Mising file $f"
        exit 1
    }
}

if (Test-Path $installdir) {
    # uninstall first
    Write-Output "Uninstalling already installed PADSI agent service"
    sc.exe stop $svcename
    sc.exe delete $svcename
    Start-Sleep -Seconds 5

    # remove files
    Get-ChildItem -Path $installdir -Include *.* -File -Recurse | foreach { $_.Delete()}
}

# copy files
New-Item -ItemType Directory -Force -Path $installdir
foreach ($f in $files) {
    Write-Output "Copying file $f to $installdir"
    Copy-Item -Path $f -Destination $installdir
}

# install service
Write-Output "Installing PADSI agent service"
sc.exe create $svcename binPath= "$installdir/padsi-agent.exe --service" start= auto DisplayName="PADSI VM agent"
Write-Output "PADSI agent service is now installed"

Write-Output "Starting the PADSI agent service"
sc.exe start $svcename


Write-Output "Hiding drive letters from the GUI via a registry key"
Start-Process "REG" "ADD HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer /v NoDrives /t REG_DWORD /d 67108739 /f" -Wait

Write-Output "Disabling network firewall"
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled false
