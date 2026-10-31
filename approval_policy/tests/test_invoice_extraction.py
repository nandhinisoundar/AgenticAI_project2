"""Tests for in-memory invoice upload extraction requests."""
from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from approval_policy.invoice_extraction import extract_invoice_fields


class FakeResponses:
    def __init__(self, output_text: str):
        self.output_text = output_text
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_text=self.output_text)


class FakeClient:
    def __init__(self, output_text: str):
        self.responses = FakeResponses(output_text)


def test_text_invoice_is_sent_as_text_and_fields_are_normalized():
    client = FakeClient('{"invoice_number":137,"vendor":"Northstar","iban":" GB29NWBK60161331926819 ","amount_due":"1240.50","due_date":"2026-11-17"}')

    fields = extract_invoice_fields("invoice.txt", b"Invoice data", "test-model", client)

    assert fields == {
        "invoice_number": "137",
        "vendor": "Northstar",
        "iban": "GB29NWBK60161331926819",
        "amount_due": 1240.5,
        "due_date": "2026-11-17",
    }
    assert client.responses.kwargs["model"] == "test-model"
    assert client.responses.kwargs["input"][0]["content"][1]["text"] == "Invoice data"


def test_pdf_and_image_uploads_use_expected_encoded_content_type():
    for filename, mime_type, content_type in (
        ("invoice.pdf", "application/pdf", "input_file"),
        ("invoice.png", "image/png", "input_image"),
    ):
        client = FakeClient('{"invoice_number":null,"vendor":null,"iban":null,"amount_due":null}')
        extract_invoice_fields(filename, b"sample-bytes", "test-model", client)
        content = client.responses.kwargs["input"][0]["content"][1]
        if content_type == "input_file":
            encoded = content["file_data"].split(",", 1)[1]
        else:
            encoded = content["image_url"].split(",", 1)[1]
        assert content["type"] == content_type
        assert content["file_data" if content_type == "input_file" else "image_url"].startswith(f"data:{mime_type};base64,")
        assert base64.b64decode(encoded) == b"sample-bytes"


def test_upload_type_size_and_invalid_model_json_fail_closed():
    client = FakeClient("not json")
    with pytest.raises(ValueError, match="Supported invoice uploads"):
        extract_invoice_fields("invoice.exe", b"x", "test-model", client)
    with pytest.raises(ValueError, match="10 MB"):
        extract_invoice_fields("invoice.txt", b"x" * (10 * 1024 * 1024 + 1), "test-model", client)
    with pytest.raises(ValueError, match="valid JSON"):
        extract_invoice_fields("invoice.txt", b"invoice", "test-model", client)
