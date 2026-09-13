# Specification authorship and human review

The current study uses **author-defined specifications with actual professor
approval before paper execution**. Authoring a specification does not provide
independent confirmation. No professor approval has been received or generated.
The existing independent-review workflow remains available as a separate route.
The archived neutral review packets and earlier pilot records retain their
original instructions and hashes; they describe the independent route at the
time of their creation.

The shared implementation is `are/simulation/distributed/review_policy.py`.
Runtime, doctor, handoff and saved-trace eligibility use this policy; annotation
selection inherits saved-trace paper eligibility. A frozen authored artifact
has `expert_review_status: author_defined`, `annotation_status: frozen`, a
review-content digest and no `engineering_defaults`. Structural completeness
is checked separately by `FarmProcessSpecV5`. These labels must never be used
to promote an incomplete engineering conversion.

For the author route, the human professor supplies an attestation containing:

```json
{
  "route": "author_defined_professor_approved",
  "approved": false,
  "reviewer_name": "",
  "reviewer_role": "professor",
  "signed_at": "",
  "statement": "",
  "subject_digests": {
    "process": "",
    "team": "",
    "protocol": ""
  }
}
```

This is deliberately **invalid for approval** until the human completes it.
Include `refinement` in `subject_digests` for a refined team. Digests bind the
exact process, team, refinement and analysis protocol. The signature time must
include a timezone. A changed scientific artifact requires another review.
This records a human declaration, not cryptographic identity verification.

Independent review continues to use two separately authored neutral submissions,
adjudication and the existing third-reviewer confirmation tools. Keep that route's
`confirmed` labels and provenance. Do not relabel an authored specification or a
professor's approval as an independently authored specification.

`handoff build --stage review` requires frozen authored specifications and the
engineering evidence, with approval pending. `--stage release` additionally
requires the genuine attestation and scientific release gates. Neither stage is
a way to bypass failed calibration or smoke checks. A development archive that
contains blockers is not a review package or permission to launch the study.

Independent episode annotation is unchanged: 60 episodes, at most two per run,
two independent annotations per episode, and separate adjudication. Specification
approval is not a substitute for those annotations.
