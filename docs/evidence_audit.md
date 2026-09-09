# Evidence publication audit, 2026-09-06

Scope: fifteen symbol-days on MSFT, INTC and AAPL in 2024. The historical vendor
cell-agreement check covers only two sessions. These are not population claims.

Before implementation, the audit found every generated report CSV was excluded
from Git. The eighty cached aggregate files total 444899 bytes. Their schemas
were inspected: session summaries, distribution distances, intensity bins,
kernel parameters, queue deciles, latency curves and classifier scores. They
contain no event timestamps, order IDs, individual trades or book snapshots.
Only these explicit paths are added to the ignore-rule exceptions.

The publication protocol is fixed before writing the verifier: preserve cached
bytes; record their hashes and row counts; recalculate summary counts with full
precision; report failures; introduce no new tuning, pulls or fitted model.
These are existing outputs first published in this audit. The manifest's
analysis revision identifies the available code, not a newly verified execution
of that code. Exact raw-to-aggregate regeneration was not performed in this
review, and a hash proves integrity, not correctness or run provenance.

The queue-edge mismatch was found during initial inspection and is a correction,
not a preregistered discovery: twelve, not fourteen, absolute differences are
below 0.05 tick. In addition to the large December MSFT difference, October
MSFT and June AAPL exceed that threshold. No confidence or causal claim is made
from fifteen correlated sessions of three stocks.

`python scripts/verify_evidence.py --check` checks source hashes, sample identity
and internal arithmetic, then reproduces the committed claim table. Tests corrupt
an input and alter expected counts to ensure the verifier actually fails.

`report/readme_tables/` archives historical README tables verbatim in CSV form.
These are explicitly marked transcriptions, not independent machine output or
proof of a headline. This is needed for older vendor/performance tables whose
cached aggregate output is unavailable. The modern verifier uses the original
aggregate CSVs instead. Raw-replay correctness cannot be reproduced from these
transcriptions and still requires the licensed feed and documented validators.

The Hawkes excitation matrix is subcritical, but that alone is not proof of
stationarity for the coupled state-dependent queue process. Neither the
retrospective queue buckets nor simulator discriminability measures strategy
P&L. The remaining experiment with the greatest value is a frozen comparison
of event-clock and state corrections on an untouched session, including timing,
book distributions and fill/cost behavior together. No such result is claimed.
