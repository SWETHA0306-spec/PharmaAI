"""
report.py — Risk Report Generator
===================================
Combines resolver identity + interaction results into a
single structured report. Prints a full console summary
and exports a timestamped JSON file for future frontend use.

No external dependencies beyond stdlib.
"""

import json
import datetime
import os
from typing import Optional

# ══════════════════════════════════════════════════════════════
#  SEVERITY DISPLAY HELPERS
# ══════════════════════════════════════════════════════════════

RISK_EMOJI = {
    "CRITICAL": "🚫",
    "HIGH":     "🔴",
    "MODERATE": "🟡",
    "LOW":      "🟢",
    "NONE":     "✅",
    "UNKNOWN":  "⚪",
}

AUTH_EMOJI = {
    "LOW":      "✅",
    "MODERATE": "⚠️ ",
    "HIGH":     "🚨",
}

SEVERITY_EMOJI = {
    "contraindicated": "🚫",
    "major":           "🔴",
    "moderate":        "🟡",
    "minor":           "🟢",
    "unknown":         "⚪",
}


def _bar(value: float, width: int = 20) -> str:
    """ASCII progress bar for scores."""
    filled = int(round(value * width))
    return "█" * filled + "░" * (width - filled)


def _risk_score_from_interactions(interactions: list) -> float:
    """Convert interaction list to a 0–1 numeric risk score."""
    from interactions import SEVERITY_RANK
    if not interactions:
        return 0.0
    total_weight = sum(SEVERITY_RANK.get(i.get("severity", "unknown"), 0)
                       for i in interactions)
    max_possible = len(interactions) * 4   # max rank is 4 (contraindicated)
    return min(total_weight / max_possible, 1.0) if max_possible > 0 else 0.0


# ══════════════════════════════════════════════════════════════
#  REPORT BUILDER
# ══════════════════════════════════════════════════════════════

def build_report(identity: dict,
                 interaction_result: dict,
                 source_mode: str = "image") -> dict:
    """
    Assemble the final report dict from all pipeline outputs.

    identity           : from resolver.resolve_drug()
    interaction_result : from interactions.check_interactions()
    source_mode        : "image" or "text"
    """
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")

    # Authenticity
    auth_score   = identity.get("suspicion_score", 0.0)
    auth_level   = identity.get("suspicion_level", "UNKNOWN")
    auth_verdict = identity.get("authenticity_verdict", "⚪ UNKNOWN")
    auth_flags   = identity.get("suspicion_flags", [])

    # Interactions
    interactions     = interaction_result.get("interactions", [])
    overall_risk     = interaction_result.get("overall_risk", "UNKNOWN")
    summary          = interaction_result.get("summary", {})
    drugs_checked    = interaction_result.get("drugs_checked", [])
    drugs_unresolved = interaction_result.get("drugs_unresolved", [])

    interaction_score = _risk_score_from_interactions(interactions)

    # Combined risk (weighted: 40% authenticity, 60% interactions)
    combined_score = round(auth_score * 0.4 + interaction_score * 0.6, 3)

    if combined_score >= 0.6:
        combined_risk = "CRITICAL"
    elif combined_score >= 0.35:
        combined_risk = "HIGH"
    elif combined_score >= 0.15:
        combined_risk = "MODERATE"
    elif combined_score > 0.0:
        combined_risk = "LOW"
    else:
        combined_risk = "NONE"

    report = {
        "meta": {
            "timestamp":       timestamp,
            "source_mode":     source_mode,
            "pipeline_version": "1.0.0",
        },
        "drug_identity": {
            "input_name":      identity.get("input_name"),
            "canonical_name":  identity.get("canonical_name"),
            "brand_name":      identity.get("brand_name"),
            "generic_name":    identity.get("generic_name"),
            "drug_class":      identity.get("drug_class"),
            "rxcui":           identity.get("rxcui"),
            "pubchem_cid":     identity.get("pubchem_cid"),
            "mol_formula":     identity.get("mol_formula"),
            "mol_weight":      identity.get("mol_weight"),
            "smiles":          identity.get("smiles"),
            "route":           identity.get("route"),
            "product_type":    identity.get("product_type"),
            "manufacturer":    identity.get("manufacturer_fda") or identity.get("ocr_manufacturer"),
            "dosage":          identity.get("ocr_dosage"),
            "dosage_form":     identity.get("ocr_dosage_form"),
            "batch_no":        identity.get("ocr_batch_no"),
            "exp_date":        identity.get("ocr_exp_date"),
            "storage":         identity.get("ocr_storage"),
        },
        "authenticity": {
            "verdict":         auth_verdict,
            "level":           auth_level,
            "score":           auth_score,
            "flags":           auth_flags,
        },
        "interactions": {
            "overall_risk":    overall_risk,
            "risk_score":      round(interaction_score, 3),
            "summary":         summary,
            "drugs_checked":   drugs_checked,
            "drugs_unresolved": drugs_unresolved,
            "detail":          interactions,
        },
        "combined_risk": {
            "level":           combined_risk,
            "score":           combined_score,
        },
    }

    return report


