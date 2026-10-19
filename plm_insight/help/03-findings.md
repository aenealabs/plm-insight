# Findings and decisions

Every finding shows the rule, the evidence, and a proposed fix. A person decides.

## Working the queue

The **Steward queue** holds every finding except export classification, which goes to the
**Trade Compliance** queue. Open a finding to see the parts involved, where they are used,
and the evidence the rule found.

## Deciding

- **Accept the fix.** For duplicates, choose the part to keep (the rule proposes the one most
  used in BOMs, then released, then oldest); for an obsolete part, the replacement. Choosing a
  different part than the rule proposes needs a reason.
- **Reject the finding** with a reason. A rejected false positive stays rejected in later
  imports, so it is not raised again without a reason.
- **Export classification** is never proposed: only Trade Compliance can record one. A
  duplicate merge between parts with different classifications would silently reclassify
  every assembly that used the retired part, so only Trade Compliance can approve it.

## AI reviews

For a duplicate cluster, **Get AI review** asks whether the parts are really the same and
which to keep. For a missing classification, Trade Compliance can click **Get evidence
packet**. Every part number, field, and value the AI cites is compared with the export, and a
mismatch is flagged in place. Anything that looks like a classification is removed from a
packet, and the removal is shown.

## Parts

A part's page shows its attributes, where it is used, and every finding about it.
