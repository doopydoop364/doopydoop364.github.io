# OS media inventory

Status (2026-10-26): **drive not inventoried - not mounted.**

`lsblk -f` shows the "OS Palooza" drive as `sdc1` (exFAT, UUID 4E21-0000) with a second
`sdc2` (FAT16, label `VTOYEFI`), so it is a Ventoy stick: ISO/IMG files should be loose
files on the exFAT partition. It has **no mountpoint**; `/mnt/OS Palooza` exists but is an empty
root-owned directory. The mission brief forbids mounting or changing mount configuration
without approval, so nothing was read from it.

To allow read-only inventory, run in the Claude Code prompt:

    ! sudo mount -o ro,uid=$(id -u) /dev/sdc1 "/mnt/OS Palooza"

Nothing before v0.6 (BIOS/boot) depends on this media. Downloaded or copied images belong in
`media/` (git-ignored), never in Git.

## Inventory table (to fill)
| File | Format | Size | OS / version | Arch | Boot type | Use for Scratch86 |
|---|---|---|---|---|---|---|
