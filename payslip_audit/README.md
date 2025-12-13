# Payslip Timesheet Audit

A helper script to compare payslip PDFs with OCR'd timesheet screenshots and produce an audit report.

## Installation
Install the Python dependencies before running the script. The Render service already installs them from the repo's central requirements file:

```bash
pip install -r ../render/requirements.txt
```

The tool requires Tesseract OCR to be available on your system for `pytesseract` to work.

## Usage
Place your payslip PDF and timesheet screenshots in the working directory, then run:

```bash
python payslip_timesheet_audit.py
```

Use `--help` for additional options.
