# PLM Insight

PLM Insight finds the data problems in a PLM export and shows where engineering changes
wait, so data stewards fix causes, not symptoms.

## What it is for

- **Data quality.** Seven readable rules run on every part master and BOM export: likely
  duplicate parts, missing export classification, released assemblies with In Work children,
  obsolete parts in use, BOM lines pointing to missing parts, unit of measure conflicts, and
  missing attributes. Accepted fixes become a change list for the PLM bulk-update process.
- **Engineering change (ECO) analytics.** Cycle time, where ECOs wait, bottleneck approvers,
  slow-ECO warnings, and why ECOs are sent back.

## Who does what

| Who | Does what |
| --- | --- |
| **Rules (code)** | Find every problem, propose a fix (never an export classification), compute the scorecard and ECO measures, and check everything the AI cites. |
| **The AI** | Reviews duplicate clusters, assembles evidence packets for Trade Compliance, and groups ECO return comments into causes, citing part numbers, fields, values, and quotes. |
| **A data steward (approver)** | Accepts a fix or rejects the finding with a reason, and imports data. Choosing a different fix than the rule proposed needs a reason. |
| **Trade Compliance** | Alone records export classifications and approves merges that would change one. |
| **The audit log** | Records every import, decision, review, export, hand-off, and refused attempt. |

Nothing is written to PLM: the change list goes through your normal PLM bulk-update process.

## A typical session

1. Import the latest export on **Data**.
2. Check the **Scorecard** for the business units and rules that need attention.
3. Work the **Steward queue** (and Trade Compliance its own queue).
4. Download the **Change list** for the PLM team.
5. Open **Engineering changes** to see where ECOs wait and why they come back.

The full rules and thresholds are in the [rules and settings reference](reference).
