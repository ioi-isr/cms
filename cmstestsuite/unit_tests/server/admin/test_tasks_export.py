"""Exercise task export downloads through their production HTTP handler."""

import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import zipfile

from openpyxl import load_workbook
import yaml

from cms.db import Dataset, Statement, Task
from cms.server.admin.handlers import HANDLERS
from cms.server.admin.handlers.excel import ExcelExportMixin
from cms.server.admin.handlers.export_handlers import ExportTasksHandler
import tornado.testing
from tornado.web import Application, RequestHandler


class ExportHarness(ExcelExportMixin, RequestHandler):
    get = ExportTasksHandler.get

    def initialize(self, fixture):
        self.fixture = fixture
        self.sql_session = Mock()
        self.sql_session.query.return_value.order_by.return_value.all.return_value = fixture.tasks
        self.service = fixture.service
        self.url = lambda *parts: '/' + '/'.join(parts)

    def get_current_user(self):
        return self.fixture.admin


class TestTasksExport(tornado.testing.AsyncHTTPTestCase):
    # Tornado 4.5 expects runTest when pytest inspects unittest classes.
    def runTest(self):
        pass

    def get_app(self):
        self.tasks = []
        self.admin = SimpleNamespace(permission_all=False)
        self.service = SimpleNamespace(file_cacher=Mock(), add_notification=Mock())
        self.service.file_cacher.get_file_to_path.side_effect = (
            lambda digest, path: Path(path).write_bytes(digest.encode()))
        pattern = next(pattern for pattern, *rest in HANDLERS
                       if rest[0] is ExportTasksHandler)
        return Application([(pattern, ExportHarness, {'fixture': self})],
                           login_url='/login')

    def task(self, task_id=7, name='example', dataset=True):
        task = Task(name=name, title='כותרת')
        task.id = task_id
        Statement(task=task, language='he', digest='hebrew',
                  source_digest='source', source_extension='.docx')
        Statement(task=task, language='en', digest='english')
        if dataset:
            task.active_dataset = Dataset(
                task=task, description='Default', task_type='Batch',
                task_type_parameters=['alone', ['', ''], 'diff'],
                score_type='Sum', score_type_parameters=100)
        self.tasks.append(task)
        return task

    def archive(self, kind):
        response = self.fetch('/tasks/export/' + kind)
        self.assertEqual(response.code, 200)
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        self.assertIn('tasks_' + kind + '.zip',
                      response.headers['Content-Disposition'])
        return zipfile.ZipFile(io.BytesIO(response.body))

    def test_metadata_uses_admin_id_and_literal_text(self):
        task = self.task(name='=1+1')
        response = self.fetch('/tasks/export/metadata')
        self.assertEqual(response.code, 200)
        self.assertIn('tasks.xlsx', response.headers['Content-Disposition'])
        sheet = load_workbook(io.BytesIO(response.body)).active
        self.assertEqual(list(sheet.values), [
            ('Task name', 'Title', 'ID'), (task.name, task.title, task.id)])
        self.assertEqual(sheet['A2'].data_type, 's')
        self.service.file_cacher.get_file_to_path.assert_not_called()

    def test_statements_all_languages_sources_and_duplicate_names(self):
        self.task(dataset=False)
        self.task(task_id=8, dataset=False)
        with self.archive('statements') as archive:
            self.assertEqual(set(archive.namelist()), {
                f'tasks/{task_id}_example/{filename}'
                for task_id in (7, 8)
                for filename in ('hebrew.pdf', 'hebrew.docx', 'english.pdf')})
            self.assertEqual(archive.read('tasks/7_example/hebrew.docx'), b'source')

    def test_backup_reuses_task_package_export(self):
        self.task()
        self.task(task_id=8)
        with self.archive('backup') as archive:
            for task_id in (7, 8):
                base = f'tasks/{task_id}_example/'
                config = yaml.safe_load(archive.read(base + 'task.yaml'))
                self.assertEqual(config['name'], 'example')
                self.assertEqual(config['title'], 'כותרת')
                self.assertEqual(archive.read(base + 'statements/english.pdf'), b'english')
                with zipfile.ZipFile(io.BytesIO(archive.read(base + 'tests.zip'))) as tests:
                    self.assertEqual(tests.namelist(), [])

    def test_backup_fails_if_any_task_has_no_dataset(self):
        self.task(dataset=False)
        response = self.fetch('/tasks/export/backup', follow_redirects=False)
        self.assertEqual(response.code, 302)
        self.assertEqual(response.headers['Location'], '/tasks')
        self.assertIn('example', self.service.add_notification.call_args.args[2])

    def test_empty_system_exports(self):
        response = self.fetch('/tasks/export/metadata')
        sheet = load_workbook(io.BytesIO(response.body)).active
        self.assertEqual(sheet.max_row, 1)
        for kind in ('statements', 'backup'):
            with self.archive(kind) as archive:
                self.assertEqual(archive.namelist(), [])

    def test_download_requires_login(self):
        self.admin = None
        for kind in ('metadata', 'statements', 'backup'):
            response = self.fetch('/tasks/export/' + kind, follow_redirects=False)
            self.assertEqual(response.code, 302)
            self.assertTrue(response.headers['Location'].startswith('/login'))

    def test_failed_file_read_cleans_temporary_directory(self):
        self.task()
        self.service.file_cacher.get_file_to_path.side_effect = OSError('missing file')
        from cms.server.admin.handlers import export_handlers
        original = export_handlers.tempfile.TemporaryDirectory
        directories = []

        def temporary_directory(**kwargs):
            directory = original(**kwargs)
            directories.append(directory.name)
            return directory

        with patch.object(export_handlers.tempfile, 'TemporaryDirectory', temporary_directory):
            response = self.fetch('/tasks/export/statements', follow_redirects=False)
        self.assertEqual(response.code, 302)
        self.assertTrue(directories)
        self.assertTrue(all(not Path(path).exists() for path in directories))

    def test_unknown_export_type(self):
        self.assertEqual(self.fetch('/tasks/export/unknown').code, 404)
