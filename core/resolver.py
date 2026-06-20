"""
resolver.py — Drug Name Resolver
=================================
Converts brand names / generic names / OCR output into a canonical
drug identity: generic name + RxCUI + PubChem CID + SMILES.

Chain:
  Input name
    → clean / normalise
    → RxNorm  (brand → generic + RxCUI)
    → PubChem (generic → CID + SMILES + synonyms)
    → OpenFDA (NDC validation + manufacturer info)

No API keys required. All endpoints are free public APIs.

Install:  pip install requests
"""

import re
import json
import time
import requests
from typing import Optional

# ══════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════

RXNORM_BASE  = "https://rxnav.nlm.nih.gov/REST"
PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
OPENFDA_BASE = "https://api.fda.gov/drug"

TIMEOUT      = 15   # seconds per request
RETRY_DELAY  = 2    # seconds between retries
MAX_RETRIES  = 2


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def _get(url: str, params: dict = None) -> Optional[dict]:
    """Safe GET with retries. Returns parsed JSON or None."""
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


def _clean_name(name: str) -> str:
    """
    Strip common OCR / label suffixes that break API lookups.
    e.g. "CLONATE®" → "CLONATE"
         "Clobetasol Propionate IP" → "Clobetasol Propionate"
         "Metformin HCl 500mg" → "Metformin HCl"
    """
    name = name.strip()
    # Remove registered/trademark symbols
    name = re.sub(r'[®™©]', '', name)
    # Remove pharmacopoeial suffixes (IP, BP, USP, EP, NF)
    name = re.sub(r'\b(IP|BP|USP|EP|NF)\b', '', name, flags=re.I)
    # Remove strength (e.g. "500mg", "0.05% w/w")
    name = re.sub(r'\b\d+(\.\d+)?\s*(%|mg|mcg|g|ml|IU|w/w|w/v|v/v)\b', '', name, flags=re.I)
    # Remove dosage forms
    name = re.sub(r'\b(tablet|capsule|syrup|injection|ointment|cream|gel|lotion|drops|'
                  r'solution|suspension|powder|patch|inhaler|spray)s?\b', '', name, flags=re.I)
    # Collapse whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    return name


# ══════════════════════════════════════════════════════════════
#  RXNORM LAYER
# ══════════════════════════════════════════════════════════════

def _rxnorm_lookup(name: str) -> dict:
    """
    Query RxNorm for:
      - rxcui (standard drug ID)
      - generic name (ingredient)
      - drug class
    Returns dict with keys: rxcui, generic_name, drug_class (may be None).
    """
    result = {"rxcui": None, "rxnorm_generic": None, "drug_class": None}

    # Step 1: find RxCUI by approximate name
    data = _get(f"{RXNORM_BASE}/approximateTerm.json",
                params={"term": name, "maxEntries": 5})
    if not data:
        return result

    candidates = (data.get("approximateGroup", {})
                      .get("candidate", []))
    if not candidates:
        return result

    # Pick the highest-scoring candidate
    best = max(candidates, key=lambda c: float(c.get("score", 0)))
    rxcui = best.get("rxcui")
    if not rxcui:
        return result

    result["rxcui"] = rxcui

    # Step 2: get ingredient (generic) name from RxCUI
    props = _get(f"{RXNORM_BASE}/rxcui/{rxcui}/properties.json")
    if props:
        name_val = (props.get("properties", {}).get("name") or "")
        result["rxnorm_generic"] = name_val

    # Step 3: resolve to base ingredient if this is a branded drug
    related = _get(f"{RXNORM_BASE}/rxcui/{rxcui}/related.json",
                   params={"tty": "IN+PIN+MIN"})
    if related:
        concept_groups = (related.get("relatedGroup", {})
                                 .get("conceptGroup", []))
        for grp in concept_groups:
            concepts = grp.get("conceptProperties", [])
            if concepts:
                result["rxnorm_generic"] = concepts[0].get("name", result["rxnorm_generic"])
                result["rxcui"]          = concepts[0].get("rxcui", rxcui)
                break

    # Step 4: drug class via RxClass
    class_data = _get(f"{RXNORM_BASE}/rxclass/class/byRxcui.json",
                      params={"rxcui": result["rxcui"], "relaSource": "MEDRT"})
    if class_data:
        classes = (class_data.get("rxclassDrugInfoList", {})
                              .get("rxclassDrugInfo", []))
        if classes:
            result["drug_class"] = classes[0].get("rxclassMinConceptItem", {}).get("className")

    return result


# ══════════════════════════════════════════════════════════════
#  PUBCHEM LAYER
# ══════════════════════════════════════════════════════════════

