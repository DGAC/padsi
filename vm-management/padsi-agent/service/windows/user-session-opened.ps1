# this script uses the Windows events to determine if the user session has been opened
# it returns true or false

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

Log-Event "Looking for events for user $env:PADSI_USER_NAME"
$logonEvents=Get-WinEvent -FilterHashtable @{LogName = 'Security'; ID = 4624 } -MaxEvents 20
if ($logonEvents) {
    Log-Event "Some events found, checking if they are what we want"
    $evts=$logonEvents | Where-Object {
        $_.Properties[5].Value -eq $env:PADSI_USER_NAME
    }
    if ($evts) {
        Write-Output $true
    } else {
        Log-Event "No interesting event"
        Write-Output $false
    }
} else {
    Log-Event "Zero event found"
    Write-Output $false
}