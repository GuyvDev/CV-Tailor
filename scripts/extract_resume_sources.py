from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path

from docx import Document
import fitz
from PIL import Image
import pdfplumber
from pypdf import PdfReader
from rapidocr_onnxruntime import RapidOCR


TEXT_EXTENSIONS = {".md", ".txt", ".json", ".py", ".toml", ".yml", ".yaml"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
SKIP_DIRS = {".git", "__pycache__", "extracted_text", "organized"}
SKIP_EXTENSIONS = {".pyc", ".npy"}


def normalize_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\x00", "").splitlines()]
    compact = []
    previous_blank = False
    for line in lines:
        if line.strip():
            compact.append(line.strip())
            previous_blank = False
        elif not previous_blank:
            compact.append("")
            previous_blank = True
    return "\n".join(compact).strip()


def extract_text_file(path: Path) -> tuple[str, str]:
    return path.read_text(encoding="utf-8", errors="ignore"), "text"


def extract_docx_text(path: Path) -> tuple[str, str]:
    document = Document(path)
    lines = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    return "\n".join(lines), "docx"


def extract_ipynb_text(path: Path) -> tuple[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    lines: list[str] = []
    for cell in data.get("cells", []):
        source = "".join(cell.get("source", []))
        if not source.strip():
            continue
        cell_type = cell.get("cell_type", "unknown")
        lines.append(f"[{cell_type}]")
        lines.extend(line.rstrip() for line in source.splitlines())
        lines.append("")
    return "\n".join(lines).strip(), "ipynb"


def ocr_image(image: Image.Image, ocr_engine: RapidOCR) -> str:
    result, _ = ocr_engine(image)
    if not result:
        return ""
    parts = [entry[1] for entry in result if len(entry) >= 2 and entry[1].strip()]
    return "\n".join(parts)


def extract_image_text(path: Path, ocr_engine: RapidOCR) -> tuple[str, str]:
    image = Image.open(path)
    return ocr_image(image, ocr_engine), "ocr-image"


def extract_pdf_text(path: Path, ocr_engine: RapidOCR) -> tuple[str, str]:
    methods_used: list[str] = []
    candidates: list[str] = []

    try:
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if text.strip():
            methods_used.append("pypdf")
            candidates.append(text)
    except Exception:
        pass

    try:
        with pdfplumber.open(str(path)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        if text.strip():
            methods_used.append("pdfplumber")
            candidates.append(text)
    except Exception:
        pass

    page_texts: list[str] = []
    ocr_texts: list[str] = []
    try:
        document = fitz.open(str(path))
        for page in document:
            direct = page.get_text("text") or ""
            if direct.strip():
                page_texts.append(direct)
                continue

            pix = page.get_pixmap(dpi=220, alpha=False)
            image = Image.open(BytesIO(pix.tobytes("png")))
            ocr_text = ocr_image(image, ocr_engine)
            if ocr_text.strip():
                ocr_texts.append(ocr_text)

        if page_texts:
            methods_used.append("pymupdf")
            candidates.append("\n".join(page_texts))
        if ocr_texts:
            methods_used.append("ocr-pdf")
            candidates.append("\n".join(ocr_texts))
    except Exception:
        pass

    best = max(candidates, key=lambda value: len(value.strip()), default="")
    return best, "+".join(methods_used) if methods_used else "none"


def should_skip(path: Path, input_root: Path) -> bool:
    if path.suffix.lower() in SKIP_EXTENSIONS:
        return True
    if any(part in SKIP_DIRS for part in path.relative_to(input_root).parts):
        return True
    return False


def extract_text(path: Path, ocr_engine: RapidOCR) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return extract_text_file(path)
    if suffix == ".docx":
        return extract_docx_text(path)
    if suffix == ".ipynb":
        return extract_ipynb_text(path)
    if suffix == ".pdf":
        return extract_pdf_text(path, ocr_engine)
    if suffix in IMAGE_EXTENSIONS:
        return extract_image_text(path, ocr_engine)
    return "", "unsupported"


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: extract_resume_sources.py <input_root> <output_root>")
        return 1

    input_root = Path(sys.argv[1])
    output_root = Path(sys.argv[2])
    output_root.mkdir(parents=True, exist_ok=True)
    ocr_engine = RapidOCR()

    manifest: list[dict[str, str]] = []
    for path in sorted(input_root.rglob("*")):
        if not path.is_file() or should_skip(path, input_root):
            continue

        relative = path.relative_to(input_root)
        output_path = output_root / relative.with_suffix(relative.suffix + ".txt")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        text, method = extract_text(path, ocr_engine)
        text = normalize_text(text)
        output_path.write_text(text, encoding="utf-8")

        manifest.append(
            {
                "source": str(relative),
                "extracted": str(output_path.relative_to(output_root)),
                "chars": str(len(text)),
                "method": method,
            }
        )

    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
