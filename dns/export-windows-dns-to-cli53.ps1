<#
.SYNOPSIS
    Export Windows DNS forward-zone records as cli53 (AWS Route 53) commands.

.DESCRIPTION
    Enumerates every forward lookup zone on a Windows DNS server and prints the
    equivalent cli53 commands (`create` for the zone, `rrcreate` for each record)
    needed to recreate its A, MX, CNAME, TXT, and SRV records in Amazon Route 53.

    This is a DRY RUN: it generates command TEXT, it does not execute anything.
    The cli53 commands are written to the output stream (so `>` or Tee-Object
    captures only the commands); zone headers, per-zone counts and the final
    summary are written to the console via Write-Host and are NOT captured.
    Review the saved file, then run it to perform the migration.

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

$zones = @(Get-DnsServerZone -ComputerName $DnsServer | Where-Object { $_.IsReverseLookupZone -eq $false })

$zoneCount = 0
$recordCount = 0
$skippedCount = 0
$zoneNum = 0

# The cli53 COMMANDS go to the success/output stream (Write-Output) so they can be
# redirected or Tee'd into a runnable file. Progress/headers go to Write-Host, which
# stays on the console and is NOT captured by '>' or Tee-Object — so the saved file
# contains only the commands, not the chatter.

foreach ($zone in $zones) {
    $zoneNum++
    $zoneCount++
    Write-Host -ForegroundColor Green "[$zoneNum/$($zones.Count)] $($zone.ZoneName)"
    Write-Output "$Cli53Path create $($zone.ZoneName) --comment ""Zone Creation $((Get-Date).DateTime)"""
    $records = @(Get-DnsServerResourceRecord -ZoneName $zone.ZoneName -ComputerName $DnsServer)
    $zoneRecords = 0

    foreach ($record in $records) {
        switch ($record.RecordType) {
            "A" {
                Write-Output "$Cli53Path rrcreate $($record.HostName) 600 A $($record.RecordData.IPv4Address.IPAddressToString)"
                $zoneRecords++; $recordCount++
            }
            "MX" {
                Write-Output "$Cli53Path rrcreate $($record.HostName) 600 MX ""$($record.RecordData.Preference) $($record.RecordData.MailExchange)"""
                $zoneRecords++; $recordCount++
            }
            "CNAME" {
                # TTL added for consistency with the other record types.
                Write-Output "$Cli53Path rrcreate $($record.HostName) 600 CNAME $($record.RecordData.HostNameAlias)"
                $zoneRecords++; $recordCount++
            }
            "TXT" {
                # TXT data can contain spaces; quote it so cli53 sees one value.
                Write-Output "$Cli53Path rrcreate $($record.HostName) 600 TXT ""$($record.RecordData.DescriptiveText)"""
                $zoneRecords++; $recordCount++
            }
            "SRV" {
                Write-Output "$Cli53Path rrcreate $($record.HostName) 600 SRV ""$($record.RecordData.Priority) $($record.RecordData.Weight) $($record.RecordData.Port) $($record.RecordData.DomainName)"""
                $zoneRecords++; $recordCount++
            }
            default {
                # NS/SOA and other types aren't migrated by this generator.
                $skippedCount++
            }
        }
    }

    Write-Host -ForegroundColor DarkGray "    -> $zoneRecords record(s) emitted"
}

# --- Summary (console only) ------------------------------------------------
Write-Host ""
Write-Host -ForegroundColor Cyan "Summary:"
Write-Host -ForegroundColor Cyan "  Zones:            $zoneCount"
Write-Host -ForegroundColor Cyan "  Records emitted:  $recordCount"
Write-Host -ForegroundColor Cyan "  Records skipped:  $skippedCount (NS/SOA/other types)"
