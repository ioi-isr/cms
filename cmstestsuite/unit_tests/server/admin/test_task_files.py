"""Check statement responses and text preview eligibility on admin task pages."""

import io
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urljoin, urlsplit

import pytest
from werkzeug.test import Client
from werkzeug.wrappers import Response

from cms.db import Statement
from cms.server.admin.handlers import HANDLERS
from cms.server.admin.handlers.base import BaseHandler
from cms.server.admin.handlers.task import StatementHandler, _can_preview_task_file
from cms.server.file_middleware import FileServerMiddleware
from tornado.wsgi import WSGIApplication


@pytest.fixture
def statement_client(monkeypatch):
    task = SimpleNamespace(name="example")
    statement = SimpleNamespace(task_id=1, task=task, language="he", digest="pdf")
    admin = SimpleNamespace(permission_all=False)
    monkeypatch.setattr(BaseHandler, "get_current_user", lambda self: admin)
    monkeypatch.setattr(Statement, "get_from_id", lambda ident, session: statement)

    contents = b"%PDF-1.5"
    file_cacher = Mock()
    file_cacher.get_file.side_effect = lambda digest: io.BytesIO(contents)
    file_cacher.get_size.return_value = len(contents)
    application = WSGIApplication([
        route for route in HANDLERS if route[1] is StatementHandler
    ], login_url="/login")
    return Client(FileServerMiddleware(file_cacher, application), Response)


def test_statement_opens_inline(statement_client):
    response = statement_client.get("/task/1/statement/1")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.headers["Content-Disposition"] == "inline; filename=example_he.pdf"
    assert response.get_data() == b"%PDF-1.5"


@pytest.mark.parametrize("filename", [
    "notes.txt", "grader.c", "grader.C", "grader.cc", "grader.cpp", "grader.cxx",
    "task.h", "task.hh", "task.hpp", "task.hxx", "checker.py", "manager.sh",
    "samples.in", "sample.out", "config.yaml",
])
def test_text_file_can_open_in_modal(filename):
    assert _can_preview_task_file(filename)


@pytest.mark.parametrize("filename", [
    "checker", "manager.o", "library.so", "diagram.PNG", "appendix.pdf",
    "grader.zip", "source.docx", "page.html", "vector.svg",
])
def test_other_files_use_download_links(filename):
    assert not _can_preview_task_file(filename)


def test_statement_must_belong_to_task(statement_client):
    assert statement_client.get("/task/2/statement/1").status_code == 404


def test_statement_requires_admin_login(statement_client, monkeypatch):
    monkeypatch.setattr(BaseHandler, "get_current_user", lambda self: None)
    path = "/task/1/statement/1"
    response = statement_client.get(path)

    assert response.status_code == 302
    login_url = urlsplit(urljoin(path, response.location))
    assert login_url.path == "/login"
    assert parse_qs(login_url.query) == {"next": [path]}
