# this script uses the Windows events to determine if the user session has been opened
# it returns true or false

$logname="Application"
$logsource="PADSI agent"
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

function Is-Logged {
    param (
        [String]$Username
    )
    $res=$false
    Get-CimInstance Win32_Process -Filter "Name = 'explorer.exe'" |
        ForEach-Object {
            $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwner
            $u=$owner.user
            if ($u -eq $Username) {
                $res=$true
            }
        }
    return $res
}

$username=$env:PADSI_USER_NAME
$logged=Is-Logged $username
if ($logged) {
    Log-Event "User $username is logged"
} else {
    Log-Event "User $username is not logged"
}
Write-Output $logged