def _pubchem_lookup(name: str) -> dict:
    """
    Query PubChem for:
      - CID (compound ID)
      - canonical SMILES (for GNN later)
      - IUPAC name
      - molecular formula
      - molecular weight
    """
    result = {
        "pubchem_cid": None,
        "smiles":      None,
        "iupac_name":  None,
        "mol_formula": None,
        "mol_weight":  None,
    }

    # Search by name → CID list
    data = _get(f"{PUBCHEM_BASE}/compound/name/{requests.utils.quote(name)}/JSON")
    if not data:
        return result

    compounds = data.get("PC_Compounds", [])
    if not compounds:
        return result

    cid = compounds[0].get("id", {}).get("id", {}).get("cid")
    if not cid:
        return result

    result["pubchem_cid"] = cid

    # Get properties in one call
    props_data = _get(
        f"{PUBCHEM_BASE}/compound/cid/{cid}/property/"
        "CanonicalSMILES,IUPACName,MolecularFormula,MolecularWeight/JSON"
    )
    if props_data:
        props = (props_data.get("PropertyTable", {})
                           .get("Properties", [{}]))[0]
        result["smiles"]      = props.get("CanonicalSMILES")
        result["iupac_name"]  = props.get("IUPACName")
        result["mol_formula"] = props.get("MolecularFormula")
        result["mol_weight"]  = props.get("MolecularWeight")

    return result


# ══════════════════════════════════════════════════════════════
#  OPENFDA LAYER
# ══════════════════════════════════════════════════════════════

def _openfda_lookup(name: str) -> dict:
    """
    Query OpenFDA drug label endpoint for:
      - manufacturer name
      - product type (RX / OTC)
      - route (oral, topical, etc.)
      - warnings summary
      - schedule (controlled substance info)
    """
    result = {
        "manufacturer_fda": None,
        "product_type":     None,
        "route":            None,
        "schedule":         None,
        "fda_warnings":     None,
    }

    data = _get(f"{OPENFDA_BASE}/label.json",
                params={"search": f'openfda.brand_name:"{name}"+OR+openfda.generic_name:"{name}"',
                        "limit": 1})

    if not data:
        # Try looser search
        data = _get(f"{OPENFDA_BASE}/label.json",
                    params={"search": name, "limit": 1})

    if not data:
        return result

    results = data.get("results", [])
    if not results:
        return result

    r = results[0]
    openfda = r.get("openfda", {})

    mfr = openfda.get("manufacturer_name", [])
    result["manufacturer_fda"] = mfr[0] if mfr else None

    ptype = openfda.get("product_type", [])
    result["product_type"] = ptype[0] if ptype else None

    route = openfda.get("route", [])
    result["route"] = route[0] if route else None

    sched = openfda.get("schedule", [])
    result["schedule"] = sched[0] if sched else None

    # Grab first warning sentence for display
    warnings = r.get("warnings", [])
    if warnings:
        first = str(warnings[0])[:200]
        result["fda_warnings"] = first + ("…" if len(str(warnings[0])) > 200 else "")

    return result


# ══════════════════════════════════════════════════════════════
#  COUNTERFEIT RISK SIGNALS
# ══════════════════════════════════════════════════════════════

def _compute_suspicion(ocr_result: dict, fda_info: dict) -> dict:
    """
    Rule-based suspicion scoring from OCR field completeness + FDA mismatch.
    Returns a score 0.0–1.0 and a list of flag messages.

    In text mode (_source_mode = 'text') missing label fields are NOT penalised
    because the user typed a name — there is no physical label to inspect.
    Only image mode has a real label to verify.
    """
    flags       = []
    score       = 0.0
    source_mode = ocr_result.get("_source_mode", "image")

    # Missing critical fields — only meaningful for real label images
    if source_mode == "image":
        critical = ["batch_no", "mfg_date", "exp_date", "manufacturer"]
        missing  = [f for f in critical if not ocr_result.get(f)]
        if missing:
            score += len(missing) * 0.10
            flags.append(f"Missing fields on label: {', '.join(missing)}")

    # Expired drug
    exp = ocr_result.get("exp_date")
    if exp:
        # Simple year check
        years = re.findall(r'20(\d{2})', exp)
        if years:
            yr = int(years[-1])
            import datetime
            current_yr = datetime.datetime.now().year % 100
            if yr < current_yr:
                score += 0.30
                flags.append(f"Drug appears EXPIRED (exp date: {exp})")

    # Schedule H drug — must have prescription warning
    raw = (ocr_result.get("raw_text") or "").upper()
    if "SCHEDULE H" in raw and "NOT TO BE SOLD" not in raw:
        score += 0.15
        flags.append("Schedule H drug missing mandatory 'Not to be sold without prescription' warning")

    # FDA manufacturer mismatch
    if fda_info.get("manufacturer_fda") and ocr_result.get("manufacturer"):
        ocr_mfr = ocr_result["manufacturer"].lower()
        fda_mfr = fda_info["manufacturer_fda"].lower()
        # Check if any word overlaps
        ocr_words = set(re.findall(r'\w+', ocr_mfr))
        fda_words = set(re.findall(r'\w+', fda_mfr))
        if not ocr_words.intersection(fda_words):
            score += 0.20
            flags.append(f"Manufacturer mismatch — label: '{ocr_result['manufacturer']}' | FDA: '{fda_info['manufacturer_fda']}'")

    # No FDA record found at all
    if not fda_info.get("product_type") and not fda_info.get("manufacturer_fda"):
        score += 0.15
        flags.append("Drug not found in FDA database — may be India-only brand (verify with CDSCO)")

    score = min(score, 1.0)
    return {"suspicion_score": round(score, 2), "flags": flags}


