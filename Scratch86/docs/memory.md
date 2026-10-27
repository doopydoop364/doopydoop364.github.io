# Memory

* Physical RAM: 17 lists `RAM_00..RAM_16`, 64 KiB each, one byte per item (1 MiB + 64 KiB HMA window). Banks are 64 KiB because scratch-vm caps appended lists at 200000 items, and it matches a real-mode segment.
* Path: linear -> **paging** (when `CR0.PG`) -> A20 mask (`A20 gate enabled`=0 wraps at 1 MiB) -> `bank = phys div 65536` -> `RAM_bank[off+1]`.
* Reads beyond the installed banks return all ones; writes are ignored. There is no ROM yet (all RAM is writable).
* API (Scratch procedures): `mem_rb/rw/rd(lin)` -> `MEMV`, `mem_wb/ww/wd(lin,val)`, `mem_fb(lin)` (instruction fetch, never recorded in data-access diagnostics). Little-endian; unaligned, bank-crossing and page-crossing accesses are assembled byte-wise. Raw physical dword access for page tables: `mem_prd`, `mem_pwd`.
* Diagnostics (`diag: record memory accesses`, default off): last read/write linear + physical address, value, width. Logical (seg:off) last effective address is also recorded.
* RAM lists are stored zero-filled in project.json (about 1.1 M items; compresses well). Larger configurations (2-128 MiB) need runtime initialisation or sparse pages - **not done yet** (v0.4 remaining).
* Text video memory is read directly from `RAM_11` (B8000) (physical; paging does not apply to the display).

## Paging (386 two-level, 4 KiB pages)
* `page_xlate(lin, wr)` -> `PLIN` (physical, before the A20 mask) or a pending #PF. 64-entry direct-mapped TLB (`TLB_TAG/PHYS/FLAGS`); a hit costs one list read plus permission checks.
* Walk: CR3 -> PDE -> PTE (present checks), effective R/W and U/S are the AND of both levels, `CR0.WP` makes supervisor writes honour read-only pages (486 profile and later: the 386 profile has no WP bit), accessed bit set in PDE and PTE, dirty bit on the first write (a write that hits a clean TLB entry re-walks to set D).
* #PF: CR2 = faulting linear address (for an access straddling a page boundary, the first byte on the faulting page), error code = P | W<<1 | U<<2. User = CPL 3, except implicit supervisor accesses (descriptor tables, TSS, IDT, exception frames) which are always supervisor (`SUPACC`).
* Page-crossing writes probe both pages before writing anything, so a fault leaves memory untouched.
* The TLB is flushed by `MOV CR3`, any CR0 write that goes through `cr0_changed`, and reset; `INVLPG` (486+) invalidates one entry. Failed walks are not cached.
* Not implemented: PSE (4 MiB pages), global pages, PAE, CR0.PG interaction with the A20 gate beyond the plain mask, #PF on instruction fetch is raised like a data read (no I/D bit on 386/486).
