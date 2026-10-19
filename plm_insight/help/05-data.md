# Data and imports

Import a part master and BOM export, and ECO history. Rules run on import; earlier decisions
carry over.

## Part master and BOM

Upload `parts.csv` and `bom.csv` from your PLM export, name the import, and list the
export-controlled business units. Data stewards can import; every import is audited.

## ECO history

Upload ECOs and their stage events (who held each stage, when, and the outcome and comment
on each return) to refresh the engineering change analytics.

## Where the data comes from

Part master, BOMs, and ECO history come from PLM exports (for example a scheduled report or
the PLM's own export); PLM Insight only reads them. The demo uses a generated, fictional
export with known problems planted in it.
