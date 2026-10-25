"""Parse CRM customer CSV/XLSX files into a dry-run report."""

from __future__ import annotations

import csv
import io
import json
import posixpath
import re
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from travelos.crm.fingerprints import ContactFingerprinter

MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_XLSX_EXPANDED_BYTES = 25 * 1024 * 1024
_OOXML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_CELL_REF = re.compile(r"([A-Z]+)")
_TRUE_VALUES = frozenset({"yes", "y", "true", "1", "granted", "opt-in", "opted-in"})
_FALSE_VALUES = frozenset({"no", "n", "false", "0", "denied", "opt-out", "opted-out"})
_CHANNEL_FIELDS = (("email", "email"), ("phone", "phone"), ("whatsapp", "whatsapp"))


@dataclass(frozen=True, slots=True)
class CRMImportMapping:
    """Source column names. Unmapped/unknown marketing consent never grants consent."""

    name: str
    customer_type: str | None = None
    segment: str | None = None
    email: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    email_marketing_consent: str | None = None
    email_marketing_proof_ref: str | None = None
    phone_marketing_consent: str | None = None
    phone_marketing_proof_ref: str | None = None
    whatsapp_marketing_consent: str | None = None
    whatsapp_marketing_proof_ref: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "customer_type": self.customer_type,
            "segment": self.segment,
            "email": self.email,
            "phone": self.phone,
            "whatsapp": self.whatsapp,
            "email_marketing_consent": self.email_marketing_consent,
            "email_marketing_proof_ref": self.email_marketing_proof_ref,
            "phone_marketing_consent": self.phone_marketing_consent,
            "phone_marketing_proof_ref": self.phone_marketing_proof_ref,
            "whatsapp_marketing_consent": self.whatsapp_marketing_consent,
            "whatsapp_marketing_proof_ref": self.whatsapp_marketing_proof_ref,
        }


@dataclass(frozen=True, slots=True)
class ImportedContact:
    channel: str
    value: str
    fingerprint: str
    marketing_consent: bool
    consent_proof_ref: str | None


@dataclass(frozen=True, slots=True)
class ImportedCustomer:
    row_number: int
    display_name: str
    customer_type: str
    segment: str | None
    contacts: tuple[ImportedContact, ...]


@dataclass(frozen=True, slots=True)
class ImportRowIssue:
    row_number: int
    code: str


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    channel: str
    fingerprint: str
    first_row: int | None
    duplicate_row: int
    existing_customer_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class CRMImportReport:
    source_hash: str
    mapping_hash: str
    batch_key: str
    rows_seen: int
    valid_rows: tuple[ImportedCustomer, ...]
    bad_rows: tuple[ImportRowIssue, ...]
    duplicate_candidates: tuple[DuplicateCandidate, ...]
    unclear_consent_rows: tuple[int, ...]

    @property
    def importable_rows(self) -> tuple[ImportedCustomer, ...]:
        duplicate_rows = {item.duplicate_row for item in self.duplicate_candidates}
        return tuple(row for row in self.valid_rows if row.row_number not in duplicate_rows)

    @property
    def within_batch_duplicate_count(self) -> int:
        return sum(
            candidate.existing_customer_id is None for candidate in self.duplicate_candidates
        )


class CRMImportFormatError(ValueError):
    """The source file or mapping cannot be interpreted safely."""


