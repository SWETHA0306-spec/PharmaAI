# Drug Authenticity & Interaction AI
### Console-based pipeline — OCR → Resolve → Interactions → Report

---

## Project Structure

```
drug_ai/
├── main.py                  ← Entry point (run this)
├── requirements.txt
├── core/
│   ├── ocr_fixed.py         ← Your original OCR module (unchanged)
│   ├── resolver.py          ← Drug name → canonical identity + authenticity
│   ├── interactions.py      ← DDI check via RxNorm + OpenFDA (3-tier)
│   └── report.py            ← Risk report builder + JSON export
└── reports/                 ← Auto-created, stores exported JSON reports
```

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set your OCR API key
Copy `.env.example` to `.env` and set:
```
OCR_API_KEY=your_openrouter_key_here
```
> Only needed for **image mode**. Text mode uses free public APIs only.

---

## Usage

### Image mode (OCR → full pipeline)
```bash
python main.py --image "C:\path\to\label.jpg"
```

### Text mode (type drug name → full pipeline)
```bash
python main.py --drug "Clobetasol Propionate"
python main.py --drug "metformin"
python main.py --drug "CLONATE"
```

### Interactive mode (prompts you)
```bash
python main.py
```

---

## Pipeline Flow

```
Image path ──→ OCR (2-pass VL model) ──┐
                                        ├──→ Drug Resolver
Drug name (text) ──────────────────────┘        │
                                                 │  RxNorm API  → RxCUI + generic name
                                                 │  PubChem API → CID + SMILES + formula
                                                 │  OpenFDA API → manufacturer + type
                                                 │  Rules       → suspicion score (0–1)
                                                 ↓
                                    Interaction Engine
                                         │
                                         │  Tier 1: RxNorm list API
                                         │  Tier 2: RxNorm pairwise
                                         │  Tier 3: OpenFDA FAERS signals
                                         │  Merge + deduplicate
                                         ↓
                                    Risk Report
                                         │
                                         │  Full console display
                                         │  Combined risk score
                                         └──→ JSON export (optional)
```

---

## APIs Used (All Free, No Key Required)

| API | Purpose |
|-----|---------|
| RxNorm (NLM) | Drug name → RxCUI, generic name, interaction list |
| PubChem (NCBI) | Drug → SMILES, CID, molecular formula |
| OpenFDA | Label data, manufacturer, FAERS adverse event signals |

> OpenRouter key is only needed for the VL model in OCR image mode.

---

## Output

### Console
- Drug identity card (brand, generic, class, formula, route)
- Authenticity verdict with suspicion flags
- Interaction table with severity color codes
- Combined risk score with recommendation

### JSON export (`reports/` folder)
Full structured report with all pipeline data — ready for the future frontend.

---

## Next Steps (Roadmap)

- [ ] GNN drug interaction model (SMILES already resolved in pipeline)
- [ ] CDSCO India drug database integration
- [ ] DrugBank XML local lookup (download from drugbank.com)
- [ ] Frontend web app (identity card + risk charts)
- [ ] Authentication layer

---

## Sample Run

```
python main.py --image label.jpg

  → OCR extracts: CLONATE® | Clobetasol Propionate | 0.05% w/w
  → Resolver: RxCUI 41493 | PubChem CID 5311051 | C25H32ClFO5
  → Authenticity: 🚨 ALERT (40%) — missing batch, exp date, manufacturer
  → Enter other drugs: warfarin, aspirin
  → Interactions: 2 found (1 moderate, 1 minor)
  → Overall risk: MODERATE
  → Export report? y → reports/report_clobetasol_20250101_120000.json
```
