"""FR-1: parse PDF/DOCX into page-aware lines, extract document metadata, and chunk without splitting table rows."""
import re, pathlib
import pymupdf
import docx as pydocx

META = {"Document ID": "id", "Type": "type", "Models": "models", "Revision": "rev", "Effective": "effective", "Supersedes": "supersedes"}
META_RE = re.compile(r"^(Document ID|Type|Models|Revision|Effective|Supersedes):\s*(.+)$")
HEAD_RE = re.compile(r"^(\d+)\.\s+([A-Z][A-Za-z &/-]+)$")
FOOT_RE = re.compile(r"^Page \d+$")
GAP = 20          # horizontal gap (pt) that separates table cells
WRAP_LEN = 85     # a line this long that doesn't end a sentence is a wrapped paragraph line


def pdf_pages(path):
    pages = []
    with pymupdf.open(path) as pdf:
        for page in pdf:
            rows = {}
            for x0, y0, x1, y1, w, *_ in page.get_text("words"):
                rows.setdefault(round(y0 / 3), []).append((x0, x1, w))
            lines = []
            for k in sorted(rows):
                ws = sorted(rows[k]); out = ws[0][2]
                for a, b in zip(ws, ws[1:]):
                    out += (" | " if b[0] - a[1] > GAP else " ") + b[2]
                lines.append(out)
            pages.append(lines)
    return pages


def docx_pages(path):
    d = pydocx.Document(path); lines = []
    for el in d.element.body.iterchildren():
        if el.tag.endswith("}p"):
            t = "".join(x.text or "" for x in el.iter() if x.tag.endswith("}t")).strip()
            if t: lines.append(t)
        elif el.tag.endswith("}tbl"):
            for tr in el.iter():
                if tr.tag.endswith("}tr"):
                    cells = ["".join(x.text or "" for x in tc.iter() if x.tag.endswith("}t")).strip()
                             for tc in tr if tc.tag.endswith("}tc")]
                    lines.append(" | ".join(cells))
    return [lines]        # DOCX has no fixed pagination: treat as page 1


def parse(path):
    path = pathlib.Path(path)
    pages = pdf_pages(path) if path.suffix == ".pdf" else docx_pages(path)
    meta = {}
    for ln in pages[0]:
        m = META_RE.match(ln)
        if m: meta[META[m.group(1)]] = m.group(2).strip()
    meta["models"] = [x.strip() for x in meta.get("models", "").split(",") if x.strip()]
    meta["title"] = pages[0][0] if pages[0] else path.stem
    meta["file"] = path.name
    meta["n_pages"] = len(pages)
    return {"meta": meta, "pages": pages}


def _segments(parsed):
    """Yield (page, section_num, section_title, [units]); a unit is a paragraph line or an entire table block."""
    sec = ("0", "Header"); out = []
    for pno, lines in enumerate(parsed["pages"], 1):
        units, buf, table = [], "", []
        def push_para():
            nonlocal buf
            if buf: units.append(("p", buf)); buf = ""
        def push_table():
            nonlocal table
            if table: units.append(("t", "\n".join(table))); table = []
        for ln in lines:
            if FOOT_RE.match(ln): continue
            if " | " in ln: push_para(); table.append(ln); continue
            push_table()
            h = HEAD_RE.match(ln)
            if h or META_RE.match(ln):
                push_para()
                if h:
                    out.append((pno, sec, units)); units = []; sec = (h.group(1), h.group(2).strip())
                    continue
                units.append(("p", ln)); continue
            if buf and len(buf) >= WRAP_LEN and not buf.endswith("."): buf += " " + ln
            else: push_para(); buf = ln
        push_para(); push_table()
        out.append((pno, sec, units))
    return [(p, s, u) for p, s, u in out if u]


def chunk(parsed, max_chars=900):
    m = parsed["meta"]; chunks = []
    ctx = lambda sec, page: f"[{m['id']} | {m['type']} | Model {','.join(m['models'])} | Rev {m['rev']} | eff {m['effective']} | §{sec[0]} {sec[1]} | p.{page}]"
    for page, sec, units in _segments(parsed):
        cur, size = [], 0
        def emit():
            nonlocal cur, size
            if not cur: return
            body = "\n".join(cur)
            chunks.append({"id": f"{m['id']}:p{page}:c{len(chunks)}", "doc_id": m["id"], "doc_type": m["type"], "models": m["models"],
                           "rev": m["rev"], "effective": m["effective"], "section": f"{sec[0]} {sec[1]}", "section_num": sec[0],
                           "section_title": sec[1], "page": page, "body": body, "text": ctx(sec, page) + "\n" + body})
            cur, size = [], 0
        for kind, txt in units:                      # tables are atomic units -> never split mid-row
            if cur and size + len(txt) > max_chars: emit()
            cur.append(txt); size += len(txt)
        emit()
    return chunks
