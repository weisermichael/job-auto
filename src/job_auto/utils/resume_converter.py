"""Convert resume formats: DOCX/MD → PDF."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from job_auto.utils.logging import get_logger

logger = get_logger(__name__)


def md_to_html(md_text: str) -> str:
    """Convert Markdown to HTML with minimal styling for PDF rendering."""
    import markdown
    html_body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: 'Georgia', serif; font-size: 11pt; line-height: 1.5;
         max-width: 800px; margin: 0 auto; padding: 1.5cm; color: #1a1a1a; }}
  h1 {{ font-size: 20pt; margin-bottom: 0.2em; }}
  h2 {{ font-size: 13pt; border-bottom: 1px solid #ccc; padding-bottom: 2px;
        margin-top: 1.2em; }}
  h3 {{ font-size: 11pt; margin-bottom: 0.1em; }}
  ul {{ margin: 0.3em 0 0.3em 1.5em; }}
  li {{ margin-bottom: 0.2em; }}
  p  {{ margin: 0.4em 0; }}
  a  {{ color: #1a1a1a; text-decoration: none; }}
  .contact {{ font-size: 10pt; color: #444; }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""


def html_to_pdf(html: str, output_path: Path) -> Path:
    """Render HTML to PDF using WeasyPrint."""
    try:
        from weasyprint import HTML
        HTML(string=html).write_pdf(str(output_path))
        logger.info("pdf_rendered", path=str(output_path))
        return output_path
    except ImportError:
        logger.warning("weasyprint_not_installed", fallback="trying wkhtmltopdf")
        return _wkhtmltopdf_fallback(html, output_path)


def _wkhtmltopdf_fallback(html: str, output_path: Path) -> Path:
    """Fallback to wkhtmltopdf if WeasyPrint is unavailable."""
    with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False) as f:
        f.write(html)
        tmp_html = Path(f.name)
    try:
        subprocess.run(
            ["wkhtmltopdf", "--quiet", str(tmp_html), str(output_path)],
            check=True,
            capture_output=True,
        )
    finally:
        tmp_html.unlink(missing_ok=True)
    return output_path


def md_to_pdf(md_text: str, output_path: Path) -> Path:
    """Convert Markdown text to a PDF file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html = md_to_html(md_text)
    return html_to_pdf(html, output_path)


def docx_to_md(docx_path: Path) -> str:
    """Convert a DOCX file to Markdown using python-docx."""
    from docx import Document

    doc = Document(str(docx_path))
    lines: list[str] = []

    for para in doc.paragraphs:
        style = para.style.name.lower() if para.style else ""
        text = para.text.strip()
        if not text:
            lines.append("")
            continue

        if "heading 1" in style:
            lines.append(f"# {text}")
        elif "heading 2" in style:
            lines.append(f"## {text}")
        elif "heading 3" in style:
            lines.append(f"### {text}")
        elif "list" in style or text.startswith(("•", "-", "–", "*")):
            clean = text.lstrip("•-–* ").strip()
            lines.append(f"- {clean}")
        else:
            lines.append(text)

    return "\n".join(lines)
