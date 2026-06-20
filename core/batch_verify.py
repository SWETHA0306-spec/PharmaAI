"""
batch_verify.py — Barcode + Batch/Expiry Authenticity Module
================================================================
NEW MODULE — additive only. Does not modify or depend on changes
to resolver.py, ocr_fixed.py, interactions.py, or report.py.

Adds a second authenticity-check path alongside the existing
label-OCR + database-text-match pipeline:

  Step 1  SCAN     — barcode/QR (image) OR manual batch entry
  Step 2  VERIFY    — decode barcode (GTIN/EAN-13), validate batch
                       number format, check manufacturing/expiry dates
  Step 3  GOV CHECK — attempt CDSCO/DAVA lookup (no public API exists
                       today — see note below). Falls back to an
                       honest "UNAVAILABLE" status rather than
                       pretending to verify against government records.

Usage (standalone):
    python core/batch_verify.py --image path/to/package.jpg
    python core/batch_verify.py --batch "B12345" --mfg 2024-03-01 --exp 2026-03-01

Usage (import):
    from batch_verify import verify_batch
    result = verify_batch(barcode_image="package.jpg",
                           batch_no="B12345",
                           mfg_date="2024-03-01",
                           exp_date="2026-03-01")

Install (only needed for barcode image decoding):
    pip install pyzbar pillow
    # pyzbar also needs the system zbar library:
    #   Ubuntu/Debian: sudo apt-get install libzbar0
    #   Mac:           brew install zbar
    #   Windows:       pip install pyzbar[scripts]  (bundles DLL)

If pyzbar/zbar isn't installed, barcode image decoding is skipped
gracefully — manual batch/expiry entry still works fully.
"""

import re
import datetime
from typing import Optional


# ══════════════════════════════════════════════════════════════
#  OPTIONAL BARCODE DECODING (graceful fallback if lib missing)
# ══════════════════════════════════════════════════════════════

try:
    from pyzbar.pyzbar import decode as _zbar_decode
    from PIL import Image as _PILImage
    _BARCODE_LIB_AVAILABLE = True
except (ImportError, FileNotFoundError, OSError):
    _BARCODE_LIB_AVAILABLE = False


def decode_barcode(image_path: str) -> dict:
    """
    Decode a barcode/QR code from an image file.

    Returns:
        {
          "success": bool,
          "codes": [ { "type": "EAN13"|"QRCODE"|..., "data": str } ],
          "error": str | None
        }
    """
    if not _BARCODE_LIB_AVAILABLE:
        return {
            "success": False,
            "codes": [],
            "error": ("Barcode library not installed. Run: "
                       "pip install pyzbar pillow  (+ system libzbar0). "
                       "Manual batch entry still works without this."),
        }

    try:
        img = _PILImage.open(image_path)
        results = _zbar_decode(img)
        if not results:
            return {"success": False, "codes": [],
                     "error": "No barcode/QR code detected in image."}

        codes = []
        for r in results:
            codes.append({
                "type": r.type,                       # e.g. EAN13, QRCODE, CODE128
                "data": r.data.decode("utf-8", errors="replace"),
            })
        return {"success": True, "codes": codes, "error": None}

    except Exception as e:
        return {"success": False, "codes": [], "error": f"Decode error: {e}"}


def parse_gtin(barcode_data: str) -> dict:
    """
    Parse a GS1 barcode payload (common on pharma packaging).
    Handles both:
      - Plain EAN-13/GTIN-13 numeric strings (e.g. "8901030895555")
      - GS1 Application Identifier strings with batch/expiry embedded
        (e.g. "010890103089555517260301102B12345")
        AI(01)=GTIN, AI(17)=Expiry YYMMDD, AI(10)=Batch/Lot

    Returns whatever fields could be extracted; missing fields are None.
    """
    out = {"gtin": None, "expiry_from_barcode": None, "batch_from_barcode": None}

    if not barcode_data:
        return out

    # Plain numeric GTIN/EAN-13 (no AI structure)
    if re.fullmatch(r"\d{8,14}", barcode_data):
        out["gtin"] = barcode_data
        return out

    # GS1 Application Identifier parsing (basic, common subset)
    # AIs appear as fixed-length blocks: (01)+14digits, (17)+6digits, (10)+variable(up to FNC1/end)
    s = barcode_data
    m = re.match(r"^01(\d{14})", s)
    if m:
        out["gtin"] = m.group(1)
        s = s[len(m.group(0)):]  # consume parsed segment so later AIs don't re-match inside it
    else:
        m = re.search(r"01(\d{14})", barcode_data)
        if m:
            out["gtin"] = m.group(1)

    m = re.match(r"^17(\d{6})", s)  # YYMMDD, fixed length
    if m:
        yy, mm, dd = m.group(1)[0:2], m.group(1)[2:4], m.group(1)[4:6]
        try:
            year = 2000 + int(yy)
            out["expiry_from_barcode"] = f"{year:04d}-{mm}-{dd}"
        except ValueError:
            pass
        s = s[len(m.group(0)):]

    m = re.match(r"^10([A-Za-z0-9\-]+)", s)  # batch/lot — variable length, rest of string
    if m:
        out["batch_from_barcode"] = m.group(1)

    return out


