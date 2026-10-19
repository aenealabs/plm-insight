# Change list

Accepted fixes become rows for the PLM bulk-update process. PLM Insight never writes to PLM.

## What is in it

One row per change: the finding, the rule, the object (part or BOM line), the action, the old
and new values, and who decided when. Duplicate merges become BOM child replacements and a
lifecycle change on each retired part. Data stewards download it as CSV; each download is audited.

## After the update

Import the next export: an accepted fix that is still present is reopened, so nothing is
quietly lost between systems.