def parse_customer_import(
    content: bytes,
    *,
    filename: str,
    mapping: CRMImportMapping,
    fingerprinter: ContactFingerprinter | None = None,
) -> CRMImportReport:
    """Parse CSV or XLSX without storing raw source rows or logging their contents."""

    if not content:
        raise CRMImportFormatError("import file is empty")
    if len(content) > MAX_IMPORT_BYTES:
        raise CRMImportFormatError("import file exceeds the 10 MB size limit")
    suffix = filename.lower().rsplit(".", maxsplit=1)[-1] if "." in filename else ""
    if suffix == "csv":
        headers, records = _read_csv(content)
    elif suffix == "xlsx":
        headers, records = _read_xlsx(content)
    else:
        raise CRMImportFormatError("only CSV and XLSX files are supported")

    _validate_mapping_headers(headers, mapping)
    active_fingerprinter = fingerprinter or ContactFingerprinter()
    source_hash = active_fingerprinter.keyed_digest("crm-import-source-v1", content)
    mapping_payload = json.dumps(mapping.as_dict(), sort_keys=True, separators=(",", ":"))
    mapping_hash = active_fingerprinter.keyed_digest(
        "crm-import-mapping-v1", mapping_payload.encode()
    )
    batch_key = active_fingerprinter.keyed_digest(
        "crm-import-batch-v1", f"{source_hash}:{mapping_hash}".encode()
    )
    valid_rows: list[ImportedCustomer] = []
    bad_rows: list[ImportRowIssue] = []
    duplicates: list[DuplicateCandidate] = []
    unclear_consent: list[int] = []
    seen: dict[tuple[str, str], int] = {}

    for row_number, source_row in records:
        if None in source_row:
            bad_rows.append(ImportRowIssue(row_number, "too_many_columns"))
            continue
        display_name = _value(source_row, mapping.name)
        if not display_name:
            bad_rows.append(ImportRowIssue(row_number, "missing_customer_name"))
            continue
        if len(display_name) > 200:
            bad_rows.append(ImportRowIssue(row_number, "customer_name_too_long"))
            continue
        customer_type = _value(source_row, mapping.customer_type).lower() or "individual"
        if customer_type not in {"individual", "family", "corporate"}:
            bad_rows.append(ImportRowIssue(row_number, "invalid_customer_type"))
            continue
        segment_value = _value(source_row, mapping.segment) or None
        if segment_value is not None and len(segment_value) > 40:
            bad_rows.append(ImportRowIssue(row_number, "segment_too_long"))
            continue

        contact_rows: list[ImportedContact] = []
        invalid_contact = False
        for field_name, channel in _CHANNEL_FIELDS:
            column = getattr(mapping, field_name)
            contact_value = _value(source_row, column)
            if not contact_value:
                continue
            if len(contact_value) > 320:
                invalid_contact = True
                break
            if channel == "email" and not _valid_email(contact_value):
                invalid_contact = True
                break
            try:
                fingerprint = active_fingerprinter.fingerprint(channel, contact_value)
            except ValueError:
                invalid_contact = True
                break
            raw_consent = _value(
                source_row, getattr(mapping, f"{field_name}_marketing_consent")
            ).casefold()
            proof_ref = (
                _value(source_row, getattr(mapping, f"{field_name}_marketing_proof_ref")) or None
            )
            if proof_ref is not None and len(proof_ref) > 255:
                invalid_contact = True
                break
            affirmative = raw_consent in _TRUE_VALUES and proof_ref is not None
            if (raw_consent and raw_consent not in _TRUE_VALUES | _FALSE_VALUES) or (
                raw_consent in _TRUE_VALUES and proof_ref is None
            ):
                unclear_consent.append(row_number)
            contact_rows.append(
                ImportedContact(
                    channel=channel,
                    value=contact_value,
                    fingerprint=fingerprint,
                    marketing_consent=affirmative,
                    consent_proof_ref=proof_ref if affirmative else None,
                )
            )
        if invalid_contact:
            bad_rows.append(ImportRowIssue(row_number, "invalid_contact_value"))
            continue

        for contact in contact_rows:
            key = (contact.channel, contact.fingerprint)
            first_row = seen.get(key)
            if first_row is not None:
                duplicates.append(
                    DuplicateCandidate(
                        channel=contact.channel,
                        fingerprint=contact.fingerprint,
                        first_row=first_row,
                        duplicate_row=row_number,
                    )
                )
            else:
                seen[key] = row_number

        valid_rows.append(
            ImportedCustomer(
                row_number=row_number,
                display_name=display_name,
                customer_type=customer_type,
                segment=segment_value,
                contacts=tuple(contact_rows),
            )
        )

    return CRMImportReport(
        source_hash=source_hash,
        mapping_hash=mapping_hash,
        batch_key=batch_key,
        rows_seen=len(records),
        valid_rows=tuple(valid_rows),
        bad_rows=tuple(bad_rows),
        duplicate_candidates=tuple(duplicates),
        unclear_consent_rows=tuple(unclear_consent),
    )


