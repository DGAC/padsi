#
# Run this script to install the PADSI agent in a Windows VM
#

$files=@(
    "padsi_agent.py",
    "common.py",
    "windows.py",
    "padsi-service.py",
    "user-session-opened.ps1"
)

foreach ($f in $files) {
    if (-not (Test-Path $f)) {
        Write-Output "Mising file $f"
        exit 1
    }
}

$installdir="C:\Program Files\PADSI\agent"
if (Test-Path $installdir) {
    # uninstall first
    Write-Output "Uninstalling already installed PADSI agent service"
    $shortinstalldir=(New-Object -com scripting.filesystemobject).getFolder($installdir).ShortPath # yeah, "C:\Program Files" has spaces...
    Start-Process -NoNewWindow -FilePath "python" -ArgumentList "$shortinstalldir\padsi-service.py stop" -Wait
    Start-Process -NoNewWindow -FilePath "python" -ArgumentList "$shortinstalldir\padsi-service.py remove" -Wait
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
$shortinstalldir=(New-Object -com scripting.filesystemobject).getFolder($installdir).ShortPath
Start-Process -NoNewWindow -FilePath "python" -ArgumentList "$shortinstalldir\padsi-service.py --startup auto install"

Write-Output "PADSI agent service is now installed"

Write-Output "Hiding drive letters from the GUI via a registry key"
Start-Process "REG" "ADD HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer /v NoDrives /t REG_DWORD /d 67108739 /f" -Wait

Write-Output "Disabling network firewall"
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled false