# ══════════════════════════════════════════════════════════════
#  MAIN RESOLVER
# ══════════════════════════════════════════════════════════════

def resolve_drug(ocr_result: dict) -> dict:
    """
    Full resolution pipeline.

    Input : OCR result dict (from ocr_fixed.py → run_ocr())
    Output: Enriched drug identity dict

    Steps:
      1. Pick best name from OCR output
      2. Clean / normalise name
      3. RxNorm  → rxcui + generic name
      4. PubChem → CID + SMILES
      5. OpenFDA → manufacturer + product type
      6. Suspicion scoring
    """
    SEP = "=" * 55

    # ── Pick input name ───────────────────────────────────────
    raw_brand   = ocr_result.get("brand_name")   or ""
    raw_generic = ocr_result.get("generic_name") or ""

    # Prefer generic for API lookups (more reliable), use brand as fallback display
    display_name = raw_brand or raw_generic
    lookup_name  = raw_generic or raw_brand

    print(f"\n{SEP}\n  DRUG RESOLVER\n{SEP}")
    print(f"  Input name   : {display_name}")

    cleaned = _clean_name(lookup_name)
    print(f"  Cleaned name : {cleaned}")

    # ── RxNorm ────────────────────────────────────────────────
    print("\n  Step 1 : RxNorm lookup...")
    rxnorm = _rxnorm_lookup(cleaned)
    if rxnorm["rxcui"]:
        print(f"  ✓ RxCUI       : {rxnorm['rxcui']}")
        print(f"  ✓ Generic     : {rxnorm['rxnorm_generic']}")
        print(f"  ✓ Drug class  : {rxnorm['drug_class'] or '—'}")
    else:
        print(f"  ⚠ Not found in RxNorm — continuing with cleaned name")

    # Use RxNorm generic if found, else cleaned name
    canonical_name = rxnorm.get("rxnorm_generic") or cleaned

    # ── PubChem ───────────────────────────────────────────────
    print("\n  Step 2 : PubChem lookup...")
    pubchem = _pubchem_lookup(canonical_name)
    if pubchem["pubchem_cid"]:
        print(f"  ✓ CID         : {pubchem['pubchem_cid']}")
        print(f"  ✓ Formula     : {pubchem['mol_formula'] or '—'}")
        print(f"  ✓ Mol weight  : {pubchem['mol_weight'] or '—'}")
        smiles_preview = (pubchem["smiles"] or "")[:60]
        print(f"  ✓ SMILES      : {smiles_preview}{'…' if len(pubchem['smiles'] or '') > 60 else ''}")
    else:
        print(f"  ⚠ Not found in PubChem")

    # ── OpenFDA ───────────────────────────────────────────────
    print("\n  Step 3 : OpenFDA lookup...")
    # Try brand name first for FDA (more reliable for label lookup)
    fda = _openfda_lookup(_clean_name(raw_brand) if raw_brand else canonical_name)
    if not fda["product_type"]:
        fda = _openfda_lookup(canonical_name)

    if fda["product_type"]:
        print(f"  ✓ Product type: {fda['product_type']}")
        print(f"  ✓ Manufacturer: {fda['manufacturer_fda'] or '—'}")
        print(f"  ✓ Route       : {fda['route'] or '—'}")
        print(f"  ✓ Schedule    : {fda['schedule'] or '—'}")
    else:
        print(f"  ⚠ Not found in FDA label database")

    # ── Suspicion scoring ─────────────────────────────────────
    print("\n  Step 4 : Counterfeit risk assessment...")
    suspicion = _compute_suspicion(ocr_result, fda)
    score     = suspicion["suspicion_score"]

    if score == 0.0:
        verdict = "✅ PASS"
        level   = "LOW"
    elif score < 0.30:
        verdict = "⚠️  WARN"
        level   = "MODERATE"
    else:
        verdict = "🚨 ALERT"
        level   = "HIGH"

    print(f"  Suspicion score : {score:.0%}  →  {level} RISK  {verdict}")
    if suspicion["flags"]:
        for flag in suspicion["flags"]:
            print(f"    ⚑  {flag}")

    # ── Build final result ────────────────────────────────────
    identity = {
        # Names
        "input_name":      display_name,
        "canonical_name":  canonical_name,
        "brand_name":      raw_brand   or None,
        "generic_name":    raw_generic or None,
        # IDs
        "rxcui":           rxnorm["rxcui"],
        "rxnorm_generic":  rxnorm["rxnorm_generic"],
        "pubchem_cid":     pubchem["pubchem_cid"],
        "drug_class":      rxnorm["drug_class"],
        # Molecular
        "smiles":          pubchem["smiles"],
        "iupac_name":      pubchem["iupac_name"],
        "mol_formula":     pubchem["mol_formula"],
        "mol_weight":      pubchem["mol_weight"],
        # FDA info
        "manufacturer_fda": fda["manufacturer_fda"],
        "product_type":    fda["product_type"],
        "route":           fda["route"],
        "schedule":        fda["schedule"],
        "fda_warnings":    fda["fda_warnings"],
        # Authenticity
        "suspicion_score": score,
        "suspicion_level": level,
        "suspicion_flags": suspicion["flags"],
        "authenticity_verdict": verdict,
        # Pass-through OCR fields
        "ocr_dosage":      ocr_result.get("dosage"),
        "ocr_dosage_form": ocr_result.get("dosage_form"),
        "ocr_batch_no":    ocr_result.get("batch_no"),
        "ocr_exp_date":    ocr_result.get("exp_date"),
        "ocr_mfg_date":    ocr_result.get("mfg_date"),
        "ocr_manufacturer":ocr_result.get("manufacturer"),
        "ocr_storage":     ocr_result.get("storage"),
        "detected_language": ocr_result.get("detected_language"),
        "ocr_confidence":    ocr_result.get("ocr_confidence"),
        "composition":       ocr_result.get("composition"),
    }

    print(f"\n  ✓ Resolution complete → canonical: '{canonical_name}'")
    return identity