# ══════════════════════════════════════════════════════════════
#  BATCH NUMBER FORMAT VALIDATION
# ══════════════════════════════════════════════════════════════

def validate_batch_format(batch_no: Optional[str]) -> dict:
    """
    Lightweight sanity check on batch number format.
    This is NOT authenticity verification — it just flags
    obviously malformed/missing batch numbers as suspicious.
    """
    if not batch_no or not batch_no.strip():
        return {"valid_format": False, "reason": "Batch number missing."}

    cleaned = batch_no.strip()

    if len(cleaned) < 3:
        return {"valid_format": False, "reason": "Batch number too short to be plausible."}

    if not re.match(r"^[A-Za-z0-9\-/]+$", cleaned):
        return {"valid_format": False, "reason": "Batch number contains unexpected characters."}

    return {"valid_format": True, "reason": None}


# ══════════════════════════════════════════════════════════════
#  DATE CHECKS
# ══════════════════════════════════════════════════════════════

def _parse_date(date_str: Optional[str]) -> Optional[datetime.date]:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%Y", "%Y-%m"):
        try:
            dt = datetime.datetime.strptime(date_str.strip(), fmt)
            return dt.date()
        except ValueError:
            continue
    return None


def check_dates(mfg_date: Optional[str], exp_date: Optional[str]) -> dict:
    """
    Validates manufacturing/expiry date logic.
    Returns flags for: expired, expiring soon, invalid date order,
    or mfg date implausibly old/in the future.
    """
    flags = []
    today = datetime.date.today()

    mfg = _parse_date(mfg_date)
    exp = _parse_date(exp_date)

    status = "OK"

    if exp_date and not exp:
        flags.append(f"Could not parse expiry date: '{exp_date}'")
        status = "UNKNOWN"
    if mfg_date and not mfg:
        flags.append(f"Could not parse manufacturing date: '{mfg_date}'")

    if exp:
        if exp < today:
            flags.append(f"EXPIRED — expiry date {exp.isoformat()} has passed.")
            status = "EXPIRED"
        elif (exp - today).days <= 60:
            flags.append(f"Expiring soon — {(exp - today).days} day(s) remaining.")
            if status == "OK":
                status = "EXPIRING_SOON"

    if mfg and exp and mfg >= exp:
        flags.append("Manufacturing date is on/after expiry date — inconsistent.")
        status = "INCONSISTENT"

    if mfg and mfg > today:
        flags.append("Manufacturing date is in the future — implausible.")
        status = "INCONSISTENT"

    return {
        "status": status,            # OK | EXPIRED | EXPIRING_SOON | INCONSISTENT | UNKNOWN
        "flags": flags,
        "mfg_date_parsed": mfg.isoformat() if mfg else None,
        "exp_date_parsed": exp.isoformat() if exp else None,
    }


# ══════════════════════════════════════════════════════════════
#  GOVERNMENT LOOKUP — REPLACED WITH MEDITRUST VERIFICATION DB
# ══════════════════════════════════════════════════════════════
#
# CHANGE LOG: The earlier version of this module attempted a
# CDSCO/DAVA government API call here. That dependency has been
# REMOVED because CDSCO's DAVA system does not expose a public
# API for third-party real-time batch verification — there was
# never a working endpoint to call.
#
# Replacement architecture (see core/meditrust_db.py):
#
#     QR Scan
#        |
#        v
#     MediTrust Verification Database
#        |
#        v
#     Batch + QR + Scan History Check
#        |
#        v
#     Result: Genuine / Suspicious / High Risk
#
# This function now simply documents that the authoritative check
# lives in meditrust_db.MediTrustDB.verify_qr() — a self-owned
# verification layer PharmaAI/MediTrust controls directly, rather
# than depending on unavailable government infrastructure. Call
# that function (via run_meditrust_mode() in main.py) for any QR
# that's present; this batch_verify.py module remains responsible
# only for barcode decoding + standalone batch/expiry format checks
# when no MediTrust QR is available (e.g. a plain printed batch
# number with no QR code at all).
# ══════════════════════════════════════════════════════════════

def check_government_database(gtin: Optional[str], batch_no: Optional[str]) -> dict:
    """
    Historical note: this used to attempt a CDSCO/DAVA call.
    That path is removed — see comment block above. Use
    meditrust_db.MediTrustDB.verify_qr() instead when a QR_ID
    is available. This stub remains only so older callers don't
    break; it always returns NOT_APPLICABLE rather than faking
    a government-verified result.
    """
    return {
        "status": "NOT_APPLICABLE",
        "message": ("Government CDSCO/DAVA lookup removed — no public API "
                     "exists. Use MediTrust's own verification database "
                     "(core/meditrust_db.py) for QR-based checks instead."),
        "verified": None,
    }


# ══════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT — combines everything into one verdict
# ══════════════════════════════════════════════════════════════

