"""Build a minimal text PDF in memory (no extra dependency) for resume tests."""

from __future__ import annotations


def make_pdf(lines: list[str]) -> bytes:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    body = "BT /F1 10 Tf 50 760 Td 13 TL " + " ".join(f"({esc(ln)}) Tj T*" for ln in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(body)} >>\nstream\n{body}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


RESUME_LINES = [
    "Ravi Example",
    "Email: candidate@example.test | Bengaluru",
    "Education",
    "MCA (AI & IoT), Example University, 2025 - 2027, CGPA: 8.4",
    "BCA, Example College, 2022 - 2025",
    "Skills",
    "Python, Java, SQL, Flask, Streamlit, scikit-learn, Firebase, Git",
    "Projects",
    "Spam Mail Detector: TF-IDF + Logistic Regression model served with Flask and Streamlit.",
    "Job tracker app with Firebase authentication and a REST API backend.",
]
