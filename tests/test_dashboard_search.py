"""Exercise the generated dashboard in Chrome against a local SQLite-backed API."""

import ast
import asyncio
import errno
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
from urllib.request import build_opener, ProxyHandler
from urllib.parse import urlsplit, parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import pytest
import uvicorn
from websockets.sync.client import connect

from test_webui_blockdata import load_webui


class Browser:
    def __init__(self, connection):
        self.connection = connection
        self.counter = 0

    def command(self, method, **params):
        self.counter += 1
        self.connection.send(json.dumps({'id': self.counter, 'method': method, 'params': params}))
        while True:
            response = json.loads(self.connection.recv(timeout=10))
            if response.get('id') == self.counter:
                assert 'error' not in response, response
                return response.get('result', {})

    def evaluate(self, expression):
        result = self.command('Runtime.evaluate', expression=expression,
                              returnByValue=True, awaitPromise=True)
        assert 'exceptionDetails' not in result, result
        return result.get('result', {}).get('value')

    def wait(self, expression):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.evaluate(expression):
                return
            time.sleep(0.05)
        pytest.fail('Dashboard did not reach expected state: ' + expression)

    def navigate(self, url):
        self.command('Page.navigate', url=url)
        self.wait("document.readyState === 'complete' && !!document.getElementById('secret-input')")

    def login(self, key):
        self.evaluate(f"document.getElementById('secret-input').value = {json.dumps(key)}; login()")


@pytest.fixture(scope='module')
def browser(tmp_path_factory):
    candidates = [shutil.which(name) for name in ('google-chrome', 'chromium', 'chromium-browser')]
    candidates += [str(Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) /
                       'Google/Chrome/Application/chrome.exe')]
    executable = next((path for path in candidates if path and Path(path).is_file()), None)
    if executable is None:
        if os.environ.get('CI'):
            pytest.fail('CI must provide Chrome/Chromium for the dashboard browser tests')
        pytest.skip('Chrome/Chromium is required for dashboard browser tests')
    profile = tmp_path_factory.mktemp('antigrief-chrome').resolve()
    assert profile.is_relative_to(tmp_path_factory.getbasetemp().resolve())
    process = subprocess.Popen([
        executable, '--headless=new', '--disable-gpu', '--no-first-run',
        '--no-default-browser-check', '--disable-background-networking', '--disable-extensions',
        '--no-proxy-server', '--remote-debugging-port=0', '--window-size=1280,900',
        '--user-data-dir=' + str(profile), 'about:blank',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        deadline = time.monotonic() + 15
        port_file = profile / 'DevToolsActivePort'
        while not port_file.exists() and time.monotonic() < deadline:
            assert process.poll() is None, 'Headless Chrome exited before startup'
            time.sleep(0.05)
        port = int(port_file.read_text().splitlines()[0])
        with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}/json/list') as response:
            page = next(target for target in json.load(response) if target['type'] == 'page')
        with connect(page['webSocketDebuggerUrl'], proxy=None) as connection:
            instance = Browser(connection)
            instance.command('Network.enable')
            instance.command('Network.setBlockedURLs', urls=['https://fonts.googleapis.com/*',
                                                              'https://fonts.gstatic.com/*'])
            yield instance
            instance.command('Browser.close')
    finally:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=10)
        # This is a newly created, isolated test profile, never the user's browser profile.
        assert profile.is_relative_to(tmp_path_factory.getbasetemp().resolve())
        for attempt in range(50):
            try:
                shutil.rmtree(profile)
                break
            except FileNotFoundError:
                if not profile.exists():
                    break
            except OSError as error:
                if error.errno not in (errno.ENOTEMPTY, errno.EACCES, errno.EPERM) or attempt == 49:
                    raise
                time.sleep(0.1)


@contextmanager
def dashboard(tmp_path, *, key='test-key', prefix='', fail_logs=False,
              fail_stats=False, delay_player=None):
    webui = load_webui()
    database = tmp_path / 'dashboard.db'
    source = Path(__file__).parents[1] / 'src/endstone_antigrief/antigrief_plugin.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    init = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == 'init_database')
    namespace = {'sqlite3': sqlite3, 'DB_FILE': str(database)}
    exec(compile(ast.Module(body=[init], type_ignores=[]), str(source), 'exec'), namespace)
    connection, _ = namespace['init_database']()
    connection.close()
    with sqlite3.connect(database) as db:
        for player, action, x in [('Alice', 'Container Take', 10), ('Bob', 'Container Add', 50),
                                  ('Alice', 'Interact', 10)]:
            db.execute('INSERT INTO interactions(name,action,x,y,z,type,world,time,blockdata) '
                       'VALUES (?,?,?,?,?,?,?,?,?)',
                       (player, action, x, 64, -20, 'minecraft:diamond', 'overworld',
                        webui.now_est().isoformat(), json.dumps({'container_type': 'minecraft:chest',
                                                               'item': 'minecraft:diamond', 'amount': 3})))
    webui.DB_FILE = str(database)
    webui.get_web_config = lambda: {'secret': key, 'max_results': 10000}
    app = webui.create_app()
    stats_calls = 0

    @app.middleware('http')
    async def fail_search(request: Request, call_next):
        nonlocal stats_calls
        if fail_logs and request.url.path.endswith('/api/logs'):
            return JSONResponse({'detail': 'Test failure'}, status_code=500)
        if request.url.path.endswith('/api/stats'):
            stats_calls += 1
            if fail_stats and stats_calls > 1:
                return JSONResponse({'detail': 'Test failure'}, status_code=500)
        if (delay_player and request.url.path.endswith('/api/logs')
                and request.query_params.get('player') == delay_player):
            await asyncio.sleep(0.3)
        return await call_next(request)

    if prefix:
        parent = FastAPI()
        parent.mount(prefix, app)
        app = parent
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False, ws='none'))
    worker = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    worker.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield f'http://127.0.0.1:{port}{prefix}/', key
    finally:
        server.should_exit = True
        worker.join(timeout=10)
        listener.close()


