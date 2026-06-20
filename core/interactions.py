"""
interactions.py — Drug-Drug Interaction Engine  v4
====================================================
PRIMARY SOURCE: OpenFDA drug label full text.
Every FDA-approved drug label contains a "Drug Interactions"
section written by the manufacturer. We fetch that section
for every drug and search it for the other drug's name.
No hardcoded tables. No deprecated APIs. Pure database pull.

Sources (all free, no API key):
  1. OpenFDA label  — drug_interactions section (primary)
  2. OpenFDA label  — contraindications section
  3. OpenFDA label  — boxed_warning section
  4. OpenFDA label  — warnings_and_precautions section
  5. OpenFDA FAERS  — real-world adverse event co-reports

RxNorm is used ONLY for drug name normalisation (still alive).
The interaction API (deprecated Jan 2024) is NOT used.

Install: pip install requests
"""

import re
import time
import requests
from typing import Optional

# ══════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════

OPENFDA_LABEL = "https://api.fda.gov/drug/label.json"
OPENFDA_EVENT = "https://api.fda.gov/drug/event.json"
RXNORM_BASE   = "https://rxnav.nlm.nih.gov/REST"

TIMEOUT     = 20
RETRY_DELAY = 2
MAX_RETRIES = 3

SEVERITY_RANK = {
    "contraindicated": 4,
    "major":           3,
    "moderate":        2,
    "minor":           1,
    "unknown":         0,
}

SEVERITY_EMOJI = {
    "contraindicated": "🚫",
    "major":           "🔴",
    "moderate":        "🟡",
    "minor":           "🟢",
    "unknown":         "⚪",
}

# Keywords that indicate severity level inside label text
CONTRAINDICATED_WORDS = [
    "contraindicated", "must not be used", "must not be taken",
    "do not use", "do not administer", "should not be used together",
    "should not be given", "prohibited", "never use", "never administer",
    "avoid concurrent", "avoid concomitant", "absolutely contraindicated",
]
MAJOR_WORDS = [
    "serious", "severe", "life-threatening", "fatal", "death",
    "black box", "boxed warning", "hospitalization", "significant",
    "substantial increase", "substantially", "markedly", "greatly",
    "dangerous", "hazardous", "overdose", "respiratory depression",
    "cardiac arrest", "hypotension", "bleeding", "haemorrhage",
    "rhabdomyolysis", "serotonin syndrome", "malignant", "toxicity",
    "avoid", "caution", "warning",
]
MODERATE_WORDS = [
    "moderate", "caution", "monitor", "careful", "may increase",
    "may decrease", "may affect", "reduced", "elevated", "adjust",
]


# ══════════════════════════════════════════════════════════════
#  HTTP
# ══════════════════════════════════════════════════════════════

def _get(url: str, params: dict = None) -> Optional[dict]:
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                time.sleep(RETRY_DELAY * (attempt + 1))
        except requests.RequestException:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
    return None


# ══════════════════════════════════════════════════════════════
#  DRUG NAME RESOLUTION  (RxNorm — still alive)
# ══════════════════════════════════════════════════════════════

def _resolve_name(name: str) -> str:
    """
    Use RxNorm approximateTerm to get the canonical ingredient name.
    Falls back to the input name if resolution fails.
    RxNorm name lookup is NOT the deprecated interaction API.
    """
    data = _get(f"{RXNORM_BASE}/approximateTerm.json",
                params={"term": name, "maxEntries": 3})
    if not data:
        return name.lower().strip()
    candidates = data.get("approximateGroup", {}).get("candidate", [])
    if not candidates:
        return name.lower().strip()
    best  = max(candidates, key=lambda c: float(c.get("score", 0)))
    rxcui = best.get("rxcui")
    if not rxcui:
        return name.lower().strip()

    # Get ingredient-level name
    info = _get(f"{RXNORM_BASE}/rxcui/{rxcui}/properties.json")
    if info:
        props = info.get("properties", {})
        resolved = props.get("name", "")
        if resolved:
            return resolved.lower().strip()

    return name.lower().strip()


def _resolve_rxcui(drug: dict) -> Optional[str]:
    """Get RxCUI from identity dict."""
    rxcui = drug.get("rxcui")
    if rxcui:
        return str(rxcui)
    name = (drug.get("canonical_name") or drug.get("generic_name")
            or drug.get("input_name", ""))
    if not name:
        return None
    data = _get(f"{RXNORM_BASE}/approximateTerm.json",
                params={"term": name, "maxEntries": 3})
    if not data:
        return None
    candidates = data.get("approximateGroup", {}).get("candidate", [])
    if not candidates:
        return None
    best = max(candidates, key=lambda c: float(c.get("score", 0)))
    return best.get("rxcui")


