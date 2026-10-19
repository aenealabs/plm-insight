"""The Help reference: rules, checks, and settings, built from the code that uses them.

Data quality rules come from rules.py and ECO rules from eco.py, so the reference cannot say
something the app does not do. SETTINGS documents every environment setting; a test fails if
the code reads one that is not listed here.
"""

from mission_control import ReferenceSection

from plm_insight.eco import ECO_RULES
from plm_insight.review import REMOVED
from plm_insight.rules import ABBREVIATIONS, RULE_NAMES, RULES

SETTINGS = {
    "PI_DB_PATH": "Where the app keeps its database (SQLite file).",
    "PI_LLM_PROVIDER": "replay (offline demo, recorded reviews), anthropic (Claude API), or "
                       "foundry (Claude on Microsoft Foundry).",
    "PI_MODEL": "The Claude model (or Foundry deployment) for reviews and ECO themes.",
    "PI_RECORD": "true to save live reviews as recordings for the offline demo.",
    "ANTHROPIC_API_KEY": "Claude API key, when PI_LLM_PROVIDER=anthropic.",
    "ANTHROPIC_FOUNDRY_RESOURCE": "The Microsoft Foundry resource, when PI_LLM_PROVIDER=foundry.",
    "ANTHROPIC_FOUNDRY_API_KEY": "Key for Claude on Microsoft Foundry; without it, Azure sign-in "
                                 "(managed identity) is used.",
    "PI_AUTH": "demo (pick a fictional person) or oidc (single sign-on).",
    "PI_OIDC_METADATA_URL": "Your identity provider's OpenID Connect metadata address.",
    "PI_OIDC_CLIENT_ID": "The app registration's client ID.",
    "PI_OIDC_CLIENT_SECRET": "The app registration's client secret.",
    "PI_SESSION_SECRET": "A long random value that signs the sign-in cookie.",
    "PI_SESSION_HTTPS_ONLY": "false only for local testing over plain http.",
    "PI_APPROVER_ROLE": "The app role for data stewards, who decide fixes and import data.",
    "PI_APPROVER_EMAILS": "Comma-separated emails that are data stewards even without the role.",
    "PI_COMPLIANCE_ROLE": "The app role for Trade Compliance, who alone record export "
                          "classifications and approve merges that change one.",
    "PI_BACKLOG_URL": "Backlog Pilot's address, to send prevention work to its inbox.",
    "PI_LEADERSHIP_URL": "Leadership Pilot's address (older single-link menu).",
    "PI_SUITE_TOKEN": "The shared Mission Control token; the same value as BP_SUITE_TOKEN.",
    "PI_PUBLIC_URL": "This app's own address, so items sent to the inbox link back here.",
    "MISSION_CONTROL_APPS": "The suite's apps and addresses, the same in every app.",
}


def reference() -> list[ReferenceSection]:
    return [
        ReferenceSection("Data quality rules", [
            (f"{rule} {RULE_NAMES[rule]}", severity.capitalize(), text + ".")
            for rule, (severity, text) in RULES.items()
        ], columns=("Rule", "Severity", "Flags"),
            intro="The rules run on every import. Each finding shows the rule, its evidence, "
                  "and a proposed fix (except export classification, which is never proposed)."),
        ReferenceSection("Matching duplicates", [
            ("Normalized", "Case, punctuation, word order, and spacing such as 'm8 x 20' vs "
                           "'M8X20' are ignored"),
            ("Abbreviations", ", ".join(f"{k} = {v}" for k, v in ABBREVIATIONS.items()
                                        if k != v)),
            ("Never matched", "Different part types or sizes, or different materials"),
            ("Part to keep", "The one most used in BOMs, then a released one, then the oldest"),
        ], columns=("Rule", "How it works")),
        ReferenceSection("Decisions", [
            ("Reject a finding", "Needs a reason; the rejection carries over to later imports"),
            ("Accept a different fix", "Keeping a different part, or a different replacement, "
                                       "than the rule proposes needs a reason"),
            ("Export classification", "Only Trade Compliance can record one"),
            ("Merge that changes a classification", "Only Trade Compliance can approve it"),
            ("Accepted fix still present", "Reopened on the next import"),
            ("Change list", "Accepted fixes become CSV rows for the PLM bulk-update process; "
                            "nothing is written to PLM"),
        ], columns=("Action", "Rule")),
        ReferenceSection("AI reviews", [
            ("Duplicates", "The AI says whether the parts are the same and which to keep; every "
                           "part number, field, and value it cites is compared with the export"),
            ("Compliance packets", "Evidence for Trade Compliance; anything that looks like a "
                                   f"classification (ECCN, EAR99, USML or ITAR category) is "
                                   f"replaced with {REMOVED} and the removal is shown"),
            ("ECO return causes", "Every cited ECO and quote is checked word for word, each ECO "
                                  "counted at most as often as it was actually returned, and "
                                  "code does all the counting"),
        ], columns=("Review", "What code checks")),
        ReferenceSection("ECO rules", [(k, v + ".") for k, v in ECO_RULES.items()],
                         intro="A bottleneck is a workload question, not a performance verdict."),
        ReferenceSection("Settings", sorted(SETTINGS.items()),
                         columns=("Setting", "What it does"),
                         intro="Environment settings (or a .env file next to the app)."),
    ]
