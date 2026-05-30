# dns/

DNS administration scripts.

## Scripts

### `add-dns-from-csv.ps1`
Bulk-creates DNS records in a Windows Active Directory–integrated zone from a CSV
file, including forward (A) records and matching reverse (PTR) records.

- **PowerShell** on a Windows host with the `DnsServer` module / RSAT, run with
  rights to modify the target zone.
- Edit the `$csvPath` and `$zoneName` variables at the top before running. The CSV
  is expected to have at least `Name` and IP-address columns.

```powershell
.\add-dns-from-csv.ps1
```

### `export-windows-dns-to-cli53.ps1`
Reads every forward lookup zone on a Windows DNS server and prints the `cli53`
commands needed to recreate its A/MX/CNAME/TXT/SRV records in **AWS Route 53**.
It's a **dry run** — it only prints the commands; redirect the output to a file,
review it, then run it to migrate. (This is a Windows-DNS → Route 53 migration
helper, *not* a PTR/reverse-DNS auditor.)

- **PowerShell** with the `DnsServer` module (RSAT) and read access to the zones.
- Parameters: `-DnsServer` (default `au3dns01`), `-Cli53Path`.

```powershell
.\export-windows-dns-to-cli53.ps1 -DnsServer dns01 | Tee-Object migrate.ps1
```