def _read_csv(content: bytes) -> tuple[list[str], list[tuple[int, dict[str | None, str | None]]]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CRMImportFormatError("CSV must use UTF-8 encoding") from exc
    if not text.strip():
        raise CRMImportFormatError("CSV has no rows")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise CRMImportFormatError("CSV header row is missing")
    reader.fieldnames = [str(name).strip() for name in reader.fieldnames]
    headers = reader.fieldnames
    if len(set(headers)) != len(headers):
        raise CRMImportFormatError("CSV contains duplicate column names")
    rows: list[tuple[int, dict[str | None, str | None]]] = []
    for row_number, row in enumerate(reader, start=2):
        normalized_row = cast(dict[str | None, str | None], dict(row))
        rows.append((row_number, normalized_row))
    return headers, rows


def _read_xlsx(content: bytes) -> tuple[list[str], list[tuple[int, dict[str | None, str | None]]]]:
    try:
        archive = ZipFile(io.BytesIO(content))
    except BadZipFile as exc:
        raise CRMImportFormatError("XLSX file is invalid") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > 256 or sum(item.file_size for item in infos) > MAX_XLSX_EXPANDED_BYTES:
            raise CRMImportFormatError("XLSX expands beyond the permitted size limit")
        names = set(archive.namelist())
        sheet_path = _first_worksheet_path(archive, names)
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = _xml_root(archive.read("xl/sharedStrings.xml"), "shared strings")
            shared_strings = [
                "".join(node.text or "" for node in item.iter(f"{_OOXML_NS}t"))
                for item in root.findall(f"{_OOXML_NS}si")
            ]
        worksheet = _xml_root(archive.read(sheet_path), "worksheet")
        rows: list[tuple[int, list[str]]] = []
        for sequence, row in enumerate(worksheet.iter(f"{_OOXML_NS}row"), start=1):
            row_number = int(row.attrib.get("r", sequence))
            cells = [""] * max(
                (_column_index(cell.attrib.get("r", "A1")) for cell in row), default=0
            )
            for cell in row.findall(f"{_OOXML_NS}c"):
                index = _column_index(cell.attrib.get("r", "A1")) - 1
                value_node = cell.find(f"{_OOXML_NS}v")
                value = value_node.text or "" if value_node is not None else ""
                if cell.attrib.get("t") == "s" and value:
                    try:
                        value = shared_strings[int(value)]
                    except (IndexError, ValueError) as exc:
                        raise CRMImportFormatError(
                            "XLSX has an invalid shared string index"
                        ) from exc
                elif cell.attrib.get("t") == "inlineStr":
                    value = "".join(node.text or "" for node in cell.iter(f"{_OOXML_NS}t"))
                if index >= len(cells):
                    cells.extend([""] * (index + 1 - len(cells)))
                cells[index] = value
            rows.append((row_number, cells))
    if not rows:
        raise CRMImportFormatError("XLSX first worksheet has no rows")
    headers = [value.strip() for value in rows[0][1]]
    if not headers or len({name for name in headers if name}) != len(
        [name for name in headers if name]
    ):
        raise CRMImportFormatError("XLSX header row is missing or has duplicate columns")
    records: list[tuple[int, dict[str | None, str | None]]] = []
    for number, values in rows[1:]:
        record: dict[str | None, str | None] = {
            headers[index]: value for index, value in enumerate(values) if index < len(headers)
        }
        if len(values) > len(headers):
            record[None] = None
        records.append((number, record))
    return headers, records


def _first_worksheet_path(archive: ZipFile, names: set[str]) -> str:
    workbook_path = "xl/workbook.xml"
    relationships_path = "xl/_rels/workbook.xml.rels"
    if workbook_path not in names or relationships_path not in names:
        raise CRMImportFormatError("XLSX workbook metadata is missing")
    workbook = _xml_root(archive.read(workbook_path), "workbook")
    sheets = workbook.find(f"{_OOXML_NS}sheets")
    first_sheet = sheets.find(f"{_OOXML_NS}sheet") if sheets is not None else None
    relationship_id = (
        first_sheet.attrib.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        if first_sheet is not None
        else None
    )
    relationships = _xml_root(archive.read(relationships_path), "relationships")
    for relationship in relationships:
        if relationship.attrib.get("Id") != relationship_id:
            continue
        if relationship.attrib.get("TargetMode") == "External":
            raise CRMImportFormatError("XLSX first worksheet cannot be external")
        target = relationship.attrib.get("Target", "")
        path = posixpath.normpath(target.lstrip("/"))
        if not target.startswith("/"):
            path = posixpath.normpath(posixpath.join("xl", path))
        if not path.startswith("xl/worksheets/") or path not in names:
            raise CRMImportFormatError("XLSX first worksheet path is invalid")
        return path
    raise CRMImportFormatError("XLSX first worksheet relationship is missing")


def _xml_root(content: bytes, description: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise CRMImportFormatError(f"XLSX {description} XML is invalid") from exc


def _column_index(reference: str) -> int:
    match = _CELL_REF.match(reference)
    if match is None:
        raise CRMImportFormatError("XLSX contains an invalid cell reference")
    index = 0
    for character in match.group(1):
        index = index * 26 + ord(character) - ord("A") + 1
    return index


def _validate_mapping_headers(headers: list[str], mapping: CRMImportMapping) -> None:
    if not mapping.name.strip():
        raise CRMImportFormatError("customer name mapping is required")
    missing = sorted(
        column
        for column in mapping.as_dict().values()
        if column is not None and column not in headers
    )
    if missing:
        raise CRMImportFormatError("mapped column not found in source header")
    for field_name, _channel in _CHANNEL_FIELDS:
        consent_column = getattr(mapping, f"{field_name}_marketing_consent")
        proof_column = getattr(mapping, f"{field_name}_marketing_proof_ref")
        if (consent_column is not None or proof_column is not None) and getattr(
            mapping, field_name
        ) is None:
            raise CRMImportFormatError("consent mapping requires the matching channel mapping")
        if proof_column is not None and consent_column is None:
            raise CRMImportFormatError("consent proof mapping requires a consent-value mapping")


def _valid_email(value: str) -> bool:
    if any(character.isspace() for character in value) or value.count("@") != 1:
        return False
    local, separator, domain = value.rpartition("@")
    return bool(separator and local and domain and "." in domain and not domain.startswith("."))


def _value(row: dict[str | None, str | None], column: str | None) -> str:
    value: Any = row.get(column) if column is not None else None
    return str(value).strip() if value is not None else ""
