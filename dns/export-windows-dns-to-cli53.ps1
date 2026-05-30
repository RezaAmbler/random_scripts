<#
.SYNOPSIS
    Export Windows DNS forward-zone records as cli53 (AWS Route 53) commands.

.DESCRIPTION
    Enumerates every forward lookup zone on a Windows DNS server and prints the
    equivalent cli53 commands (`create` for the zone, `rrcreate` for each record)
    needed to recreate its A, MX, CNAME, TXT, and SRV records in Amazon Route 53.

    This is a DRY RUN: it only PRINTS the commands. The actual cli53 invocations
    are left commented out in the source. Review the output, redirect it to a
    .ps1/.bat file, then run that to perform the migration.

    NOTE: despite earlier guesses, this script does not audit reverse DNS / PTR
    coverage — it is a Windows-DNS -> Route 53 migration helper. Reverse lookup
    zones are deliberately excluded.

.PARAMETER DnsServer
    Hostname of the Windows DNS server to read zones/records from.

.PARAMETER Cli53Path
    Path to the cli53 executable to emit in the generated commands.

.EXAMPLE
    .\export-windows-dns-to-cli53.ps1 -DnsServer dns01 | Tee-Object migrate.ps1

.NOTES
    Requires the DnsServer PowerShell module (RSAT) and rights to read the zones.
#>
param(
    [string]$DnsServer = "au3dns01",
    [string]$Cli53Path = "D:\cli53-windows-amd64.exe"
)

$zones = Get-DnsServerZone -ComputerName $DnsServer | Where-Object { $_.IsReverseLookupZone -eq $false }

foreach ($zone in $zones) {
    Write-Host -ForegroundColor Green -BackgroundColor Black "$Cli53Path create $($zone.ZoneName) --comment Zone Creation $((Get-Date).DateTime)"
    $records = Get-DnsServerResourceRecord -ZoneName $zone.ZoneName -ComputerName $DnsServer

    foreach ($record in $records) {
        if ($record.RecordType -eq "A") {
            Write-Host "$Cli53Path rrcreate $($record.HostName) 600 $($record.RecordType) $($record.RecordData.IPv4Address.IPAddressToString)"
        }

        if ($record.RecordType -eq "MX") {
            Write-Host "$Cli53Path rrcreate $($record.HostName) 600 $($record.RecordType) $($record.RecordData.Preference) $($record.RecordData.MailExchange)"
        }

        if ($record.RecordType -eq "CNAME") {
            Write-Host "$Cli53Path rrcreate $($record.HostName) $($record.RecordType) $($record.RecordData.HostNameAlias)"
        }

        if ($record.RecordType -eq "TXT") {
            Write-Host "$Cli53Path rrcreate $($record.HostName) 600 $($record.RecordType) $($record.RecordData.DescriptiveText)"
        }

        if ($record.RecordType -eq "SRV") {
            Write-Host "$Cli53Path rrcreate $($record.HostName) 600 $($record.RecordType) $($record.RecordData.Priority) $($record.RecordData.Weight) $($record.RecordData.Port) $($record.RecordData.DomainName)"
        }
    }
}