# ══════════════════════════════════════════════════════════════
#  CONSOLE PRINTER
# ══════════════════════════════════════════════════════════════

def print_report(report: dict):
    """Full console risk report."""
    SEP  = "═" * 55
    SEP2 = "─" * 55

    meta      = report.get("meta", {})
    identity  = report.get("drug_identity", {})
    auth      = report.get("authenticity", {})
    inter     = report.get("interactions", {})
    combined  = report.get("combined_risk", {})

    print(f"\n{SEP}")
    print(f"  FULL RISK REPORT")
    print(f"  Generated : {meta.get('timestamp', '—')}")
    print(f"  Source    : {meta.get('source_mode', '—').upper()} input")
    print(SEP)

    # ── Drug Identity ─────────────────────────────────────────
    print(f"\n  ┌─ DRUG IDENTITY {'─'*37}┐")
    print(f"  │  Brand name   : {identity.get('brand_name') or '—'}")
    print(f"  │  Generic name : {identity.get('canonical_name') or '—'}")
    print(f"  │  Drug class   : {identity.get('drug_class') or '—'}")
    print(f"  │  Formula      : {identity.get('mol_formula') or '—'}")
    print(f"  │  Route        : {identity.get('route') or '—'}")
    print(f"  │  Dosage       : {identity.get('dosage') or '—'}")
    print(f"  │  Dosage form  : {identity.get('dosage_form') or '—'}")
    print(f"  │  Batch No     : {identity.get('batch_no') or '—'}")
    print(f"  │  Exp date     : {identity.get('exp_date') or '—'}")
    print(f"  │  Manufacturer : {identity.get('manufacturer') or '—'}")
    print(f"  └{'─'*53}┘")

    # ── Authenticity ──────────────────────────────────────────
    auth_level  = auth.get("level", "UNKNOWN")
    auth_score  = auth.get("score", 0.0)
    auth_emoji  = AUTH_EMOJI.get(auth_level, "⚪")
    auth_bar    = _bar(auth_score)

    print(f"\n  ┌─ AUTHENTICITY CHECK {'─'*32}┐")
    print(f"  │  Verdict  : {auth.get('verdict', '—')}")
    print(f"  │  Risk     : {auth_emoji} {auth_level}")
    print(f"  │  Score    : [{auth_bar}] {auth_score:.0%}")

    flags = auth.get("flags", [])
    if flags:
        print(f"  │  Flags:")
        for flag in flags:
            print(f"  │    ⚑  {flag}")
    else:
        print(f"  │  Flags   : None — all checks passed")
    print(f"  └{'─'*53}┘")

    # ── Interactions ──────────────────────────────────────────
    int_risk   = inter.get("overall_risk", "UNKNOWN")
    int_score  = inter.get("risk_score", 0.0)
    int_emoji  = RISK_EMOJI.get(int_risk, "⚪")
    int_bar    = _bar(int_score)
    summary    = inter.get("summary", {})
    detail     = inter.get("detail", [])

    print(f"\n  ┌─ INTERACTION RISK {'─'*34}┐")
    print(f"  │  Drugs checked   : {', '.join(inter.get('drugs_checked', [])) or '—'}")

    unresolved = inter.get("drugs_unresolved", [])
    if unresolved:
        print(f"  │  Unresolved      : {', '.join(unresolved)}")

    print(f"  │  Overall risk    : {int_emoji} {int_risk}")
    print(f"  │  Risk score      : [{int_bar}] {int_score:.0%}")
    print(f"  │  Breakdown       :", end="")

    parts = []
    for sev in ["contraindicated", "major", "moderate", "minor"]:
        count = summary.get(sev, 0)
        if count > 0:
            parts.append(f"{count} {sev}")
    print(f" {' | '.join(parts) if parts else 'none'}")

    if detail:
        print(f"  │")
        print(f"  │  {'#':<4} {'Drug Pair':<36} {'Severity'}")
        print(f"  │  {'─'*49}")
        for i, item in enumerate(detail, 1):
            sev   = item.get("severity", "unknown")
            emoji = SEVERITY_EMOJI.get(sev, "⚪")
            pair  = f"{item.get('drug_a','')} ↔ {item.get('drug_b','')}"
            pair  = pair[:35]
            print(f"  │  {i:<4} {pair:<36} {emoji} {sev}")

        # Expand serious ones
        serious = [x for x in detail
                   if x.get("severity") in ("major", "contraindicated")]
        if serious:
            print(f"  │")
            print(f"  │  SERIOUS INTERACTIONS:")
            for item in serious:
                emoji = SEVERITY_EMOJI.get(item["severity"], "⚪")
                print(f"  │")
                print(f"  │  {emoji} {item.get('drug_a','')} ↔ {item.get('drug_b','')}")
                desc = item.get("description", "")
                if desc:
                    # Word wrap at 50 chars
                    words = desc.split()
                    line  = "  │      "
                    for word in words:
                        if len(line) + len(word) > 58:
                            print(line)
                            line = "  │      " + word + " "
                        else:
                            line += word + " "
                    if line.strip():
                        print(line)
    else:
        print(f"  │  No interactions found.")
    print(f"  └{'─'*53}┘")

    # ── Combined Risk ─────────────────────────────────────────
    comb_level  = combined.get("level", "UNKNOWN")
    comb_score  = combined.get("score", 0.0)
    comb_emoji  = RISK_EMOJI.get(comb_level, "⚪")
    comb_bar    = _bar(comb_score)

    print(f"\n  {'═'*55}")
    print(f"  OVERALL RISK ASSESSMENT")
    print(f"  {'═'*55}")
    print(f"  {comb_emoji} RISK LEVEL : {comb_level}")
    print(f"  Combined score : [{comb_bar}] {comb_score:.0%}")
    print(f"  {'═'*55}")

    if comb_level in ("CRITICAL", "HIGH"):
        print(f"\n  ⚠ RECOMMENDATION: Consult a pharmacist or physician")
        print(f"    before taking this medication combination.")
    elif comb_level == "MODERATE":
        print(f"\n  💊 RECOMMENDATION: Use with caution. Monitor for")
        print(f"    adverse effects. Consult a pharmacist if unsure.")
    else:
        print(f"\n  ✅ No major concerns detected. Always follow the")
        print(f"    prescribing physician's instructions.")
    print()