# ══════════════════════════════════════════════════════════════
#  OPENFDA LABEL FETCHER
# ══════════════════════════════════════════════════════════════

def _fetch_label_sections(drug_name: str) -> dict:
    """
    Fetch the full drug label from OpenFDA for a given drug name.
    Returns dict of section_name → text content.
    Tries both generic and brand name searches.
    """
    sections = {
        "drug_interactions":          "",
        "contraindications":          "",
        "boxed_warning":              "",
        "warnings_and_precautions":   "",
        "warnings":                   "",
    }

    # Try generic name search first, then brand
    for field in ["openfda.generic_name", "openfda.brand_name", "openfda.substance_name"]:
        query = f'{field}:"{drug_name}"'
        data  = _get(OPENFDA_LABEL, params={"search": query, "limit": 1})
        if data and data.get("results"):
            label = data["results"][0]
            for sec in sections:
                raw = label.get(sec, [])
                if isinstance(raw, list) and raw:
                    sections[sec] = " ".join(raw).lower()
                elif isinstance(raw, str):
                    sections[sec] = raw.lower()
            # If we got a drug_interactions section, stop searching
            if sections["drug_interactions"]:
                break

    return sections


def _infer_severity(text_snippet: str) -> str:
    """
    Infer severity from the text surrounding a drug mention in a label.
    Checks for keyword signals in order of severity.
    """
    t = text_snippet.lower()
    for w in CONTRAINDICATED_WORDS:
        if w in t:
            return "contraindicated"
    for w in MAJOR_WORDS:
        if w in t:
            return "major"
    for w in MODERATE_WORDS:
        if w in t:
            return "moderate"
    return "minor"


