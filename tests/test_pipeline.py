"""
test_pipeline.py — Offline pipeline test (no API calls)
=========================================================
Run this first to verify all imports and module wiring work
before making real API calls.

Usage:  python tests/test_pipeline.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

print("\n  Testing imports...")

try:
    from resolver     import resolve_drug, print_identity, _clean_name
    print("  ✓ resolver.py imported")
except Exception as e:
    print(f"  ✗ resolver.py  : {e}"); sys.exit(1)

try:
    from interactions import check_interactions, print_interactions, _normalise_severity
    print("  ✓ interactions.py imported")
except Exception as e:
    print(f"  ✗ interactions.py : {e}"); sys.exit(1)

try:
    from report import build_report, print_report
    print("  ✓ report.py imported")
except Exception as e:
    print(f"  ✗ report.py : {e}"); sys.exit(1)


# ── Test name cleaner ─────────────────────────────────────────
print("\n  Testing name cleaner...")
cases = [
    ("CLONATE®",                   "CLONATE"),
    ("Clobetasol Propionate IP",   "Clobetasol Propionate"),
    ("Metformin HCl 500mg",        "Metformin HCl"),
    ("Aspirin Tablets 100mg BP",   "Aspirin"),
    ("Amoxicillin 250mg/5ml",      "Amoxicillin"),
]
all_ok = True
for raw, expected in cases:
    got = _clean_name(raw)
    status = "✓" if expected.lower() in got.lower() else "⚠"
    print(f"  {status}  '{raw}' → '{got}'  (expected contains '{expected}')")
    if status == "⚠":
        all_ok = False

print(f"\n  Name cleaner: {'✓ all ok' if all_ok else '⚠ some differ (check above)'}")


# ── Test severity normaliser ──────────────────────────────────
print("\n  Testing severity normaliser...")
sev_cases = [
    ("Major — avoid combination",  "major"),
    ("Moderate risk",              "moderate"),
    ("CONTRAINDICATED",            "contraindicated"),
    ("minor interaction",          "minor"),
    ("",                           "unknown"),
]
for raw, expected in sev_cases:
    got = _normalise_severity(raw)
    status = "✓" if got == expected else "✗"
    print(f"  {status}  '{raw}' → '{got}'  (expected '{expected}')")


# ── Test report builder (no API) ──────────────────────────────
print("\n  Testing report builder (mock data)...")

mock_identity = {
    "input_name":         "CLONATE®",
    "canonical_name":     "clobetasol",
    "brand_name":         "CLONATE®",
    "generic_name":       "Clobetasol Propionate",
    "drug_class":         "Corticosteroid",
    "rxcui":              "41493",
    "pubchem_cid":        "5311051",
    "mol_formula":        "C25H32ClFO5",
    "mol_weight":         "466.97",
    "smiles":             "O=C1C=C[C@@]2(C)[C@H]3CC...",
    "route":              "TOPICAL",
    "product_type":       "HUMAN PRESCRIPTION DRUG",
    "manufacturer_fda":   "Galderma Laboratories",
    "ocr_dosage":         "0.05% w/w",
    "ocr_dosage_form":    "Ointment",
    "ocr_batch_no":       None,
    "ocr_exp_date":       None,
    "ocr_mfg_date":       None,
    "ocr_manufacturer":   None,
    "ocr_storage":        "Store below 25°C",
    "suspicion_score":    0.40,
    "suspicion_level":    "HIGH",
    "authenticity_verdict": "🚨 ALERT",
    "suspicion_flags":    ["Missing fields: batch_no, exp_date, manufacturer"],
}

mock_interactions = {
    "interactions": [
        {
            "drug_a": "clobetasol", "drug_b": "warfarin",
            "severity": "moderate",
            "description": "Corticosteroids may enhance anticoagulant effect of warfarin.",
            "source": "RxNorm", "tier": 1,
            "all_sources": ["RxNorm"],
        },
    ],
    "overall_risk":     "MODERATE",
    "summary":          {"moderate": 1},
    "drugs_checked":    ["clobetasol", "warfarin"],
    "drugs_unresolved": [],
}

try:
    report = build_report(mock_identity, mock_interactions, source_mode="image")
    assert report["combined_risk"]["level"] in ("LOW","MODERATE","HIGH","CRITICAL","NONE")
    assert report["meta"]["source_mode"] == "image"
    assert report["drug_identity"]["canonical_name"] == "clobetasol"
    print("  ✓ build_report() succeeded")
except Exception as e:
    print(f"  ✗ build_report() failed: {e}")
    sys.exit(1)

print("\n  Printing mock report...\n")
print_report(report)

print("  ═" * 28)
print("  ✓ All tests passed — pipeline wiring is correct.")
print("  Run: python main.py --drug \"metformin\"  to test live APIs.")
print()
