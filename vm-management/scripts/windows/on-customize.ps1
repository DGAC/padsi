# this script customizes a system for the specified user:
# - create the user
# - set the user to autologon
# - reboot the system so the user has an initialized profile

$stagefile="C:\Windows\Temp\padsi-stage2"

# load username from config
$jsoncontent = Get-Content -Path "$env:PADSI_ETC_DIR\config.json" -Raw | ConvertFrom-Json
$username=$jsoncontent.PADSI_USER_NAME

# logging
$logname="Application"
$logsource="PADSICustomization"
if (-not [System.Diagnostics.EventLog]::SourceExists($logsource)) {
    New-EventLog -logname $logname -Source $logsource
}

function Log-Event {
    param (
        [String]$Message,
        [int]$EventId=1001,
        [String]$EntryType="Information"
    )
    Write-EventLog -LogName $logname -Source $logsource -EntryType $EntryType -EventId $EventId -Message $Message
}

# actual customization work
if (Test-Path $stagefile) {
    # let the user auto login to have a profile and wait for that profile to be initialized
    Log-Event "Customization for user $username, stage 2"

    $timeout=120
    $elapsed=0

    $user_home="C:\Users\$username"
    While (-not (Test-Path $user_home)) {
        Start-Sleep -Seconds 1
    }

    $expected_file="$user_home\NTUSER.DAT"
    Log-Event "Waiting for end of profile creation with file $expected_file"
    While (-not (Test-Path $expected_file) -and $elapsed -lt $timeout) {
        Start-Sleep -Seconds 1
        $elapsed++
    }
    if (Test-Path $expected_file) {
        # profile is now created, wait a bit more and shutdown
        Remove-Item -Path $stagefile
        Start-Sleep -Seconds 10
        Log-Event "Profile created, shuting down now"
        Stop-Computer -Force
    }
    else {
        # something failed
        Log-Event "Customization for user $username, stage 1"
        Log-Event -Message "Profile creation timed out" -EntryType "Error"
    }
}
else {
    Log-Event "Customization for user $username, stage 1"

    # create user
    $username=$jsoncontent.PADSI_USER_NAME
    $fullname=$jsoncontent.PADSI_USER_FULLNAME
    New-LocalUser -Name $username -NoPassword -FullName $fullname
    Set-LocalUser -Name $username -PasswordNeverExpires $true -UserMayChangePassword $false

    # add user to the "Users" group
    $usersgroup=New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-545")
    $groupname=$usersgroup.Translate([System.Security.Principal.NTAccount]).Value # will be like BUILTIN\Utilisateurs
    $groupname=$groupname -replace '^BUILTIN\\', ''
    Add-LocalGroupMember -Group $groupname -Member $username

    # set autologon
    $RegistryPath = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
    Set-ItemProperty $RegistryPath 'AutoAdminLogon' -Value "1" -Type String
    Set-ItemProperty $RegistryPath 'DefaultUsername' -Value "$username" -type String
    Set-ItemProperty $RegistryPath 'DefaultPassword' -Value "" -type String

    # reboot to stage 2
    New-Item -ItemType File -Path $stagefile -Force
    Log-Event "Restarting system"
    Restart-Computer -Force
}