def _extract_snippet(text: str, drug_b: str, window: int = 400) -> str:
    """
    Extract a text window around the first mention of drug_b in text.
    Returns the surrounding context for severity inference and display.
    """
    idx = text.find(drug_b.lower())
    if idx == -1:
        return ""
    start = max(0, idx - window // 2)
    end   = min(len(text), idx + window // 2)
    snippet = text[start:end].strip()
    # Clean up whitespace
    snippet = re.sub(r'\s+', ' ', snippet)
    return snippet


def _search_label_for_drug(sections: dict,
                            drug_b_names: list,
                            drug_a_display: str,
                            drug_b_display: str) -> list:
    """
    Search all label sections of drug A for any mention of drug B names.
    Returns list of interaction dicts if found.
    """
    found = []
    section_priority = [
        ("boxed_warning",            "contraindicated", "FDA Boxed Warning"),
        ("contraindications",        "contraindicated", "FDA Label Contraindications"),
        ("drug_interactions",        None,              "FDA Drug Interactions Section"),
        ("warnings_and_precautions", None,              "FDA Warnings & Precautions"),
        ("warnings",                 None,              "FDA Warnings"),
    ]

    best_severity  = None
    best_snippet   = ""
    best_source    = ""

    for sec_key, forced_severity, source_label in section_priority:
        text = sections.get(sec_key, "")
        if not text:
            continue

        for b_name in drug_b_names:
            if b_name.lower() in text:
                snippet  = _extract_snippet(text, b_name, window=600)
                severity = forced_severity if forced_severity else _infer_severity(snippet)

                # Keep the most severe finding
                if best_severity is None or \
                   SEVERITY_RANK.get(severity, 0) > SEVERITY_RANK.get(best_severity, 0):
                    best_severity = severity
                    best_snippet  = snippet
                    best_source   = source_label

    if best_severity:
        # Trim snippet to a readable length
        display_snippet = best_snippet[:500] + "..." if len(best_snippet) > 500 else best_snippet
        found.append({
            "drug_a":      drug_a_display,
            "drug_b":      drug_b_display,
            "severity":    best_severity,
            "description": f"[FDA Label — {drug_a_display}] {display_snippet}",
            "source":      f"OpenFDA Label ({best_source})",
            "tier":        1,
        })

    return found


# ══════════════════════════════════════════════════════════════
#  REVERSE LABEL CHECK
# ══════════════════════════════════════════════════════════════

def _check_reverse(drug_b_name: str,
                   drug_a_names: list,
                   drug_a_display: str,
                   drug_b_display: str) -> list:
    """
    Also check drug B's label for mentions of drug A.
    Interactions are often documented in only one direction.
    """
    b_sections = _fetch_label_sections(drug_b_name)
    return _search_label_for_drug(
        b_sections, drug_a_names, drug_b_display, drug_a_display
    )


# ══════════════════════════════════════════════════════════════
#  FAERS SIGNAL CHECK
# ══════════════════════════════════════════════════════════════

def _faers_signal(name_a: str, name_b: str) -> list:
    """
    Check FDA FAERS for serious adverse event co-reports.
    Real-world signal — catches interactions not yet in labels.
    """
    query = (f'patient.drug.medicinalproduct:"{name_a}"'
             f'+AND+patient.drug.medicinalproduct:"{name_b}"'
             f'+AND+serious:1')
    data  = _get(OPENFDA_EVENT,
                 params={"search": query,
                         "count":  "patient.reaction.reactionmeddrapt.exact",
                         "limit":  5})
    if not data:
        return []

    total = int(data.get("meta", {}).get("results", {}).get("total", 0))
    if total < 50:
        return []

    results   = data.get("results", [])
    reactions = [r.get("term", "") for r in results[:4] if r.get("term")]
    severity  = "major" if total > 5000 else "moderate" if total > 500 else "minor"

    return [{
        "drug_a":       name_a,
        "drug_b":       name_b,
        "severity":     severity,
        "description":  (f"FDA FAERS: {total:,} serious adverse event reports "
                         f"co-reporting {name_a} + {name_b}. "
                         f"Top reactions: {', '.join(reactions)}."),
        "source":       "OpenFDA FAERS (real-world signal)",
        "tier":         2,
        "report_count": total,
    }]


# ══════════════════════════════════════════════════════════════
#  MERGE
# ══════════════════════════════════════════════════════════════

def _merge(all_interactions: list) -> list:
    seen = {}
    for item in all_interactions:
        key = frozenset([item["drug_a"].lower(), item["drug_b"].lower()])
        if key not in seen:
            seen[key] = item.copy()
            seen[key]["all_sources"] = [item["source"]]
        else:
            ex = seen[key]
            if SEVERITY_RANK.get(item["severity"], 0) > SEVERITY_RANK.get(ex["severity"], 0):
                seen[key]["severity"]    = item["severity"]
                seen[key]["description"] = item["description"]
            if item["source"] not in seen[key]["all_sources"]:
                seen[key]["all_sources"].append(item["source"])

    merged = list(seen.values())
    merged.sort(key=lambda x: SEVERITY_RANK.get(x["severity"], 0), reverse=True)
    return merged


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def check_interactions(primary_drug: dict, other_drugs: list) -> dict:
    SEP = "=" * 60
    print(f"\n{SEP}")
    print(f"  DRUG INTERACTION ENGINE  v4")
    print(f"  Source: OpenFDA drug label full text (primary)")
    print(f"{SEP}")

    if not other_drugs:
        print("  ⚠  No other drugs provided.")
        return {"interactions": [], "summary": {}, "overall_risk": "NONE",
                "drugs_checked": [], "drugs_unresolved": []}

    primary_name = (primary_drug.get("canonical_name")
                    or primary_drug.get("input_name", "Drug A"))

    # Resolve primary drug canonical name via RxNorm
    print(f"\n  Resolving canonical names via RxNorm...")
    primary_resolved = _resolve_name(primary_name)
    primary_names    = list({primary_name.lower(), primary_resolved})
    print(f"  ✓ {primary_name:30s} → {primary_resolved}")

    # Fetch primary drug label once (reused for all checks)
    print(f"\n  Fetching FDA label for: {primary_resolved}...")
    primary_sections = _fetch_label_sections(primary_resolved)
    has_ddi_section  = bool(primary_sections.get("drug_interactions"))
    has_contra       = bool(primary_sections.get("contraindications"))
    has_boxed        = bool(primary_sections.get("boxed_warning"))
    print(f"  ✓ Label sections found: "
          f"drug_interactions={'YES' if has_ddi_section else 'NO'}  "
          f"contraindications={'YES' if has_contra else 'NO'}  "
          f"boxed_warning={'YES' if has_boxed else 'NO'}")

    all_interactions = []
    unresolved       = []

    for raw in other_drugs:
        other_name     = raw.strip()
        other_resolved = _resolve_name(other_name)
        other_names    = list({other_name.lower(), other_resolved})

        print(f"\n  ─── Checking: {other_name} ({other_resolved}) ───")

        # ── Step 1: Search primary label for other drug ────────────
        print(f"  Step 1: Searching {primary_resolved} label for {other_resolved}...")
        hits = _search_label_for_drug(
            primary_sections, other_names,
            primary_resolved, other_resolved
        )
        if hits:
            print(f"  🔴 FOUND in {primary_resolved} label → {hits[0]['severity'].upper()}")
            all_interactions.extend(hits)
        else:
            print(f"  ○  Not found in {primary_resolved} label")

        # ── Step 2: Reverse check — search other drug's label ──────
        print(f"  Step 2: Searching {other_resolved} label for {primary_resolved}...")
        reverse_hits = _check_reverse(
            other_resolved, primary_names,
            primary_resolved, other_resolved
        )
        if reverse_hits:
            print(f"  🔴 FOUND in {other_resolved} label → {reverse_hits[0]['severity'].upper()}")
            all_interactions.extend(reverse_hits)
        else:
            print(f"  ○  Not found in {other_resolved} label")

        # ── Step 3: FAERS real-world signal ────────────────────────
        print(f"  Step 3: FAERS adverse event signal...")
        faers = _faers_signal(primary_resolved, other_resolved)
        if faers:
            print(f"  📊 FAERS: {faers[0]['report_count']:,} serious co-reports → "
                  f"{faers[0]['severity'].upper()}")
            all_interactions.extend(faers)
        else:
            print(f"  ○  No significant FAERS signal")

        if not hits and not reverse_hits and not faers:
            unresolved.append(other_name)

    merged  = _merge(all_interactions)
    summary = {s: 0 for s in SEVERITY_RANK}
    for item in merged:
        sev = item.get("severity", "unknown")
        summary[sev] = summary.get(sev, 0) + 1

    if   summary.get("contraindicated", 0) > 0: overall_risk = "CRITICAL"
    elif summary.get("major",           0) > 0: overall_risk = "HIGH"
    elif summary.get("moderate",        0) > 0: overall_risk = "MODERATE"
    elif summary.get("minor",           0) > 0: overall_risk = "LOW"
    else:                                        overall_risk = "NONE"

    print(f"\n{SEP}")
    print(f"  Total: {len(merged)} interaction(s) found | Overall risk: {overall_risk}")
    print(f"{SEP}")

    return {
        "interactions":     merged,
        "summary":          summary,
        "overall_risk":     overall_risk,
        "drugs_checked":    [primary_name] + [d.strip() for d in other_drugs],
        "drugs_unresolved": unresolved,
    }


# ══════════════════════════════════════════════════════════════
#  DISPLAY
# ══════════════════════════════════════════════════════════════

def print_interactions(result: dict):
    SEP = "=" * 60
    print(f"\n{SEP}\n  INTERACTION REPORT\n{SEP}")
    print(f"  Drugs : {', '.join(result.get('drugs_checked', [])) or '—'}")

    risk  = result.get("overall_risk", "UNKNOWN")
    emoji = {"CRITICAL": "🚫", "HIGH": "🔴", "MODERATE": "🟡",
             "LOW": "🟢", "NONE": "✅", "UNKNOWN": "⚪"}
    print(f"\n  Overall risk : {emoji.get(risk, '⚪')} {risk}")

    summary = result.get("summary", {})
    parts   = [f"{v} {k}" for k, v in summary.items() if v > 0]
    if parts:
        print(f"  Breakdown    : {' | '.join(parts)}")

    interactions = result.get("interactions", [])
    if not interactions:
        print("\n  ✅ No interactions found in FDA drug labels or FAERS.")
        print(); return

    print(f"\n  {'─' * 60}")
    for i, item in enumerate(interactions, 1):
        sev  = item.get("severity", "unknown")
        em   = SEVERITY_EMOJI.get(sev, "⚪")
        pair = f"{item.get('drug_a', '')} ↔ {item.get('drug_b', '')}"
        print(f"\n  {i}. {em} [{sev.upper()}]  {pair}")
        print(f"     Source: {', '.join(item.get('all_sources', [item.get('source', '')]))}")
        desc = item.get("description", "")
        # Word-wrap description
        words, line = desc.split(), "     "
        for w in words:
            if len(line) + len(w) > 72:
                print(line)
                line = "     " + w + " "
            else:
                line += w + " "
        if line.strip():
            print(line)
    print(f"\n  {'─' * 60}\n")