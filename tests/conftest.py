"""Test fixtures with temporary databases and a fake model client."""

import re
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.config import Settings
from backend.main import create_app
from backend.ollama import Ollama, normalized


class FakeOllama(Ollama):
    def __init__(self, settings):
        super().__init__(settings)
        self.embed_calls = 0
        self.generate_calls = 0
        self.response = None

    def status(self):
        return {"available": False, "chat_ready": False, "embedding_ready": False, "models": []}

    def embed(self, texts):
        self.embed_calls += 1
        results = []
        for text in texts:
            lowered = text.lower()
            refund = any(w in lowered for w in ["refund", "reimbursement", "money back"])
            food = any(w in lowered for w in ["recipe", "vegetable", "cook"])
            results.append(normalized([float(refund), float(food), 0.01]))
        return results

    def generate(self, question, sources, schema, *, repair=False):
        self.generate_calls += 1
        if self.response is not None:
            return self.response
        cid = sources[0]["chunk_id"]
        first_sentence = re.split(r"(?<=[.!?])\s", sources[0]["text"])[0]
        return {"answer": f"{first_sentence} [S{cid}]", "citations": [cid], "abstained": False}


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def ai(settings):
    return FakeOllama(settings)


@pytest.fixture
def client(settings, ai):
    with TestClient(create_app(settings, ai)) as instance:
        yield instance


def upload(client, text="The pilot lasts six weeks.", filename="notes.txt"):
    payload = text.encode("utf-8") if isinstance(text, str) else text
    return client.post("/api/documents", files={"file": (filename, payload)})


def pdf_bytes(texts, password=None):
    """A real in-memory PDF fixture avoids adding a PDF-generation dependency."""
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
            }
        )
        if text:
            stream = DecodedStreamObject()
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
