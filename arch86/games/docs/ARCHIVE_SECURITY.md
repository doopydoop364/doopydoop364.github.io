# Archive inspection boundary

ZIP, TAR, TAR.GZ, 7z and RAR use the installed system libarchive in an isolated
Python worker. No archive code is executed. Default limits: 384 MiB address space,
30 CPU/wall seconds (plus a short parent shutdown margin), 10,000 entries,
8 GiB per file, 32 GiB expanded total, 1000:1 expansion ratio and 16 MiB listing output. Fail closed on
warnings/corruption/encryption, invalid UTF-8, absolute/drive/traversal paths,
links, devices, duplicate and case-colliding names. Nested archives are listed,
never recursively expanded. Resource limits apply to parsing, not just extraction.

Large solid/compressed archives can exceed the time budget even while listing.
Reports say that inspection failed; they do not claim the game is incompatible.
Installation uses the same validation in a new private staging directory. No
system package is installed automatically. libarchive is an optional prerequisite
for archive operations and is normally supplied by Arch's libarchive package.

The inspected GameVault API has no remote archive-listing/header endpoint. Avoid
downloading a whole game merely to assign a speculative architecture. Optional
future scanner design: an administrator could run this exact bounded worker on a
read-only library mount with no credentials, networking or execution rights and
export JSON keyed by immutable version ID plus archive SHA-256. The client could
import that signed/authenticated sidecar as evidence with provenance. This design
is not deployed and the client does not trust arbitrary remote scanner claims.

Member-only analysis passes --member to the isolated worker, reads only the
selected executable prefix, and stops without visiting later headers. Sequential
or solid archives can require skipping prior headers/data. These results are not
a complete archive listing or integrity/extraction-safety certificate; extraction
always performs its own full validation. CPU/memory/file/size limits are unchanged.
Use `arch-games analyze ARCHIVE --member MEMBER --debug` for credential-redacted
worker status/native errors/tracebacks. SIGKILL does not prove an OOM condition.
See ARCHIVE_ANALYSIS_FIX.md for the native-install import failure and regression.
