"""Exercise the production task handlers and tag rendering over HTTP."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from cms.db import Task
from cms.server.admin.handlers.base import BaseHandler
from cms.server.admin.handlers.task import TaskHandler, TaskListHandler
import tornado.testing
from tornado.web import Application


class TaskHarness(BaseHandler):
    get = TaskListHandler.get
    post = TaskHandler.post

    def initialize(self, fixture):
        self.fixture = fixture

    def prepare(self):
        self.sql_session.close()
        self.sql_session = Mock()
        self.url = lambda *parts: '/' + '/'.join(map(str, parts))

    def on_finish(self):
        pass

    def get_current_user(self):
        return self.fixture.admin

    @property
    def service(self):
        return self.fixture.service

    def render_params(self):
        return dict(task_list=[self.fixture.task], admin=self.fixture.admin,
                    url=self.url)

    def render(self, template_name, **params):
        template = self.fixture.templates.get_template(template_name)
        self.write(template.render(**params))

    def safe_get_item(self, cls, task_id):
        return self.fixture.task

    def try_commit(self):
        return True


class TestTaskTags(tornado.testing.AsyncHTTPTestCase):
    def runTest(self):
        pass

    def get_app(self):
        self.admin = SimpleNamespace(permission_all=True)
        self.task = Task(name="example", title="Example", tags=["graphs"],
                         visible_to_tags=["advanced"])
        self.task.id = 7
        self.service = SimpleNamespace(
            proxy_service=Mock(), add_notification=Mock())
        templates_path = (Path(__file__).resolve().parents[4]
                          / "cms/server/admin/templates")
        self.templates = Environment(loader=ChoiceLoader([
            DictLoader({
                'base.html': ('{% block js_init %}{% endblock %}'
                              '{% block core %}{% endblock %}'),
                'fragments/modal_import_task.html': '',
            }), FileSystemLoader(str(templates_path)),
        ]), autoescape=True)
        return Application([
            (r'/tasks', TaskHarness, {'fixture': self}),
            (r'/task/(\d+)', TaskHarness, {'fixture': self}),
        ], login_url='/login')

    def post_tags(self, params):
        return self.fetch('/task/7', method='POST', body=urlencode(params),
                          follow_redirects=False)

    def test_save_normalizes_custom_tags_without_changing_visibility(self):
        response = self.post_tags({
            'tags': ' Graphs, DP, graphs, , checker, batch '})
        self.assertEqual(response.code, 302)
        self.assertEqual(self.task.tags, ['graphs', 'dp'])
        self.assertEqual(self.task.visible_to_tags, ['advanced'])
        self.service.add_notification.assert_not_called()
        self.service.proxy_service.dataset_updated.assert_called_once_with(
            task_id=7)

    def test_missing_tags_preserves_them_and_empty_tags_clears_them(self):
        self.assertEqual(self.post_tags({}).code, 302)
        self.assertEqual(self.task.tags, ['graphs'])
        self.assertEqual(self.post_tags({'tags': ''}).code, 302)
        self.assertEqual(self.task.tags, [])

    def test_read_only_admin_cannot_edit_tags(self):
        self.admin.permission_all = False
        self.assertEqual(self.post_tags({'tags': 'dp'}).code, 403)
        self.assertEqual(self.task.tags, ['graphs'])

    def test_list_renders_exact_tags_and_escapes_custom_text(self):
        self.admin.permission_all = False
        unusual_tag = "שלום ' <img src=x onerror=alert(1)>"
        self.task.tags = ['graphs', unusual_tag]
        response = self.fetch('/tasks')
        self.assertEqual(response.code, 200)
        html = BeautifulSoup(response.body, 'html.parser')
        card = html.select_one('#tasks-grid .tp-program-card')
        self.assertEqual(json.loads(card['data-tags']), self.task.all_tags)
        self.assertEqual([tag.text for tag in card.select('.tag')],
                         self.task.all_tags)
        self.assertIsNone(card.find('img'))

    def test_list_requires_login(self):
        self.admin = None
        self.assertEqual(self.fetch('/tasks', follow_redirects=False).code, 302)
