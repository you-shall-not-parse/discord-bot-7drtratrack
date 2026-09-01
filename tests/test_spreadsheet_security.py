from openpyxl import Workbook

from spreadsheet_security import safe_spreadsheet_value


def test_formula_prefixes_are_escaped() -> None:
    for value in ("=1+1", "+cmd", "-2+3", "@SUM(A1:A2)"):
        assert safe_spreadsheet_value(value) == f"'{value}"


def test_normal_text_and_non_strings_are_unchanged() -> None:
    assert safe_spreadsheet_value("Example User") == "Example User"
    assert safe_spreadsheet_value(123) == 123
    assert safe_spreadsheet_value(None) is None


def test_escaped_value_is_stored_as_text_by_openpyxl() -> None:
    workbook = Workbook()
    cell = workbook.active.cell(row=1, column=1, value=safe_spreadsheet_value("=1+1"))

    assert cell.value == "'=1+1"
    assert cell.data_type == "s"
