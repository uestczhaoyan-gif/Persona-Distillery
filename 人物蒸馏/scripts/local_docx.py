"""Bounded DOCX main-body text extraction without archive extraction or relationships."""
import io
import zipfile
import xml.etree.ElementTree as ET

MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_XML_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024
MAX_MEMBERS = 1000
MAX_NODES = 100000
MAX_DEPTH = 256
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_docx(payload: bytes) -> str:
    if not isinstance(payload, bytes) or len(payload) > MAX_INPUT_BYTES:
        raise ValueError("docx_input_limit")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if len(members) > MAX_MEMBERS or sum(i.file_size for i in members) > MAX_TOTAL_BYTES:
                raise ValueError("docx_archive_limit")
            names = [i.filename for i in members]
            if len(set(names)) != len(names):
                raise ValueError("docx_duplicate_members")
            if any(i.flag_bits & 1 for i in members):
                raise ValueError("docx_encrypted")
            for name in names:
                if name.startswith("/") or "\\" in name or any(p in {"..", "."} for p in name.split("/")):
                    raise ValueError("docx_unsafe_member")
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_XML_BYTES:
                raise ValueError("docx_xml_limit")
            with archive.open(info) as handle:
                raw = handle.read(MAX_XML_BYTES + 1)
            if len(raw) > MAX_XML_BYTES:
                raise ValueError("docx_xml_limit")
    except (zipfile.BadZipFile, KeyError, NotImplementedError, RuntimeError) as error:
        raise ValueError("docx_invalid_archive") from error
    # Deliberately accept UTF-8 XML only; reject DTDs/entities before XML parsing.
    source = raw.decode("utf-8-sig")
    if "\x00" in source or "<!DOCTYPE" in source.upper() or "<!ENTITY" in source.upper():
        raise ValueError("docx_unsafe_xml")
    parts = []
    stack = []
    count = 0
    root_seen = body_seen = False
    try:
        for event, element in ET.iterparse(io.StringIO(source), events=("start", "end")):
            if event == "start":
                count += 1
                if count > MAX_NODES or len(stack) >= MAX_DEPTH:
                    raise ValueError("docx_xml_structure_limit")
                if not root_seen:
                    if element.tag != W + "document":
                        raise ValueError("docx_unknown_document_namespace")
                    root_seen = True
                parent_in_body = stack[-1][1] if stack else False
                in_body = parent_in_body or (element.tag == W + "body" and len(stack) == 1)
                body_seen = body_seen or in_body
                omitted = (stack[-1][2] if stack else False) or element.tag in {W + "del", W + "moveFrom"}
                stack.append((element.tag, in_body, omitted))
            else:
                _, in_body, omitted = stack.pop()
                if in_body and not omitted:
                    if element.tag == W + "t":
                        parts.append(element.text or "")
                    elif element.tag in {W + "tab", W + "tc"}:
                        parts.append("\t")
                    elif element.tag in {W + "br", W + "cr", W + "p", W + "tr"}:
                        parts.append("\n")
                element.clear()
    except ET.ParseError as error:
        raise ValueError("docx_invalid_xml") from error
    text = "\n".join(line for part in "".join(parts).splitlines() if (line := " ".join(part.split())))
    if not body_seen or not text:
        raise ValueError("docx_empty_body")
    return text
