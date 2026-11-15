# CGOS Research Ledger

Use experiments/ledger/ for durable records authored alongside the code. Assign each experiment a stable ID and UTC date. Store text records and small sanitized summaries in Git. Keep raw traces and large artifacts in versioned storage keyed by SHA-256, with a reference, retention policy and access owner in the record. Keep existing Phase 0, 1A and 1B evidence in place and link it from new records. Corrections append a dated entry or superseding record and preserve the original decision.

Each record contains the following fields.

| Field | Required content |
| --- | --- |
| Identity and provenance | Experiment ID, author, time, status, source revision, build manifest and hardware/environment |
| Observed limitation | Affected component, reproduction, impact and hypothesis |
| Baseline | Dataset or fixture digest, measurement commands, environment, repetitions, raw evidence and uncertainty |
| Proposed improvement | Architectural change, expected gain, acceptance threshold and rollback |
| Experimental implementation | Patch or revision, instrumentation and deviations from the proposed procedure |
| Comparative results | Baseline and candidate under matched conditions, metrics with units and variability, failures and evidence hashes |
| Regression and security impact | Compatibility, authorization, resource and recovery checks, known gaps |
| Decision | Adopt, revise or reject, with justification, reviewer, date and next action |

The initial workflow is proposed, approved for execution, measured, reviewed, then decided. Human approval applies to scope changes, physical tests and adoption. A future Odysseus client can read permitted evidence and propose experiments, compare measurement summaries and draft ledger entries. It receives only gated access to submit approved graphs and cannot approve its own scope or adoption. Keep proposal provenance and reviewer decisions together.
