<#
.SYNOPSIS
    Bulk-create AD-integrated DNS records (A/AAAA/CNAME) and matching PTRs from a CSV.

.DESCRIPTION
    Reads a CSV with Name, RecordType and IP columns and creates the corresponding
    forward records in the given zone. For A/AAAA records it also creates a PTR in
    the most specific existing reverse zone (works for /8, /16 and /24 reverse
    zones — not just /24). Existing records are replaced. Progress is reported per
    record and a summary (created / skipped / failed) is printed at the end.

    Supports -WhatIf and -Confirm so the destructive replace can be previewed.

.PARAMETER CsvPath
    Path to the CSV file. Must have columns: Name, RecordType, IP.
    (For CNAME, the IP column holds the target hostname/alias.)

.PARAMETER ZoneName
    Forward lookup zone to create the records in, e.g. "corp.example.com".

.PARAMETER DnsServer
    DNS server to operate against (default: local machine).

.PARAMETER DefaultReverseMask
    Prefix length (8, 16 or 24) for a reverse zone to CREATE when no existing
    reverse zone covers an IP. Default 24.

.EXAMPLE
    .\add-dns-from-csv.ps1 -CsvPath .\records.csv -ZoneName corp.example.com -WhatIf

.EXAMPLE
    .\add-dns-from-csv.ps1 -CsvPath .\records.csv -ZoneName corp.example.com -DnsServer dns01

.NOTES
    Requires the DnsServer module (RSAT) and rights to modify the zones.
    RFC 2317 classless ( /25 and longer ) reverse delegation is NOT auto-created.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)]
    [string]$CsvPath,

    [Parameter(Mandatory = $true)]
    [string]$ZoneName,

    [string]$DnsServer = $env:COMPUTERNAME,

    [ValidateSet(8, 16, 24)]
    [int]$DefaultReverseMask = 24
)

# --- Validate CSV ---------------------------------------------------------
if (-not (Test-Path -Path $CsvPath)) {
    Write-Error "CSV file not found: $CsvPath"
    exit 1
}

$dnsRecords = Import-Csv -Path $CsvPath
if (-not $dnsRecords) {
    Write-Error "CSV is empty: $CsvPath"
    exit 1
}

$required = @('Name', 'RecordType', 'IP')
$columns = $dnsRecords[0].PSObject.Properties.Name
$missing = $required | Where-Object { $_ -notin $columns }
if ($missing) {
    Write-Error "CSV is missing required column(s): $($missing -join ', '). Found: $($columns -join ', ')"
    exit 1
}

# --- Cache existing reverse zones once -------------------------------------
$reverseZones = @(
    Get-DnsServerZone -ComputerName $DnsServer |
        Where-Object { $_.IsReverseLookupZone } |
        Select-Object -ExpandProperty ZoneName
)

# Find the reverse zone + PTR name for an IPv4 address. Prefers the most
# specific (longest-suffix) existing reverse zone; falls back to a zone at
# $DefaultReverseMask that will be created on demand.
function Get-ReverseInfo {
    param([string]$ipAddress)

    $parsed = [System.Net.IPAddress]::Any
    if (-not [System.Net.IPAddress]::TryParse($ipAddress, [ref]$parsed) -or
        $parsed.AddressFamily -ne 'InterNetwork') {
        throw "Not a valid IPv4 address: $ipAddress"
    }
    $o = $ipAddress.Split('.')

    # Candidate zones from most specific (/24) to least (/8).
    $candidates = @(
        @{ Zone = "$($o[2]).$($o[1]).$($o[0]).in-addr.arpa"; Name = "$($o[3])";              Mask = 24 },
        @{ Zone = "$($o[1]).$($o[0]).in-addr.arpa";          Name = "$($o[3]).$($o[2])";       Mask = 16 },
        @{ Zone = "$($o[0]).in-addr.arpa";                   Name = "$($o[3]).$($o[2]).$($o[1])"; Mask = 8  }
    )

    foreach ($c in $candidates) {
        if ($reverseZones -contains $c.Zone) {
            return @{ Zone = $c.Zone; Name = $c.Name; Existing = $true }
        }
    }

    # None exists — pick the candidate matching the requested default mask.
    $fallback = $candidates | Where-Object { $_.Mask -eq $DefaultReverseMask } | Select-Object -First 1
    return @{ Zone = $fallback.Zone; Name = $fallback.Name; Existing = $false }
}

# --- Process records -------------------------------------------------------
$total = $dnsRecords.Count
$i = 0
$created = 0; $skipped = 0; $failed = 0

