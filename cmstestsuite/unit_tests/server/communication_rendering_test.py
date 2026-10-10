"""Mixed-direction communication rendering in both web interfaces."""

from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
import pytest

from cms.server.jinja2_toolbox import markdown_filter


ROOT = Path(__file__).resolve().parents[3] / 'cms' / 'server'
TEXT = 'שלום, בדקו את **Task A** (test.cpp)!\n\nEnglish paragraph (123).'


def template_environment(interface):
    """Build a minimal template environment with communication dependencies."""
    env = Environment(
        loader=ChoiceLoader([
            DictLoader({'contest.html': '{% block core %}{% endblock %}'}),
            FileSystemLoader(str(ROOT / interface / 'templates')),
        ]), autoescape=True, extensions=['jinja2.ext.i18n'])
    env.install_null_translations()
    env.filters.update(markdown=markdown_filter,
                       format_datetime=lambda value: '12:34',
                       format_datetime_smart=lambda value: '12:34',
                       format_datetime_input=lambda value, zone: '12:34')
    env.globals.update(
        admin=SimpleNamespace(permission_all=True), xsrf_form_html='',
        contest_url=lambda *args: '/question')
    return env


def assert_auto(elements):
    """Require every selected content element to detect its own direction."""
    assert elements
    assert all(element.get('dir') == 'auto' for element in elements)


@pytest.mark.parametrize('text, selector', [
    (TEXT, 'p'),
    ('# שלום Task A\n\n## English', 'h1, h2'),
    ('- שלום Task A\n- English', 'ul, li'),
    ('1. שלום Task A\n2. English', 'ol, li'),
    ('> שלום Task A\n>\n> English', 'blockquote, p'),
    ('| שלום | English |\n| --- | --- |\n| Task A | בדיקה |', 'th, td'),
])
def test_markdown_blocks_choose_direction_independently(text, selector):
    """Check direction detection for each kind of Markdown text block."""
    html = BeautifulSoup(markdown_filter(text), 'html.parser')
    assert_auto(html.select(selector))


def test_markdown_preserves_formatting_and_escapes_html():
    """Keep emphasis while preventing authored HTML from becoming markup."""
    html = BeautifulSoup(markdown_filter(TEXT + '\n\n<script>alert(1)</script>'),
                         'html.parser')
    assert html.strong.text == 'Task A'
    assert html.find('script') is None
    assert '<script>alert(1)</script>' in html.get_text()


def test_markdown_preserves_code_and_links():
    """Preserve code and link content without adding direction to code blocks."""
    html = BeautifulSoup(markdown_filter(
        'שלום `a < b` [Task A](https://example.org)\n\n'
        '```cpp\nint main() { return 0; }\n```'), 'html.parser')
    assert html.code.text == 'a < b'
    assert html.a['href'] == 'https://example.org'
    assert html.pre.code.text == 'int main() { return 0; }\n'
    assert html.pre.get('dir') is None


def test_admin_composition_editing_and_preview():
    """Cover direction detection in admin subjects, editors, and previews."""
    env = template_environment('admin')
    cards = env.get_template('macro/content_card.html').make_module()
    html = BeautifulSoup(
        cards.new_item_card('New', '/add') + cards.edit_form('/edit', 'Save'),
        'html.parser')
    assert_auto(html.select('input[type=text], textarea, .markdown_preview'))
    header = BeautifulSoup(cards.card_header('שלום Task A', None),
                           'html.parser')
    assert_auto(header.select('.question-subject'))


def test_contest_communications_and_question_editor():
    """Cover announcement, message, question, reply, and editor rendering."""
    env = template_environment('contest')
    message = SimpleNamespace(subject='שלום Task A', text=TEXT,
                              timestamp=None, question_timestamp=None,
                              reply_subject='English שלום', reply_text=TEXT,
                              reply_timestamp=1)
    html = BeautifulSoup(env.get_template('communication.html').render(
        visible_announcements=[message],
        participation=SimpleNamespace(messages=[message], questions=[message]),
        questions_enabled=True, actual_phase=-1,
        contest=SimpleNamespace(allow_delay_requests=False)), 'html.parser')
    for selector in ['.announcement', '.message', '.question', '.answer']:
        assert_auto(html.select(selector + ' .subject, ' + selector + ' .body'))
    assert_auto(html.select('input[name=question_subject], textarea'))
    assert len(html.select('.body p[dir=auto]')) == 6
