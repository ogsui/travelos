"""CSV/XLSX CRM import parsing is deterministic and conservative about consent."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from travelos.crm.fingerprints import ContactFingerprinter
from travelos.crm.importer import CRMImportMapping, parse_customer_import
from travelos.security.encryption import StaticKeyProvider


def mapping() -> CRMImportMapping:
    return CRMImportMapping(
        name="Customer Name",
        email="Email Address",
        phone="Mobile",
        email_marketing_consent="Marketing Opt In",
        email_marketing_proof_ref="Consent Reference",
    )


def test_csv_import_maps_explicit_consent_only_with_proof_reference() -> None:
    content = (
        b"Customer Name,Email Address,Mobile,Marketing Opt In,Consent Reference\n"
        b"Asha Synthetic,ASHA@example.test,+91 (90000) 00001,Yes,form-001\n"
        b"Balu Synthetic,balu@example.test,,Yes,\n"
        b"Chet Synthetic,chet@example.test,,maybe,\n"
    )

    report = parse_customer_import(content, filename="customers.csv", mapping=mapping())

    assert report.rows_seen == 3
    assert report.bad_rows == ()
    assert report.valid_rows[0].contacts[0].marketing_consent is True
    assert report.valid_rows[0].contacts[0].consent_proof_ref == "form-001"
    assert report.valid_rows[0].contacts[1].channel == "phone"
    assert report.valid_rows[0].contacts[1].marketing_consent is False
    assert report.valid_rows[1].contacts[0].marketing_consent is False
    assert report.valid_rows[2].contacts[0].marketing_consent is False
    assert report.unclear_consent_rows == (3, 4)


def test_csv_import_reports_bad_rows_and_duplicate_candidates() -> None:
    content = (
        b"Customer Name,Email Address\n"
        b"Asha Synthetic,asha@example.test\n"
        b",missing-name@example.test\n"
        b"Asha Duplicate, ASHA@example.test \n"
    )

    report = parse_customer_import(
        content,
        filename="customers.csv",
        mapping=CRMImportMapping(name="Customer Name", email="Email Address"),
    )

    assert report.rows_seen == 3
    assert len(report.valid_rows) == 2
    assert report.bad_rows[0].row_number == 3
    assert report.duplicate_candidates[0].first_row == 2
    assert report.duplicate_candidates[0].duplicate_row == 4


def test_identical_batch_and_mapping_have_stable_idempotency_key() -> None:
    content = b"Customer Name,Email Address\nAsha Synthetic,asha@example.test\n"
    import_mapping = CRMImportMapping(name="Customer Name", email="Email Address")

    first = parse_customer_import(content, filename="customers.csv", mapping=import_mapping)
    again = parse_customer_import(content, filename="renamed.csv", mapping=import_mapping)

    assert first.batch_key == again.batch_key
    assert len(first.batch_key) == 64


def test_mapping_changes_change_idempotency_key() -> None:
    content = b"Name,Email\nAsha Synthetic,asha@example.test\n"
    first = parse_customer_import(
        content, filename="customers.csv", mapping=CRMImportMapping(name="Name", email="Email")
    )
    second = parse_customer_import(
        content, filename="customers.csv", mapping=CRMImportMapping(name="Email", email="Name")
    )

    assert first.batch_key != second.batch_key


def test_batch_key_is_keyed_to_the_configured_secret() -> None:
    content = b"Name,Email\nAsha Synthetic,asha@example.test\n"
    import_mapping = CRMImportMapping(name="Name", email="Email")
    first = parse_customer_import(
        content,
        filename="customers.csv",
        mapping=import_mapping,
        fingerprinter=ContactFingerprinter(StaticKeyProvider(b"a" * 32)),
    )
    second = parse_customer_import(
        content,
        filename="customers.csv",
        mapping=import_mapping,
        fingerprinter=ContactFingerprinter(StaticKeyProvider(b"b" * 32)),
    )

    assert first.batch_key != second.batch_key


def test_xlsx_import_reads_shared_strings() -> None:
    workbook = BytesIO()
    with ZipFile(workbook, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Input" sheetId="2" r:id="rId2"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId2" Target="worksheets/sheet2.xml" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
            "</Relationships>",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<si><t>Customer Name</t></si><si><t>Email Address</t></si>"
            "<si><t>Asha Synthetic</t></si><si><t>asha@example.test</t></si></sst>",
        )
        archive.writestr(
            "xl/worksheets/sheet2.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData><row r='1'><c r='A1' t='s'><v>0</v></c><c r='B1' t='s'><v>1</v></c></row>"
            "<row r='2'><c r='A2' t='s'><v>2</v></c><c r='B2' t='s'><v>3</v></c></row>"
            "</sheetData></worksheet>",
        )

    report = parse_customer_import(
        workbook.getvalue(),
        filename="customers.xlsx",
        mapping=CRMImportMapping(name="Customer Name", email="Email Address"),
    )

    assert report.rows_seen == 1
    assert report.valid_rows[0].display_name == "Asha Synthetic"
    assert report.valid_rows[0].contacts[0].value == "asha@example.test"


def test_import_rejects_unsupported_or_oversized_files() -> None:
    try:
        parse_customer_import(b"bad", filename="customers.xls", mapping=mapping())
    except ValueError as exc:
        assert "supported" in str(exc)
    else:
        raise AssertionError("legacy XLS should be rejected")

    try:
        parse_customer_import(
            b"x" * (10 * 1024 * 1024 + 1), filename="customers.csv", mapping=mapping()
        )
    except ValueError as exc:
        assert "size" in str(exc)
    else:
        raise AssertionError("oversized import should be rejected")
