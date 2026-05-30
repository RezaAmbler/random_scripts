# Active Directory DNS Bulk Import
# Bulk-creates A and PTR DNS records in an AD-integrated zone from a CSV file.

# Path to your CSV file
$csvPath = "C:\path\to\your\file.csv"

# Zone name variable
$zoneName = "your.domain.com"

# Import the CSV file
$dnsRecords = Import-Csv -Path $csvPath

# Function to get the reverse zone name and PTR record name
function Get-ReverseInfo {
    param (
        [string]$ipAddress
    )

    $octets = $ipAddress.Split('.')
    if ($octets.Count -eq 4) {
        $reverseZone = "$($octets[2]).$($octets[1]).$($octets[0]).in-addr.arpa"
        $ptrName = $octets[3]
        return @{Zone = $reverseZone; Name = $ptrName}
    } else {
        throw "Invalid IP address format for reverse DNS"
    }
}

# Loop through each record in the CSV file
foreach ($record in $dnsRecords) {
    $name = $record.Name
    $recordType = $record.RecoordType
    $ip = $record.IP

    Write-Host "Processing record: Name=$name, RecordType=$recordType, IP=$ip"

    try {
        # Check if the record already exists
        $existingRecord = Get-DnsServerResourceRecord -ZoneName $zoneName -Name $name -ErrorAction SilentlyContinue

        if ($existingRecord) {
            # If the record exists, remove it
            Remove-DnsServerResourceRecord -ZoneName $zoneName -Name $name -RRType $recordType -Force
        }

        # Add the new DNS record based on RecordType
        if ($recordType -eq "A") {
            Add-DnsServerResourceRecord -ZoneName $zoneName -A -Name $name -IPv4Address $ip
        } elseif ($recordType -eq "AAAA") {
            Add-DnsServerResourceRecord -ZoneName $zoneName -AAAA -Name $name -IPv6Address $ip
        } elseif ($recordType -eq "CNAME") {
            Add-DnsServerResourceRecord -ZoneName $zoneName -CName -Name $name -HostNameAlias $ip
        } else {
            Write-Host "Unsupported record type: $recordType for $name" -ForegroundColor Yellow
            continue
        }

        # Create PTR record for A and AAAA records
        if ($recordType -eq "A" -or $recordType -eq "AAAA") {
            $reverseInfo = Get-ReverseInfo -ipAddress $ip
            $reverseZone = $reverseInfo.Zone
            $ptrName = $reverseInfo.Name

            # Check if the reverse zone exists
            $zoneExists = Get-DnsServerZone -Name $reverseZone -ErrorAction SilentlyContinue
            if (-not $zoneExists) {
                Add-DnsServerPrimaryZone -Name $reverseZone -ZoneFile "$reverseZone.dns"
                Write-Host "Created reverse DNS zone: $reverseZone" -ForegroundColor Green
            }

            # Check if the PTR record already exists
            $ptrRecordExists = Get-DnsServerResourceRecord -ZoneName $reverseZone -Name $ptrName -ErrorAction SilentlyContinue
            if ($ptrRecordExists) {
                Remove-DnsServerResourceRecord -ZoneName $reverseZone -Name $ptrName -RRType PTR -Force
            }

            # Add PTR record
            Add-DnsServerResourceRecordPtr -Name $ptrName -ZoneName $reverseZone -PtrDomainName "$name.$zoneName"
            Write-Host "Added PTR record: $ptrName.$reverseZone -> $name.$zoneName" -ForegroundColor Green
        }

        # Highlight the success message in green
        Write-Host "Successfully updated DNS record for $name : $ip" -ForegroundColor Green
    } catch {
        Write-Host "Failed to update DNS record for $name: $_" -ForegroundColor Red
    }
}