# ══════════════════════════════════════════════════════════════
#  JSON EXPORT
# ══════════════════════════════════════════════════════════════

def export_report(report: dict, output_dir: str = ".") -> str:
    """
    Save report to a timestamped JSON file.
    Returns the file path.
    """
    os.makedirs(output_dir, exist_ok=True)
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = (report.get("drug_identity", {}).get("canonical_name") or "drug")
    name = re.sub(r'[^\w]', '_', name.lower())[:20]
    path = os.path.join(output_dir, f"report_{name}_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return path

import re   # needed for export_report


# ══════════════════════════════════════════════════════════════
#  STANDALONE TEST
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Simulate pipeline outputs
    mock_identity = {
        "input_name":      "CLONATE®",
        "canonical_name":  "clobetasol",
        "brand_name":      "CLONATE®",
        "generic_name":    "Clobetasol Propionate IP",
        "drug_class":      "Corticosteroid",
        "rxcui":           "41493",
        "pubchem_cid":     "5311051",
        "mol_formula":     "C25H32ClFO5",
        "mol_weight":      "466.97",
        "smiles":          "O=C1C=C[C@@]2(C)[C@H]3CC[C@@]4(C)[C@@H](...)",
        "route":           "TOPICAL",
        "product_type":    "HUMAN PRESCRIPTION DRUG",
        "manufacturer_fda": "Galderma Laboratories",
        "ocr_dosage":      "0.05% w/w",
        "ocr_dosage_form": "Ointment",
        "ocr_batch_no":    None,
        "ocr_exp_date":    None,
        "ocr_mfg_date":    None,
        "ocr_manufacturer": None,
        "ocr_storage":     "Store in a cool, dry place not exceeding 25°C",
        "suspicion_score": 0.40,
        "suspicion_level": "HIGH",
        "authenticity_verdict": "🚨 ALERT",
        "suspicion_flags": [
            "Missing fields on label: batch_no, mfg_date, exp_date, manufacturer",
        ],
    }

    mock_interactions = {
        "interactions": [
            {
                "drug_a": "clobetasol", "drug_b": "warfarin",
                "severity": "moderate",
                "description": "Corticosteroids may enhance the anticoagulant effect of warfarin.",
                "source": "RxNorm", "tier": 1,
            },
            {
                "drug_a": "clobetasol", "drug_b": "aspirin",
                "severity": "minor",
                "description": "Concomitant use may slightly increase bleeding risk.",
                "source": "RxNorm (pairwise)", "tier": 2,
            },
        ],
        "overall_risk":     "MODERATE",
        "summary":          {"moderate": 1, "minor": 1},
        "drugs_checked":    ["clobetasol", "warfarin", "aspirin"],
        "drugs_unresolved": [],
    }

    report = build_report(mock_identity, mock_interactions, source_mode="image")
    print_report(report)

    path = export_report(report, output_dir="reports")
    print(f"  📄 Report saved → {path}")
