# Prospective cached-token settlement amendment

Before any calibration world 20–24 or live world 30–31, new requests may settle
at the provider-reported cache rate for the exact pinned GPT-5.4-mini snapshot.
The standard public endpoint rates were verified on 13 September 2026:
$0.75 input, $0.075 cached input, $4.50 output per million tokens.
Source: https://developers.openai.com/api/docs/models/gpt-5.4-mini

Every reservation still assumes no cache hit. Missing or invalid cached counts
receive no discount; missing or invalid total usage retains the reservation.
Amounts round upward to whole microdollars. New nullable columns record cached
input and settlement basis; all historical charges remain unchanged. The complete
pre-migration ledger is saved as
`results/aamas_handover/spending_before_cached_settlement.sqlite`.

The $40 development, $60 confirmation and $100 total caps are unchanged.
The study already declares 700 calls, 24M team tokens and 4096 maximum output
tokens. The smaller 8M development pilot reached August 12 but did not harvest;
its 441 requests and $6.123678 conservative cost remain unchanged. Final smoke
will use the existing study settings, not revise failed pilot outcomes.
