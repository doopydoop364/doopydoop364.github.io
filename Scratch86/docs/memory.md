# Memory

* Physical RAM: 17 lists `RAM_00..RAM_16`, 64 KiB each, one byte per item (1 MiB + 64 KiB HMA window). Banks are 64 KiB because scratch-vm caps appended lists at 200000 items, and it matches a real-mode segment.
* Path: linear -> A20 mask (`A20 gate enabled`=0 wraps at 1 MiB) -> `bank = phys div 65536` -> `RAM_bank[off+1]`. Paging is not implemented (linear == physical).
* Reads beyond the installed banks return all ones; writes are ignored. There is no ROM yet (all RAM is writable).
* API (Scratch procedures): `mem_rb/rw/rd(lin)` -> `MEMV`, `mem_wb/ww/wd(lin,val)`, `mem_fb(lin)` (instruction fetch, never recorded in data-access diagnostics). Little-endian, unaligned and bank-crossing accesses supported.
* Diagnostics (`diag: record memory accesses`, default off): last read/write linear + physical address, value, width. Logical (seg:off) last effective address is also recorded. Fetches, data reads and writes are tracked separately.
* RAM lists are stored zero-filled in project.json (about 1.1 M items; compresses well). Larger configurations (4-64 MiB) need runtime initialisation or sparse pages - not done yet.
* Text video memory is read directly from `RAM_11` (B8000).
