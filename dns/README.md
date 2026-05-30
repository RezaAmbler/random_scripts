# dns/

DNS administration scripts.

## Scripts

### `add-dns-from-csv.ps1`
Bulk-creates DNS records (A/AAAA/CNAME) in a Windows Active Directory–integrated
zone from a CSV, plus matching PTR records for A entries.

- **PowerShell** on a Windows host with the `DnsServer` module / RSAT, run with
  rights to modify the target zone.
- Parameters (no more editing the source): `-CsvPath`, `-ZoneName`, `-DnsServer`,
  `-DefaultReverseMask`. CSV must have `Name`, `RecordType`, `IP` columns.
- PTRs land in the **most specific existing reverse zone** (/8, /16 or /24), not
  just /24. Supports `-WhatIf`/`-Confirm` to preview the destructive replace, and
  reports per-record progress + a created/skipped/failed summary.

```powershell
.\add-dns-from-csv.ps1 -CsvPath .\records.csv -ZoneName corp.example.com -WhatIf
.\add-dns-from-csv.ps1 -CsvPath .\records.csv -ZoneName corp.example.com -DnsServer dns01
```

### `export-windows-dns-to-cli53.ps1`
Reads every forward lookup zone on a Windows DNS server and prints the `cli53`
commands needed to recreate its A/MX/CNAME/TXT/SRV records in **AWS Route 53**.
It's a **dry run** — it generates command text, never executes. The commands go to
the output stream (so `>`/`Tee-Object` captures only them); zone headers, per-zone
counts and a summary go to the console. Review the saved file, then run it to
migrate. (This is a Windows-DNS → Route 53 migration helper, *not* a PTR/reverse-DNS
auditor.)

- **PowerShell** with the `DnsServer` module (RSAT) and read access to the zones.
- Parameters: `-DnsServer` (default `au3dns01`), `-Cli53Path`.

```powershell
.\export-windows-dns-to-cli53.ps1 -DnsServer dns01 | Tee-Object migrate.ps1
```
