# 💊 AI-Powered Drug Authenticity & Interaction Checker

## 📌 Overview

Counterfeit drugs and unsafe drug combinations pose serious risks to patients worldwide. This project presents an **AI-powered system that detects drug information from medicine packages using OCR and checks potential drug interactions** to ensure patient safety.

The system extracts text from drug images, identifies the medicine name, verifies authenticity indicators, and analyzes possible interactions between multiple drugs.

---

## 🎯 Key Features

✔ **OCR-Based Drug Detection**
Extracts drug names and information from medicine images using Optical Character Recognition.

✔ **Drug Interaction Checker**
Analyzes interactions between multiple medicines and detects potential risks.

✔ **High Accuracy OCR Model**
Achieves **~95% accuracy even in dim or unclear image conditions**.

✔ **User-Friendly Interface**
Simple UI for uploading medicine images and viewing results.

✔ **Severity Detection**
Displays interaction severity levels and possible side effects.

✔ **Backend Interaction Engine**
Cross-references drugs with a trusted dataset to detect conflicts.

---

## 🧠 System Architecture

1️⃣ User uploads medicine image
2️⃣ OCR model extracts drug name
3️⃣ Backend verifies and processes drug data
4️⃣ Interaction engine checks drug combinations
5️⃣ System returns results with severity levels and warnings

---

## 🛠️ Tech Stack

**Frontend**

* HTML
* CSS
* JavaScript

**Backend**

* Python
* Flask

**AI / ML**

* OCR Model (Tesseract / Custom OCR pipeline)

**Database**

* MongoDB

**Other Tools**

* Git & GitHub

---

## 📊 Model Performance

| Model Component       | Accuracy                              |
| --------------------- | ------------------------------------- |
| OCR Detection         | ~95%                                  |
| Drug Name Recognition | High accuracy in low-light conditions |

---

## 📂 Project Structure

```
drug-checker
│
├── frontend
│   ├── index.html
│   ├── style.css
│   └── script.js
│
├── backend
│   ├── app.py
│   ├── interaction_checker.py
│   └── ocr_model.py
│
├── dataset
│
└── README.md
```

---

## ⚙️ Installation & Setup

### 1️⃣ Clone Repository

```bash
git clone https://github.com/yourusername/drug-checker.git
```

### 2️⃣ Install Dependencies

```bash
pip install -r requirements.txt
```

### 3️⃣ Run Backend

```bash
python app.py
```

###  Open Frontend

Open `index.html` in the browser.

---

## Prototype Demo

Upload a medicine image and the system will:

* Extract the drug name
* Verify drug details
* Detect possible drug interactions
* Display warnings if risks exist

---

## Future Improvements

* Deep Learning based drug recognition
* Mobile app integration
* Real-time pharmacy verification
* Advanced Drug-Drug Interaction prediction using **Graph Neural Networks**

---

## 👨‍💻 Contributors

* Swetha P
* Nitin A K
* Sowmigha K A
* Vishal M K

---

## 📜 License

This project is for **educational and research purposes**.

If You need to download the entire folder of this project just download the folder which is available in .zip format.

