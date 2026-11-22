# AMD Lemonade Developer Challenge 2026 — Status

_Last verified: 2026-11-22. This document records what is actually confirmed; it does not claim a win._

## Verified status

| Item | Verified fact |
|---|---|
| Latest announced winners | DreamServer, Talos-O, Listenr, LemonAIde, Liberty Mesh |
| SwarmMind selection | **Not among the latest winners.** No selection announced yet. |
| Review process | AMD states there is a review backlog for already-submitted projects |
| Hardware | AMD says about 20 laptops remain |
| Call for projects | AMD explicitly asks for **Omni Model** projects |
| Channel | AMD invites posts in Discord channel `<#1471558634155544637>` |
| Badge | Offered at https://github.com/lemonade-sdk/assets/tree/main/challenge |
| Our submission | Discussion: https://github.com/lemonade-sdk/lemonade/discussions/2792 |
| Our repo | https://github.com/rahulgupta0-dev/swarmmind |

## What the winners have in common (judging-relevant patterns)

These are observable patterns to learn from, not a published rubric and not
verified per-project judging criteria:

- **A distinct, named product identity** — each winner is a recognisable,
  self-contained project rather than a generic demo.
- **A discoverable public footprint** — a named project with a visible presence
  in the AMD challenge channels, so reviewers can find and discuss it.
- **Clear alignment with AMD's stated interests** — AMD is actively asking for
  Omni Model projects, so concrete Omni/multimodal use is a relevant signal.
- **A concrete, demonstrable outcome** — a working thing judges can try, not
  just a design description.

## The exact gap for SwarmMind

SwarmMind is **submitted but not yet selected**. The blocker is not a rejection;
it is the **review backlog** AMD described. Our job is to stay visible and make
the submission easy to review — especially against the Omni emphasis — without
claiming a result that has not happened.

## Top recommended next action (verified 2026-11-22)

**Run `lemonade bench --submit` on a real AMD machine, save the claim link, and
put it in README/blog as the verifiable "deep-dive performance eval" artifact.**
`--submit` is real (PR [#3284](https://github.com/lemonade-sdk/lemonade/pull/3284),
merged 2026-10-28; docs https://lemonade-server.ai/docs/guide/cli/): it uploads
results to the benchmark service and prints a claim link for
bench.lemonade-server.ai. Submission progress goes to stderr, so
`bench --submit --json > results.json` works. **NOT run here** — no AMD hardware
and no Lemonade server — so **no leaderboard claim** is made.

## Action plan

1. **Run `lemonade bench --submit` on real AMD hardware** (top action above) and
   publish the claim link as the auditable performance artifact.
2. **Refresh the demo** — keep the demo video and quick-start current so a
   reviewer can reproduce a run quickly.
3. **Post in the Omni channel** — share a concise Omni-focused summary in
   `<#1471558634155544637>` (vision, Kokoro TTS, read-aloud, local-first).
4. **Add the badge only if selected** — do not display it before selection.
5. **Keep the discussion updated** — add progress and responses to
   discussions/2792 as review resumes.
6. **Publish the eval + benchmark deep-dive** — surface `swarmmind eval`
   results and the cross-backend benchmark write-up (see
   [EVALUATION.md](EVALUATION.md)) so claims are auditable.
7. **Be honest about hardware** — end-to-end hardware validation still
   **requires an AMD machine**; results from non-AMD environments do not prove
   NPU/ROCm behaviour. Say so explicitly rather than implying it was verified.

## Sources

- AMD winners announcement — https://github.com/lemonade-sdk/lemonade/issues/2035
- AMD challenge channel / Discord paste — channel `<#1471558634155544637>`
- Submission discussion — https://github.com/lemonade-sdk/lemonade/discussions/2792
- Challenge badge — https://github.com/lemonade-sdk/assets/tree/main/challenge
- Project repo — https://github.com/rahulgupta0-dev/swarmmind
- `lemonade bench --submit` — PR https://github.com/lemonade-sdk/lemonade/pull/3284; CLI docs https://lemonade-server.ai/docs/guide/cli/