@pytest.mark.parametrize('prefix,key', [('', 'test-key'), ('/antigrief', 'test-key'),
                                      ('', 'test +&#?= key')])
def test_search_renders_recorded_container_moves(browser, tmp_path, prefix, key):
    with dashboard(tmp_path, prefix=prefix, key=key) as (url, key):
        browser.navigate(url)
        browser.login(key)
        browser.wait("document.querySelectorAll('#logs-body .action-tag').length === 3")
        browser.evaluate("document.getElementById('filter-player').value = ' Alice ';"
                         "document.getElementById('filter-action').value = 'Container'; refreshData(true)")
        browser.wait("document.querySelectorAll('#logs-body .action-tag').length === 1")
        assert 'Container Take' in browser.evaluate("document.getElementById('logs-body').innerText")
        browser.evaluate("document.getElementById('filter-player').value = 'Nobody'; refreshData(true)")
        browser.wait("document.getElementById('logs-body').innerText.includes('No events match')")
        assert browser.evaluate("document.getElementById('dashboard-status').textContent") == 'CONNECTED'
        browser.evaluate("window.open = url => { window.testReportUrl = url; }; openGriefReport('report-1')")
        report_url = urlsplit(browser.evaluate('window.testReportUrl'))
        assert report_url.path == prefix + '/reports/report-1'
        assert parse_qs(report_url.query)['secret'] == [key]


def test_failed_search_displays_an_error_instead_of_an_empty_table(browser, tmp_path):
    with dashboard(tmp_path, fail_logs=True) as (url, key):
        browser.navigate(url)
        browser.login(key)
        browser.wait("document.getElementById('logs-body').innerText.includes('HTTP 500')")
        assert 'Could not load events' in browser.evaluate("document.getElementById('logs-body').innerText")
        assert browser.evaluate("document.getElementById('dashboard-status').textContent") == 'SEARCH FAILED'


def test_enter_submits_search_filters(browser, tmp_path):
    with dashboard(tmp_path) as (url, key):
        browser.navigate(url)
        browser.login(key)
        browser.wait("document.querySelectorAll('#logs-body .action-tag').length === 3")
        browser.evaluate("document.getElementById('filter-player').value = 'Bob';"
                         "document.getElementById('filter-player').focus()")
        browser.command('Input.dispatchKeyEvent', type='keyDown', key='Enter', code='Enter',
                        windowsVirtualKeyCode=13, text='\r')
        browser.command('Input.dispatchKeyEvent', type='keyUp', key='Enter', code='Enter',
                        windowsVirtualKeyCode=13)
        browser.wait("document.querySelectorAll('#logs-body .action-tag').length === 1")
        assert 'Bob' in browser.evaluate("document.getElementById('logs-body').innerText")


def test_area_search_supports_commas_and_zero_radius(browser, tmp_path):
    with dashboard(tmp_path) as (url, key):
        browser.navigate(url)
        browser.login(key)
        browser.wait("document.querySelectorAll('#logs-body .action-tag').length === 3")
        browser.evaluate("document.getElementById('filter-coords').value = '10, 64, -20, 0'; refreshData(true)")
        browser.wait("document.querySelectorAll('#logs-body .action-tag').length === 2")


def test_invalid_area_is_reported_before_search(browser, tmp_path):
    with dashboard(tmp_path) as (url, key):
        browser.navigate(url)
        browser.login(key)
        browser.wait("document.querySelectorAll('#logs-body .action-tag').length === 3")
        browser.evaluate("document.getElementById('filter-coords').value = '10 64 -20 nope'; refreshData(true)")
        browser.wait("document.getElementById('logs-body').innerText.includes('radius')")


def test_statistics_failure_does_not_block_event_search(browser, tmp_path):
    with dashboard(tmp_path, fail_stats=True) as (url, key):
        browser.navigate(url)
        browser.login(key)
        browser.wait("document.querySelectorAll('#logs-body .action-tag').length === 3")
        assert 'Container Take' in browser.evaluate("document.getElementById('logs-body').innerText")


def test_slow_previous_search_does_not_replace_newer_results(browser, tmp_path):
    with dashboard(tmp_path, delay_player='Alice') as (url, key):
        browser.navigate(url)
        browser.login(key)
        browser.wait("document.querySelectorAll('#logs-body .action-tag').length === 3")
        browser.evaluate("document.getElementById('filter-player').value = 'Alice'; void refreshData(true)")
        browser.evaluate("document.getElementById('filter-player').value = 'Bob'; void refreshData(true)")
        browser.wait("document.querySelectorAll('#logs-body .action-tag').length === 1")
        time.sleep(0.4)
        assert 'Bob' in browser.evaluate("document.getElementById('logs-body').innerText")
        assert 'Alice' not in browser.evaluate("document.getElementById('logs-body').innerText")
