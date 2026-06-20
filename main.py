"""
main.py — Drug AI Console Application
========================================
Full pipeline entry point. Supports two input modes:

  Mode 1 (IMAGE):  python main.py --image path/to/label.jpg
  Mode 2 (TEXT):   python main.py --drug "Drug Name"
  Interactive:     python main.py   (prompts for input)

Pipeline:
  Input (image/text)
    → OCR (image mode only)
    → Drug Resolver   → canonical identity + authenticity score
    → Interaction Engine → DDI risk from RxNorm + OpenFDA
    → Risk Report     → console display + JSON export

Install all deps:
  pip install pillow requests opencv-python

No API keys needed except OpenRouter (for OCR image mode only).
Set your key in core/ocr_fixed.py  →  API_KEY = "your_key_here"
"""

import sys
import sys
if hasattr(sys.stdout, 'reconfigure'):
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

import os
import argparse
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

# ── Add core/ to path ─────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "core"))

from resolver      import resolve_drug, print_identity
from interactions  import check_interactions, print_interactions
from report        import build_report, print_report, export_report
from batch_verify  import verify_batch, print_batch_result   # NEW — additive module
from meditrust_db  import MediTrustDB, print_meditrust_result  # NEW — MediTrust DB layer


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def _banner():
    print("""
╔═══════════════════════════════════════════════════════╗
║           DRUG AUTHENTICITY & INTERACTION AI          ║
║         Counterfeit Detection + DDI Analysis          ║
╚═══════════════════════════════════════════════════════╝""")


def _ask_other_drugs() -> list[str]:
    """Prompt user for other medications."""
    print("\n  Enter other medications you are currently taking.")
    print("  Separate multiple drugs with commas.")
    print("  Press ENTER to skip.\n")
    raw = input("  Other medications: ").strip()
    if not raw:
        return []
    return [d.strip() for d in raw.split(",") if d.strip()]


def _export_prompt(report: dict):
    """Ask user if they want to export the JSON report."""
    ans = input("\n  Export full report to JSON? [y/N]: ").strip().lower()
    if ans == "y":
        path = export_report(report, output_dir="reports")
        print(f"\n  📄 Report saved → {path}")


# ══════════════════════════════════════════════════════════════
#  IMAGE MODE
# ══════════════════════════════════════════════════════════════

def run_image_mode(image_path: str):
    """Full pipeline starting from a drug label image."""
    from ocr_fixed import run_ocr, print_result

    # Step 1: OCR
    ocr_result = run_ocr(image_path)
    print_result(ocr_result, image_path)

    if not ocr_result.get("success"):
        print("\n  ✗ OCR failed. Cannot continue.\n")
        sys.exit(1)

    ocr_result["_source_mode"] = "image"   # real label — enable full suspicion checks

    # Step 2: Resolve
    identity = resolve_drug(ocr_result)
    print_identity(identity)

    # Step 3: Get other drugs from user
    other_drugs = _ask_other_drugs()

    # Step 4: Interaction check
    interaction_result = check_interactions(identity, other_drugs)
    print_interactions(interaction_result)

    # Step 5: Final report
    report = build_report(identity, interaction_result, source_mode="image")
    print_report(report)

    # Step 6: Export option
    _export_prompt(report)


# ══════════════════════════════════════════════════════════════
#  TEXT MODE
# ══════════════════════════════════════════════════════════════

def run_text_mode(drug_name: str):
    """Full pipeline starting from a typed drug name (no OCR)."""

    # Build a minimal OCR-like dict for the resolver
    mock_ocr = {
        "success":       True,
        "_source_mode":  "text",       # tells resolver: no physical label to inspect
        "brand_name":    drug_name,
        "generic_name":  drug_name,
        "dosage":        None,
        "dosage_form":   None,
        "batch_no":      None,
        "mfg_date":      None,
        "exp_date":      None,
        "manufacturer":  None,
        "license_no":    None,
        "storage":       None,
        "raw_text":      drug_name,
    }

    print(f"\n  Input mode : TEXT")
    print(f"  Drug name  : {drug_name}")

    # Step 1: Resolve
    identity = resolve_drug(mock_ocr)
    print_identity(identity)

    # Step 2: Get other drugs
    other_drugs = _ask_other_drugs()

    # Step 3: Interaction check
    interaction_result = check_interactions(identity, other_drugs)
    print_interactions(interaction_result)

    # Step 4: Final report
    report = build_report(identity, interaction_result, source_mode="text")
    print_report(report)

    # Step 5: Export option
    _export_prompt(report)


# ══════════════════════════════════════════════════════════════
#  BARCODE / BATCH MODE  (NEW — additive, does not touch image/text modes)
# ══════════════════════════════════════════════════════════════

def run_barcode_mode(image_path: str = None,
                      batch_no: str = None,
                      mfg_date: str = None,
                      exp_date: str = None):
    """
    New verification path: barcode/QR scan + batch number + expiry date.
    Runs independently of the OCR label pipeline — can be used alone,
    or combined with image/text mode results by a caller (e.g. api_bridge.py).
    """
    print(f"\n  Input mode : BARCODE / BATCH VERIFICATION")

    result = verify_batch(
        barcode_image=image_path,
        batch_no=batch_no,
        mfg_date=mfg_date,
        exp_date=exp_date,
    )
    print_batch_result(result)
    return result


