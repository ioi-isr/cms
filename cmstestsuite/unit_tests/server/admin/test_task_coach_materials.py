"""Exercise coach material storage and access through the production routes."""

from io import BytesIO
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from urllib.parse import quote
from zipfile import ZipFile

from werkzeug.datastructures import MultiDict
from werkzeug.test import Client
from werkzeug.wrappers import Response

from cms.db import Admin, TaskCoachMaterial, enumerate_files
from cms.db.filecacher import FileCacher
from cms.server.admin.authentication import AWSAuthMiddleware
from cms.server.admin.handlers import HANDLERS
from cms.server.admin.jinja2_toolbox import AWS_ENVIRONMENT
from cms.server.contest.handlers import HANDLERS as CONTEST_HANDLERS
from cms.server.file_middleware import FileServerMiddleware
from cmscontrib.importing import update_task
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin
from tornado.wsgi import WSGIApplication


class TestCoachMaterials(DatabaseMixin, unittest.TestCase):
    xsrf_token = "0123456789abcdef0123456789abcdef"

    def setUp(self):
        super().setUp()
        cache_dir = self.enterContext(TemporaryDirectory())
        cacher = FileCacher(path=cache_dir)
        app = WSGIApplication(HANDLERS, xsrf_cookies=True)
        auth = AWSAuthMiddleware(FileServerMiddleware(cacher, app))
        self.notifications = Mock()
        app.service = SimpleNamespace(auth_handler=auth, file_cacher=cacher,
                                      jinja2_environment=AWS_ENVIRONMENT,
                                      add_notification=self.notifications)
        self.app = auth
        self.cacher = cacher

        self.task = self.add_task()
        self.task.active_dataset = self.add_dataset(
            task=self.task, task_type="Batch", task_type_parameters=["alone", ["", ""], "diff"],
            score_type="Sum", score_type_parameters=100)
        self.other_task = self.add_task()
        for username, can_edit in [("editor", True), ("reader", False)]:
            self.session.add(Admin(name=username, username=username,
                                   authentication="plaintext:test", permission_all=can_edit))
        self.session.commit()

    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def client(self, username=None):
        client = Client(self.app, Response)
        client.set_cookie("localhost", "_xsrf", self.xsrf_token)
        if username is not None:
            response = client.post("/login", data={
                "_xsrf": self.xsrf_token, "username": username, "password": "test",
            })
            self.assertEqual(response.status_code, 302)
            self.assertTrue(any(header.startswith("awslogin=")
                                for header in response.headers.getlist("Set-Cookie")))
        return client

    def upload(self, client, kind, *files):
        data = MultiDict([("_xsrf", self.xsrf_token)])
        for filename, content in files:
            data.add("material", (BytesIO(content), filename))
        return client.post(f"/task/{self.task.id}/coach_materials/{kind}/upload", data=data)

    def test_coaches_can_download_and_only_editors_can_upload_or_remove(self):
        editor = self.client("editor")
        reader = self.client("reader")
        filename = "פתרון מלא.MD"
        content = b"# Written solution"
        result = self.upload(editor, "written_solution", (filename, content))
        self.assertEqual(result.status_code, 302)
        self.assertTrue(result.location.endswith(f"/task/{self.task.id}#coach-materials"))
        self.assertEqual(self.upload(editor, "editorial",
                                     ("slides.pptx", b"PowerPoint"),
                                     ("slides.pdf", b"PDF")).status_code, 302)

        materials = self.session.query(TaskCoachMaterial).all()
        filenames = {filename, "slides.pptx", "slides.pdf"}
        self.assertEqual({material.filename for material in materials}, filenames)
        material = next(material for material in materials if material.filename == filename)
        download_url = f"/task/{self.task.id}/coach_materials/{material.id}"
        digest = material.digest
        self.assertIn(digest, enumerate_files(self.session))
        self.assertEqual(self.client().get(download_url).status_code, 302)

        download = reader.get(download_url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.get_data(), content)
        self.assertEqual(download.headers["Content-Disposition"],
                         "attachment; filename*=UTF-8''" + quote(filename, safe=""))
        self.assertTrue(download.cache_control.private)
        page = reader.get(f"/task/{self.task.id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(filename, page.get_data(as_text=True))
        self.assertNotIn("coach-material-upload-editorial", page.get_data(as_text=True))

        self.assertEqual(reader.get(
            f"/task/{self.other_task.id}/coach_materials/{material.id}").status_code, 404)
        self.assertEqual(self.upload(reader, "editorial", ("a.pdf", b"a")).status_code, 403)
        xsrf = {"X-XSRFToken": self.xsrf_token}
        self.assertEqual(reader.delete(download_url, headers=xsrf).status_code, 403)
        self.assertEqual(editor.delete(download_url).status_code, 403)

        cws = Client(WSGIApplication(CONTEST_HANDLERS), Response)
        self.assertEqual(cws.get(download_url).status_code, 404)
        self.assertEqual(self.task.attachments, {})
        exported = editor.get(f"/task/{self.task.id}/export")
        self.assertEqual(exported.status_code, 200)
        with ZipFile(BytesIO(exported.get_data())) as archive:
            self.assertFalse(any(name.endswith(tuple(filenames))
                                 for name in archive.namelist()))

        update_task(self.task, self.get_task(name=self.task.name, title="Updated"))
        self.session.commit()
        self.assertEqual({item.filename for item in self.task.coach_materials}, filenames)

        self.assertEqual(editor.delete(download_url, headers=xsrf).status_code, 200)
        self.assertEqual(reader.get(download_url).status_code, 404)
        self.assertNotIn(digest, enumerate_files(self.session))

    def test_invalid_batches_leave_existing_materials_intact(self):
        editor = self.client("editor")
        self.upload(editor, "editorial", ("slides.pdf", b"original"))
        for bad_file in [("slides.pdf", b"replacement"), ("empty.pdf", b""),
                         ("other.md", b"wrong section"), ("../notes.pdf", b"bad name")]:
            with self.subTest(filename=bad_file[0]):
                result = self.upload(editor, "editorial", ("valid.pptx", b"valid"), bad_file)
                self.assertEqual(result.status_code, 302)
        material = self.session.query(TaskCoachMaterial).one()
        self.assertEqual(material.filename, "slides.pdf")
        self.assertEqual(self.cacher.get_file_content(material.digest), b"original")
        self.assertEqual([call.args[1] for call in self.notifications.call_args_list],
                         ["Operation successful."] + ["Upload failed"] * 4)
