# Independent review packets

The `wetjune/`, `drought/`, and `three_cultivar/` directories were exported with
`are-dcore review export-v5`. Each contains public scenario context, tools,
module skeletons, an unfilled submission template and per-file SHA-256 hashes.
CSV line endings are normalized to LF. The drought and three-cultivar fact
worksheets currently have headers only; experts must define their fact catalogs.
The
templates are deliberately unreviewed; no independent approval is claimed.

Send each relevant packet separately to the domain reviewers. Follow its
`INSTRUCTIONS.md`: preserve independent submissions before adjudication and
confirmation. The sender should disclose which authors have already seen pilot
outcomes; do not ask those authors to attest that they have not seen them.
The neutral packets omit pilot results and proposed thresholds. Reviewers should
also assess whether the world model can represent the claimed agronomic effect,
what constitutes an executable harvest policy, and which parameter alternatives
are defensible. Drafting a specification does not establish simulator validity.

The team review additionally needs explicit agreement on exclusive tool
ownership, time authority, communication topology, resource budgets and shared
task information. Use the existing built-in team as an engineering proposal,
retain all review changes and freeze the approved team artifact separately.

Process/domain review and blinded execution annotation are different tasks.
After release, use the sampling and annotation procedure in
[`PAPER_FOUNDATION.md`](../PAPER_FOUNDATION.md). Engineering-pilot traces are
excluded from that final natural-behavior validation cohort. None of these
packets has been sent or assigned fabricated reviewer identities.