def _ask_barcode_inputs():
    """Interactive prompt helper for barcode mode."""
    print("\n  Barcode/QR image path (press ENTER to skip and enter manually):")
    img = input("  Image path: ").strip().strip('"').strip("'")
    img = img if img and os.path.exists(img) else None

    batch = input("  Batch number (ENTER to skip): ").strip() or None
    mfg   = input("  Manufacturing date e.g. 2024-03-01 (ENTER to skip): ").strip() or None
    exp   = input("  Expiry date e.g. 2026-03-01 (ENTER to skip): ").strip() or None

    return img, batch, mfg, exp


# ══════════════════════════════════════════════════════════════
#  MEDITRUST QR VERIFICATION MODE  (NEW — additive)
# ══════════════════════════════════════════════════════════════
#
# This replaces the earlier CDSCO/DAVA API attempt (no public
# endpoint exists) with PharmaAI's own MediTrust verification
# database — a self-owned QR + batch + scan-history check.
#
# This mode is independent of the existing counterfeit-detection
# image module (run_image_mode) and the drug-interaction module
# (interactions.py) — neither of those is modified. This is simply
# an additional verification layer a pharmacist/patient can use
# when the medicine has a MediTrust QR code on its packaging.
# ══════════════════════════════════════════════════════════════

_meditrust_db_singleton = None


def _get_meditrust_db() -> MediTrustDB:
    """Lazy-load the MediTrust DB once per process (CSV has 10,500 rows)."""
    global _meditrust_db_singleton
    if _meditrust_db_singleton is None:
        _meditrust_db_singleton = MediTrustDB()
    return _meditrust_db_singleton


def run_meditrust_mode(qr_id: str, scan_location: str = None):
    """
    QR-based verification against the MediTrust Secure Verification
    Database (CSV-backed for now; same interface works with a real
    DB later). Checks: QR existence, batch/expiry/manufacturer
    validity, and duplicate-QR / cross-location scan anomalies.
    """
    print(f"\n  Input mode : MEDITRUST QR VERIFICATION")
    db = _get_meditrust_db()
    result = db.verify_qr(qr_id, scan_location=scan_location)
    print_meditrust_result(result)
    return result


def _ask_meditrust_inputs():
    """Interactive prompt helper for MediTrust QR mode."""
    qr = input("\n  QR ID (e.g. MT100001): ").strip()
    loc = input("  Scan location, e.g. Chennai (ENTER to skip): ").strip() or None
    return qr, loc




def run_interactive():
    """Prompt user to choose input mode interactively."""
    print("\n  How would you like to input the drug?")
    print("  [1] Upload / provide an image path")
    print("  [2] Type the drug name manually")
    print("  [3] Barcode / QR scan + batch number + expiry check (NEW)")
    print("  [4] MediTrust QR verification — database + scan history (NEW)")
    print()

    while True:
        choice = input("  Enter choice [1/2/3/4]: ").strip()
        if choice == "1":
            path = input("  Image path: ").strip().strip('"').strip("'")
            if not os.path.exists(path):
                print(f"  ✗ File not found: {path}")
                continue
            run_image_mode(path)
            break
        elif choice == "2":
            name = input("  Drug name: ").strip()
            if not name:
                print("  ✗ Please enter a drug name.")
                continue
            run_text_mode(name)
            break
        elif choice == "3":
            img, batch, mfg, exp = _ask_barcode_inputs()
            run_barcode_mode(image_path=img, batch_no=batch, mfg_date=mfg, exp_date=exp)
            break
        elif choice == "4":
            qr, loc = _ask_meditrust_inputs()
            if not qr:
                print("  ✗ Please enter a QR ID.")
                continue
            run_meditrust_mode(qr_id=qr, scan_location=loc)
            break
        else:
            print("  Please enter 1, 2, 3 or 4.")


# ══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════

def main():
    _banner()

    parser = argparse.ArgumentParser(
        description="Drug Authenticity & Interaction Checker",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--image", metavar="PATH",
                        help="Path to drug label image (activates OCR mode)")
    parser.add_argument("--drug",  metavar="NAME",
                        help='Drug name in quotes e.g. --drug "metformin"')
    parser.add_argument("--barcode-image", metavar="PATH",
                        help="Path to image containing a barcode/QR code (NEW)")
    parser.add_argument("--batch", metavar="BATCH_NO",
                        help="Batch/lot number for verification (NEW)")
    parser.add_argument("--mfg", metavar="DATE",
                        help="Manufacturing date e.g. 2024-03-01 (NEW)")
    parser.add_argument("--exp", metavar="DATE",
                        help="Expiry date e.g. 2026-03-01 (NEW)")
    parser.add_argument("--meditrust-qr", metavar="QR_ID",
                        help='MediTrust QR ID to verify, e.g. --meditrust-qr "MT100001" (NEW)')
    parser.add_argument("--location", metavar="LOCATION",
                        help="Scan location for MediTrust QR check, e.g. Chennai (NEW)")

    args = parser.parse_args()

    if args.image:
        if not os.path.exists(args.image):
            print(f"\n  ✗ Image not found: {args.image}\n")
            sys.exit(1)
        run_image_mode(args.image)

    elif args.drug:
        run_text_mode(args.drug)

    elif args.meditrust_qr:
        run_meditrust_mode(qr_id=args.meditrust_qr, scan_location=args.location)

    elif args.barcode_image or args.batch or args.mfg or args.exp:
        run_barcode_mode(image_path=args.barcode_image, batch_no=args.batch,
                          mfg_date=args.mfg, exp_date=args.exp)

    else:
        run_interactive()


if __name__ == "__main__":
    main()