def print_identity(identity: dict):
    """Pretty-print the resolved drug identity card."""
    SEP = "=" * 55
    print(f"\n{SEP}\n  DRUG IDENTITY CARD\n{SEP}")
    print(f"  Brand name    : {identity.get('brand_name') or '—'}")
    print(f"  Generic name  : {identity.get('canonical_name') or '—'}")
    print(f"  Drug class    : {identity.get('drug_class') or '—'}")
    print(f"  RxCUI         : {identity.get('rxcui') or '—'}")
    print(f"  PubChem CID   : {identity.get('pubchem_cid') or '—'}")
    print(f"  Formula       : {identity.get('mol_formula') or '—'}")
    print(f"  Mol weight    : {identity.get('mol_weight') or '—'} g/mol")
    print(f"  Route         : {identity.get('route') or '—'}")
    print(f"  Product type  : {identity.get('product_type') or '—'}")
    print(f"  Schedule      : {identity.get('schedule') or '—'}")
    print(f"  Authenticity  : {identity.get('authenticity_verdict')} "
          f"(score: {identity.get('suspicion_score', 0):.0%})")
    if identity.get("suspicion_flags"):
        for f in identity["suspicion_flags"]:
            print(f"    ⚑  {f}")
    print()


# ══════════════════════════════════════════════════════════════
#  STANDALONE TEST
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Simulate what OCR would return for CLONATE® ointment
    mock_ocr = {
        "success":      True,
        "brand_name":   "CLONATE®",
        "generic_name": "Clobetasol Propionate IP",
        "dosage":       "0.05% w/w",
        "dosage_form":  "Ointment",
        "batch_no":     None,
        "mfg_date":     None,
        "exp_date":     None,
        "manufacturer": None,
        "license_no":   None,
        "storage":      "Store in a cool, dry place not exceeding 25°C",
        "raw_text":     "Clobetasol Ointment IP\nCLONATE® OINTMENT\nSCHEDULE H PRESC Not to be sold by retail",
    }

    identity = resolve_drug(mock_ocr)
    print_identity(identity)      