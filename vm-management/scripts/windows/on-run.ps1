# load config
$jsoncontent = Get-Content -Path "$env:PADSI_ETC_DIR\config.json" -Raw | ConvertFrom-Json
$proxy=$jsoncontent.PADSI_WEB_PROXY

function Log-Event {
    param (
        [String]$Message,
        [int]$EventId=1001,
        [String]$EntryType="Information"
    )
    Write-EventLog -LogName $logname -Source $logsource -EntryType $EntryType -EventId $EventId -Message $Message
}

if ($proxy) {
    # WinHTTP proxy
    Log-Event "Defining WinHTTP proxy to $proxy"
    netsh winhttp set proxy proxy:3128

    # WinINET proxy
    Log-Event "Defining WinINET proxy to $proxy"
    $path="HKLM:\Software\Policies\Microsoft\WindowsCurrentVersion\Internet Settings"
    Set-ItemProperty -Path $path -Name ProxySettingsPerUser -Value 0
    Set-ItemProperty -Path $path -Name ProxyEnable -Value 1
    Set-ItemProperty -Path $path -Name ProxyServer -Value "proxy:3128"
}
else {
    Log-Event "No proxy defined"
}