def verify_batch(barcode_image: Optional[str] = None,
                  barcode_data: Optional[str] = None,
                  batch_no: Optional[str] = None,
                  mfg_date: Optional[str] = None,
                  exp_date: Optional[str] = None) -> dict:
    """
    Unified batch/barcode verification.

    Any combination of inputs is accepted:
      - barcode_image : path to a photo containing a barcode/QR
      - barcode_data   : raw decoded barcode string (if already decoded elsewhere)
      - batch_no       : manually entered batch/lot number
      - mfg_date       : manually entered manufacturing date
      - exp_date       : manually entered expiry date

    Returns a structured result dict, safe to merge into the
    existing report.py output (additive — does not replace
    suspicion_score from resolver.py).
    """
    barcode_result = {"success": False, "codes": [], "error": None}
    parsed_gtin_info = {"gtin": None, "expiry_from_barcode": None, "batch_from_barcode": None}

    if barcode_image:
        barcode_result = decode_barcode(barcode_image)
        if barcode_result["success"] and barcode_result["codes"]:
            barcode_data = barcode_result["codes"][0]["data"]

    if barcode_data:
        parsed_gtin_info = parse_gtin(barcode_data)

    # Prefer manually entered batch/expiry, fall back to barcode-embedded values
    effective_batch = batch_no or parsed_gtin_info.get("batch_from_barcode")
    effective_exp   = exp_date or parsed_gtin_info.get("expiry_from_barcode")

    batch_check = validate_batch_format(effective_batch)
    date_check  = check_dates(mfg_date, effective_exp)
    gov_check   = check_government_database(parsed_gtin_info.get("gtin"), effective_batch)

    # Roll up into one verdict
    flags = []
    score = 0.0   # 0 = clean, 1 = maximum suspicion (same scale as resolver.py)

    if not batch_check["valid_format"]:
        flags.append(batch_check["reason"])
        score += 0.25

    flags.extend(date_check["flags"])
    if date_check["status"] == "EXPIRED":
        score += 0.5
    elif date_check["status"] == "INCONSISTENT":
        score += 0.35
    elif date_check["status"] == "EXPIRING_SOON":
        score += 0.1
    elif date_check["status"] == "UNKNOWN":
        score += 0.05

    score = min(score, 1.0)

    if score >= 0.5:
        verdict = "🚨 ALERT"
    elif score >= 0.2:
        verdict = "⚠️ WARNING"
    else:
        verdict = "✅ PASS"

    return {
        "input_summary": {
            "barcode_provided": bool(barcode_image or barcode_data),
            "batch_no_used":    effective_batch,
            "mfg_date_used":    mfg_date,
            "exp_date_used":    effective_exp,
        },
        "barcode_decode":  barcode_result,
        "gtin_info":       parsed_gtin_info,
        "batch_format":    batch_check,
        "date_check":      date_check,
        "government_check": gov_check,
        "verdict":         verdict,
        "score":           round(score, 3),
        "flags":           flags,
    }


# ══════════════════════════════════════════════════════════════
#  CONSOLE PRINTER (matches style of report.py)
# ══════════════════════════════════════════════════════════════

def print_batch_result(result: dict):
    SEP = "─" * 55
    print(f"\n  ┌─ BARCODE / BATCH VERIFICATION {SEP[:22]}┐")

    summary = result["input_summary"]
    print(f"  │  Barcode scanned : {'Yes' if summary['barcode_provided'] else 'No'}")
    print(f"  │  Batch number    : {summary['batch_no_used'] or '—'}")
    print(f"  │  Mfg date        : {summary['mfg_date_used'] or '—'}")
    print(f"  │  Exp date        : {summary['exp_date_used'] or '—'}")

    gtin = result["gtin_info"].get("gtin")
    if gtin:
        print(f"  │  GTIN/Barcode ID : {gtin}")

    print(f"  │")
    print(f"  │  Verdict         : {result['verdict']}")
    print(f"  │  Suspicion score : {result['score']:.0%}")

    if result["flags"]:
        print(f"  │  Flags:")
        for f in result["flags"]:
            print(f"  │    ⚑  {f}")
    else:
        print(f"  │  Flags           : None")

    gov = result["government_check"]
    print(f"  │")
    print(f"  │  Govt CDSCO/DAVA check : {gov['status']} (removed — see MediTrust QR mode)")
    if gov["status"] == "NOT_APPLICABLE":
        print(f"  │    ℹ  {gov['message'][:90]}")

    print(f"  └{'─'*53}┘")


# ══════════════════════════════════════════════════════════════
#  CLI — optional standalone use
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Barcode + Batch/Expiry Verification (standalone test)")
    parser.add_argument("--image", help="Path to image containing a barcode/QR code")
    parser.add_argument("--barcode", help="Raw barcode data string (skip image decode)")
    parser.add_argument("--batch", help="Batch/lot number")
    parser.add_argument("--mfg", help="Manufacturing date e.g. 2024-03-01")
    parser.add_argument("--exp", help="Expiry date e.g. 2026-03-01")
    args = parser.parse_args()

    result = verify_batch(
        barcode_image=args.image,
        barcode_data=args.barcode,
        batch_no=args.batch,
        mfg_date=args.mfg,
        exp_date=args.exp,
    )
    print_batch_result(result)
