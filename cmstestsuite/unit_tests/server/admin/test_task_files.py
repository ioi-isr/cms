"""Check how the admin task file routes serve statements and attachments."""

import io
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urljoin, urlsplit

import pytest
from werkzeug.test import Client
from werkzeug.wrappers import Response

from cms.db import Attachment, Statement
from cms.server.admin.handlers import HANDLERS
from cms.server.admin.handlers.base import BaseHandler
from cms.server.admin.handlers.task import AttachmentHandler, StatementHandler
from cms.server.file_middleware import FileServerMiddleware
from tornado.wsgi import WSGIApplication


@pytest.fixture
def task_files(monkeypatch):
    task = SimpleNamespace(name="example")
    statement = SimpleNamespace(task_id=1, task=task, language="he", digest="pdf")
    attachment = SimpleNamespace(task_id=1, filename="diagram.png", digest="attachment")
    admin = SimpleNamespace(permission_all=False)
    monkeypatch.setattr(BaseHandler, "get_current_user", lambda self: admin)
    monkeypatch.setattr(Statement, "get_from_id", lambda ident, session: statement)
    monkeypatch.setattr(Attachment, "get_from_id", lambda ident, session: attachment)

    contents = {"pdf": b"%PDF-1.5", "attachment": b"attachment contents"}
    file_cacher = Mock()
    file_cacher.get_file.side_effect = lambda digest: io.BytesIO(contents[digest])
    file_cacher.get_size.side_effect = lambda digest: len(contents[digest])
    application = WSGIApplication([
        route for route in HANDLERS if route[1] in (StatementHandler, AttachmentHandler)
    ], login_url="/login")
    client = Client(FileServerMiddleware(file_cacher, application), Response)
    return client, attachment


def test_statement_opens_inline(task_files):
    client, _ = task_files
    response = client.get("/task/1/statement/1")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.headers["Content-Disposition"] == "inline; filename=example_he.pdf"
    assert response.get_data() == b"%PDF-1.5"


@pytest.mark.parametrize("filename,mimetype,disposition", [
    ("diagram.PNG", "image/png", "inline"),
    ("notes.txt", "text/plain", "inline"),
    ("grader.c", "text/plain", "inline"),
    ("grader.C", "text/plain", "inline"),
    ("grader.cc", "text/plain", "inline"),
    ("grader.cpp", "text/plain", "inline"),
    ("grader.cxx", "text/plain", "inline"),
    ("task.h", "text/plain", "inline"),
    ("task.hh", "text/plain", "inline"),
    ("task.hpp", "text/plain", "inline"),
    ("task.hxx", "text/plain", "inline"),
    ("appendix.pdf", "application/pdf", "inline"),
    ("grader.zip", "application/octet-stream", "attachment"),
    ("source.docx", "application/octet-stream", "attachment"),
    ("page.html", "application/octet-stream", "attachment"),
    ("vector.svg", "application/octet-stream", "attachment"),
])
def test_attachment_disposition(task_files, filename, mimetype, disposition):
    client, attachment = task_files
    attachment.filename = filename
    response = client.get("/task/1/attachment/1")

    assert response.status_code == 200
    assert response.mimetype == mimetype
    assert response.headers["Content-Disposition"] == f"{disposition}; filename={filename}"
    assert response.get_data() == b"attachment contents"


@pytest.mark.parametrize("kind", ["statement", "attachment"])
def test_file_must_belong_to_task(task_files, kind):
    client, _ = task_files
    assert client.get(f"/task/2/{kind}/1").status_code == 404


@pytest.mark.parametrize("kind", ["statement", "attachment"])
def test_files_require_admin_login(task_files, monkeypatch, kind):
    client, _ = task_files
    monkeypatch.setattr(BaseHandler, "get_current_user", lambda self: None)
    path = f"/task/1/{kind}/1"
    response = client.get(path)

    assert response.status_code == 302
    login_url = urlsplit(urljoin(path, response.location))
    assert login_url.path == "/login"
    assert parse_qs(login_url.query) == {"next": [path]}
