# Live smoke revision 2

Prospectively declared worlds 32–33 follow the six preserved pre-provider
configuration failures on 30–31. Only the inconsistent runtime budget check
changed; use the same model, generation settings, scenarios and decision gates.
The frozen drought confirmation failed and is not repeated or reclassified.

Launch only after final source-stable offline checks pass. Use `are-dcore matrix
AAMAS/confirmation_v2/live_no_fault.yaml --shard-count 6 --shard-index I
--output-dir results/aamas_handover/live_no_fault_confirmation_v2/shardI`, for
I=0..5. Hash-based sharding can yield empty or multi-run shards; all six
assignments must be retained. Matched smoke is conditional on all progression
gates. Passing software smoke cannot override the failed drought release gate.