foreach ($record in $dnsRecords) {
    $i++
    $name = $record.Name
    $recordType = $record.RecordType    # (was the typo "RecoordType")
    $ip = $record.IP

    Write-Progress -Activity "Importing DNS records into $ZoneName" `
        -Status "[$i/$total] $name ($recordType) -> $ip" `
        -PercentComplete (($i / $total) * 100)

    if ([string]::IsNullOrWhiteSpace($name) -or [string]::IsNullOrWhiteSpace($recordType)) {
        Write-Host "[$i/$total] Skipping row with missing Name/RecordType" -ForegroundColor Yellow
        $skipped++
        continue
    }

    # Note: don't use 'continue' inside a switch here — in PowerShell that
    # continues the switch, not the foreach. Validate the type up front instead.
    if ($recordType -notin @('A', 'AAAA', 'CNAME')) {
        Write-Host "[$i/$total] Unsupported record type: $recordType for $name" -ForegroundColor Yellow
        $skipped++
        continue
    }

    try {
        $target = "$name.$ZoneName"

        # Replace any existing forward record of this type.
        $existing = Get-DnsServerResourceRecord -ZoneName $ZoneName -Name $name -RRType $recordType `
            -ComputerName $DnsServer -ErrorAction SilentlyContinue
        if ($existing -and $PSCmdlet.ShouldProcess($target, "Remove existing $recordType record")) {
            Remove-DnsServerResourceRecord -ZoneName $ZoneName -Name $name -RRType $recordType `
                -ComputerName $DnsServer -Force
        }

        # Create the forward record. $performed stays false under -WhatIf so the
        # counters and status lines reflect what actually happened.
        $performed = $false
        if ($recordType -eq 'A' -and $PSCmdlet.ShouldProcess($target, "Create A -> $ip")) {
            Add-DnsServerResourceRecord -ZoneName $ZoneName -A -Name $name -IPv4Address $ip -ComputerName $DnsServer
            $performed = $true
        }
        elseif ($recordType -eq 'AAAA' -and $PSCmdlet.ShouldProcess($target, "Create AAAA -> $ip")) {
            Add-DnsServerResourceRecord -ZoneName $ZoneName -AAAA -Name $name -IPv6Address $ip -ComputerName $DnsServer
            $performed = $true
        }
        elseif ($recordType -eq 'CNAME' -and $PSCmdlet.ShouldProcess($target, "Create CNAME -> $ip")) {
            Add-DnsServerResourceRecord -ZoneName $ZoneName -CName -Name $name -HostNameAlias $ip -ComputerName $DnsServer
            $performed = $true
        }

        if ($performed) {
            Write-Host "[$i/$total] OK: $recordType $name -> $ip" -ForegroundColor Green
            $created++
        }

        # PTR for A records only (AAAA reverse lives in ip6.arpa — out of scope).
        if ($recordType -eq 'A' -and $performed) {
            $rev = Get-ReverseInfo -ipAddress $ip

            if (-not $rev.Existing) {
                if ($PSCmdlet.ShouldProcess($rev.Zone, "Create reverse zone (/$DefaultReverseMask)")) {
                    Add-DnsServerPrimaryZone -Name $rev.Zone -ZoneFile "$($rev.Zone).dns" -ComputerName $DnsServer
                    $reverseZones += $rev.Zone
                    Write-Host "[$i/$total] Created reverse zone: $($rev.Zone)" -ForegroundColor Green
                }
            }

            $ptrExists = Get-DnsServerResourceRecord -ZoneName $rev.Zone -Name $rev.Name -RRType 'PTR' `
                -ComputerName $DnsServer -ErrorAction SilentlyContinue
            if ($ptrExists -and $PSCmdlet.ShouldProcess("$($rev.Name).$($rev.Zone)", "Remove existing PTR")) {
                Remove-DnsServerResourceRecord -ZoneName $rev.Zone -Name $rev.Name -RRType 'PTR' `
                    -ComputerName $DnsServer -Force
            }

            if ($PSCmdlet.ShouldProcess("$($rev.Name).$($rev.Zone)", "Create PTR -> $target")) {
                Add-DnsServerResourceRecordPtr -Name $rev.Name -ZoneName $rev.Zone `
                    -PtrDomainName $target -ComputerName $DnsServer
                Write-Host "[$i/$total] PTR $($rev.Name).$($rev.Zone) -> $target" -ForegroundColor Green
            }
        }
    }
    catch {
        Write-Host "[$i/$total] FAILED: $name -> $ip : $_" -ForegroundColor Red
        $failed++
    }
}

Write-Progress -Activity "Importing DNS records into $ZoneName" -Completed

# --- Summary ---------------------------------------------------------------
Write-Host ""
Write-Host "Summary:" -ForegroundColor Cyan
Write-Host "  Created/updated: $created" -ForegroundColor Green
Write-Host "  Skipped:         $skipped" -ForegroundColor Yellow
Write-Host "  Failed:          $failed" -ForegroundColor Red
Write-Host "  Total rows:      $total"
