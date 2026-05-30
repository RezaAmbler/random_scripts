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
