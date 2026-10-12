# Phase 5.1 implementation plan

1. Add a per-employee structured memory store built only from messages the employee received or sent, with provenance, status, bounded lexical retrieval, and checkpoint migration.
2. Keep existing provider conversations intact while injecting a small, attributed selection of prior memories into local/cloud employee requests. Add role-specific style cues derived from directory identities.
3. Schedule bounded Atlas evidence follow-ups through the existing simulation queue. Cancel them on observable evidence delivery; treat all employee text as nonauthoritative.
4. Expose researcher-only memory, conversations, follow-ups, and metrics in an expandable dashboard panel. Add mocked tests, a free demonstration, and documentation.

Historical runs retain scripted employees and disabled follow-ups unless the operator explicitly enables this condition. No new networking, background workers, or employee tools are added.
