# PLM Insight

Engineering data quality and engineering change (ECO) analytics for PLM exports. Part of
**Mission Control**, with [Backlog Pilot](https://github.com/aenealabs/backlog-pilot) and
[Leadership Pilot](https://github.com/aenealabs/leadership-pilot): same stack, Brightlayer UI
styling, and governance rules (code finds and counts, a person decides, every decision on
record).

## Quick start

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
.venv/bin/python -m plm_insight.app      # http://127.0.0.1:8020
```

The demo loads a generated, fictional PLM export: about 5,000 parts and 11,000 BOM lines
across four business units, with known problems planted in it, and about 1,400 ECOs over 21
months with stage history and reviewer comments (`scripts/generate_dataset.py`,
`scripts/generate_ecos.py`; seeded, so they are the same everywhere).

## Built-in Help

**Help** in the side menu explains every screen, and the **?** in the app bar opens the
section about the screen you are on. The **rules and settings reference** is generated from
the same constants the rules run on, so it always matches what the app does; tests fail if a
screen has no help section or a setting is undocumented. Every app also serves the shared
*How Mission Control works* guide (from `mission-control-common`).

Across the suite, overriding a proposed score, rating, placement, or fix, rejecting a draft,
or dropping anything the AI drafted needs a reason; rewording a draft keeps the AI's original
beside it. Every refused attempt is audited, including actions tried without signing in and
failed sign-ins.

## What M1 does

- **Seven readable rules** run on every import: likely duplicate parts, missing export
  classification, released assemblies with In Work children, obsolete parts in use, BOM
  lines pointing to missing parts, unit of measure conflicts, and missing required
  attributes. Each finding shows the rule, the evidence, and a proposed fix.
- **Duplicates the way engineers write them:** "HEX BOLT M8X20 SS316" and "Bolt, Hex,
  m8 x 20, Stainless Steel 316" match once abbreviations, case, and word order are
  normalized; a hex nut never matches a flat washer, and different materials never match.
  The part to keep is proposed by rule (most used in BOMs, then released, then oldest).
- **A person decides.** Data stewards accept a fix or reject the finding with a reason.
  Accepted fixes become a **change list** (CSV) for the PLM bulk-update process; the tool
  never writes to PLM.
- **Export classification is never proposed.** Missing classifications go to a Trade
  Compliance queue with the evidence (controlled business unit, controlled assemblies the
  part is used in); only that role can record a classification.
- **Decisions carry over** between imports: a rejected false positive stays rejected, and a
  fix accepted but still present in the next export is reopened.
- **Scorecard** by business unit and rule, a quality score, and a trend across imports.
  On the demo export it also shows the rules measured against the planted problems. That
  proves the rules work as written; real exports will be messier.
- Demo users and OIDC sign-in with data steward and Trade Compliance roles, full audit log
  with search, security headers, phone layout.

## What M2 adds

- **AI reviews, checked by code.** For a duplicate cluster the AI says whether the parts are
  the same and which to keep; for a missing export classification it assembles an evidence
  packet for Trade Compliance. Every point cites part numbers, fields, and values, and code
  compares each one with the export; a mismatch is flagged in place.
- **No classification from the AI.** Anything that looks like an ECCN, EAR99, or a USML or
  ITAR category is removed from a packet, and the removal is shown. Only Trade Compliance
  can request a packet or record a classification.
- **Merges that change a classification need Trade Compliance.** In early recordings the AI
  noticed that some duplicates carry different export classifications (in the demo export,
  48 of 130 clusters), so merging them would silently reclassify every assembly that used
  the retired part. That is now a rule: such a merge can only be approved by Trade Compliance.
- **Prevention, not just cleanup.** A rule that keeps firing can be sent to Backlog Pilot's
  intake inbox as a request for a control in PLM. App switcher across Mission Control.
- Offline demo replays recorded AI reviews (first 10 duplicate clusters, 6 classification
  reviews), recorded through workload identity federation: no API key is stored.

## What M3 adds: ECO analytics

- **Where is my ECO?** Search any ECO and see where it has been: each stage, who held it,
  how long, and every return with the reviewer's words.
- **Computed, not estimated:** cycle time (median and the 80th percentile), time per stage
  by business unit, the share of ECOs returned at least once, and a quarterly trend.
- **Rules with their thresholds shown:** slow-ECO warnings (longer in a stage than 80% of
  past visits took, or older than 80% of released ECOs took) and bottleneck approvers
  (median at least twice the stage median over 10 or more ECOs; a workload question, not a
  performance verdict).
- **Why ECOs are sent back:** the AI groups six months of return comments into causes, with
  quotes and one prevention control each. Code checks every cited ECO and every quote word
  for word, caps each ECO at the number of times it was actually returned, and does all the
  counting. On the demo history the causes match the planted reasons almost exactly
  (affected parts 39%, cost impact 22%, drawings 20%, effectivity 9%, export classification
  6%, change type 4%).
- A cause can be sent to Backlog Pilot's intake inbox as prevention work; ECO history can be
  imported on the Data page.

## Roadmap

- **M1 (done):** rules engine, steward and Trade Compliance queues, change list,
  scorecard, imports.
- **M2 (done):** checked AI reviews, compliance evidence packets, classification conflict
  guard, prevention requests to Backlog Pilot.
- **M3 (done):** ECO analytics: cycle time, where ECOs wait, bottlenecks, slow-ECO
  warnings, why ECOs are sent back.
- **M4 (this release):** Mission Control launcher, dark mode and phone layout checks, import
  history for both data sets.
- **Later:** a read-only summary for Leadership Pilot, so the weekly update can cite the data
  quality score and ECO cycle time.

## Data and branding

All parts, people, and numbers are generated and fictional. The UI uses the open-source
Brightlayer UI color and theme values (BSD-3-Clause); see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). This is an independent project, not affiliated with or endorsed by Eaton.

## License

Released under the [MIT License](LICENSE). Third-party components keep their own licenses; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
