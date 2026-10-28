"""Run container events through the real diff, persistence, and dashboard code."""

import ast
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from test_blockdata_adapter import load_adapter, snapshot
from test_webui_blockdata import load_webui


SOURCE = Path(__file__).parents[1] / 'src/endstone_antigrief/antigrief_plugin.py'


@pytest.fixture
def plugin(tmp_path):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    plugin_class = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                        and node.name == 'AntiGriefPlugin')
    method_names = {
        '_snapshot_player_inventory', '_diff_player_inventory', '_queue_container_capture',
        'on_packet_receive', 'on_packet_send', 'on_player_interact', 'on_player_quit',
        '_queue_container_snapshot', '_start_container_tracking', 'on_disable',
        '_is_container_block_type', 'on_block_place',
    }
    methods = [deepcopy(node) for node in plugin_class.body
               if isinstance(node, ast.FunctionDef) and node.name in method_names]
    for method in methods:
        method.decorator_list = [decorator for decorator in method.decorator_list
                                 if isinstance(decorator, ast.Name) and decorator.id == 'staticmethod']
        method.returns = None
        for argument in method.args.args:
            argument.annotation = None
    functions = {
        'init_database', '_sqlite_text', '_sqlite_signed_int', 'insert_records',
        'insert_container_snapshots', 'insert_player_inventory_snapshots',
        'update_player_inventory_presence', '_take_buffer', '_requeue_buffer',
        'flush_data_to_db',
    }
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name in functions]
    nodes.insert(0, next(node for node in tree.body if isinstance(node, ast.Assign)
                         and any(isinstance(target, ast.Name) and target.id == 'CONTAINER_BLOCKS'
                                 for target in node.targets)))
    nodes.append(ast.ClassDef(name='Harness', bases=[], keywords=[], body=methods,
                              decorator_list=[]))
    adapter = load_adapter()
    namespace = {
        'sqlite3': sqlite3, 'DB_FILE': str(tmp_path / 'agdata.db'), 'json': json,
        'uuid4': uuid4, 'BlockDataAdapter': adapter, 'STORE_RAW_SNBT': True,
        'now_est': lambda: datetime.now(timezone.utc),
        'tm': SimpleNamespace(time=lambda: 100, monotonic=lambda: 100), 'container_snapshots': {},
        'data_buffers': defaultdict(list), 'buffer_lock': threading.Lock(),
        'db_write_lock': threading.Lock(), 'is_cleaning': False,
        'CAPTURE_CONTAINER_OPEN_CLOSE': True, 'lang': {'action_interact': 'Interact'},
        'CONTAINER_OWNERSHIP_ENABLED': True,
        'PlayerInteractEvent': SimpleNamespace(RIGHT_CLICK_BLOCK='RIGHT_CLICK_BLOCK'),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 str(SOURCE), 'exec'), namespace)
    connection, _ = namespace['init_database']()
    connection.close()
    instance = namespace['Harness']()
    instance.namespace = namespace
    instance.logger = Mock()
    instance.blockdata = adapter()
    instance._blockdata_ready = True
    instance._shutting_down = False
    instance.tasks = []
    instance.current = snapshot(1, [])
    instance.player = SimpleNamespace(
        name='Player', location=SimpleNamespace(dimension=SimpleNamespace(name='overworld')),
        inventory=SimpleNamespace(contents=[]),
    )
    instance.server = SimpleNamespace(
        get_player=lambda name: instance.player,
        scheduler=SimpleNamespace(run_task=lambda owner, task, **kw: instance.tasks.append(task)),
    )
    instance._is_container_authorized = lambda *args: ('Owner', False)
    instance._capture_player_inventory = Mock()
    instance._mark_player_inventory_offline = Mock()

    def capture(dimension, x, y, z, name, reason, store=True):
        result = deepcopy(instance.current)
        result['location'] = {'dimension': dimension, 'x': x, 'y': y, 'z': z}
        return result, instance._queue_container_snapshot(result, name, reason) if store else None

    instance._capture_native_snapshot = Mock(side_effect=capture)
    return instance


def open_container(plugin, x=1, kind='minecraft:chest'):
    plugin.on_player_interact(SimpleNamespace(
        player=plugin.player, block=SimpleNamespace(type=kind, x=x, y=64, z=2),
        action='RIGHT_CLICK_BLOCK', is_cancelled=False,
    ))


def receive(plugin, packet_id):
    plugin.on_packet_receive(SimpleNamespace(
        player=plugin.player, packet_id=packet_id, is_cancelled=False,
    ))


def send(plugin, packet_id):
    plugin.on_packet_send(SimpleNamespace(
        player=plugin.player, packet_id=packet_id, is_cancelled=False,
    ))


def run_tasks(plugin):
    tasks, plugin.tasks = plugin.tasks, []
    for task in tasks:
        task()


def set_items(plugin, count):
    entries = [{'slot': 0, 'item': {
        'Name': 'minecraft:diamond', 'Count': count,
        'tag': {'display': {'Name': 'Saved diamond'}},
    }}] if count else []
    plugin.current = snapshot(plugin.current['revision'] + 1, entries)


@pytest.mark.parametrize('packet_id', [30, 147])
def test_moves_are_logged_before_close_and_visible_in_dashboard(plugin, packet_id):
    open_container(plugin)
    opening_session = plugin.namespace['container_snapshots']['Player']
    # The packet event runs before BDS commits the transaction.
    receive(plugin, packet_id)
    assert not plugin.namespace['data_buffers']['container_access']
    set_items(plugin, 8)
    run_tasks(plugin)
    assert plugin.namespace['container_snapshots']['Player'] is opening_session
    receive(plugin, packet_id)
    set_items(plugin, 3)
    run_tasks(plugin)
    # Closing must not duplicate the already captured changes.
    receive(plugin, 47)
    run_tasks(plugin)
    assert 'Player' not in plugin.namespace['container_snapshots']
    plugin.namespace['flush_data_to_db']()
    webui = load_webui()
    webui.DB_FILE = plugin.namespace['DB_FILE']
    webui.get_web_config = lambda: {'secret': 'secret', 'max_results': 10000}
    response = TestClient(webui.create_app()).get('/api/logs?secret=secret&action=Container')
    assert response.status_code == 200
    logs = sorted(response.json()['logs'], key=lambda row: row['id'])
    assert [row['action'] for row in logs] == ['Container Add', 'Container Take']
    payloads = [json.loads(row['blockdata']) for row in logs]
    assert [payload['amount'] for payload in payloads] == [8, 5]
    assert payloads[1]['before_item']['Count'] == 8
    assert payloads[1]['after_item']['tag']['display']['Name'] == 'Saved diamond'
    assert all(payload['before_snapshot_id'] and payload['after_snapshot_id'] for payload in payloads)
    assert all(payload['unauthorized'] for payload in payloads)


def test_old_close_callback_does_not_consume_reopened_container(plugin):
    open_container(plugin)
    set_items(plugin, 6)
    receive(plugin, 47)
    open_container(plugin, x=10)
    new_session = plugin.namespace['container_snapshots']['Player']
    run_tasks(plugin)
    assert plugin.namespace['container_snapshots'].get('Player') is new_session
    records = plugin.namespace['data_buffers']['container_access']
    assert [(row['coordinates']['x'], row['action']) for row in records] == [(1, 'Container Add')]


@pytest.mark.parametrize('store_snbt', [True, False])
def test_native_069_fields_reach_sqlite_and_dashboard(plugin, store_snbt):
    plugin.namespace['STORE_RAW_SNBT'] = store_snbt

    def use_native_fields():
        entity = plugin.current['block_entity']
        entity['canonical'] = entity.pop('canonical_nbt')
        entity['snbt'] = '{CustomName:"Vault"}'

    use_native_fields()
    open_container(plugin)
    receive(plugin, 147)
    set_items(plugin, 4)
    use_native_fields()
    run_tasks(plugin)
    payload = json.loads(plugin.namespace['data_buffers']['container_access'][0]['blockdata'])
    assert payload['canonical_nbt'] is True
    plugin.namespace['flush_data_to_db']()

    webui = load_webui()
    webui.DB_FILE = plugin.namespace['DB_FILE']
    webui.get_web_config = lambda: {'secret': 'secret', 'max_results': 10000}
    response = TestClient(webui.create_app()).get(
        f"/api/container-snapshots/{payload['after_snapshot_id']}?secret=secret"
    )
    assert response.status_code == 200
    saved = response.json()
    assert saved['metadata']['canonical_nbt'] is True
    assert saved['raw_snbt'] == ('{CustomName:"Vault"}' if store_snbt else '')
    entity = saved['snapshot']['block_entity']
    assert entity['canonical'] is True
    assert ('snbt' in entity) is store_snbt
    assert 'raw_snbt' not in entity
    assert plugin.current['block_entity']['snbt'] == '{CustomName:"Vault"}'
    assert entity['inventory'][0]['item']['tag']['display']['Name'] == 'Saved diamond'


def test_add_then_take_before_close_keeps_both_changes(plugin):
    open_container(plugin)
    receive(plugin, 147)
    set_items(plugin, 4)
    run_tasks(plugin)
    receive(plugin, 147)
    set_items(plugin, 0)
    run_tasks(plugin)
    receive(plugin, 47)
    run_tasks(plugin)
    assert [row['action'] for row in plugin.namespace['data_buffers']['container_access']] == [
        'Container Add', 'Container Take',
    ]


def test_disconnect_captures_pending_container_changes(plugin):
    open_container(plugin)
    set_items(plugin, 4)
    plugin.on_player_quit(SimpleNamespace(player=plugin.player))
    assert 'Player' not in plugin.namespace['container_snapshots']
    assert [row['action'] for row in plugin.namespace['data_buffers']['container_access']] == ['Container Add']


def test_authoritative_response_also_captures_embedded_requests(plugin):
    open_container(plugin)
    set_items(plugin, 4)
    send(plugin, 148)
    run_tasks(plugin)
    assert [row['action'] for row in plugin.namespace['data_buffers']['container_access']] == ['Container Add']
    assert 'Player' in plugin.namespace['container_snapshots']


@pytest.mark.parametrize('server_close', [False, True])
def test_close_without_transaction_notification_still_records_final_state(plugin, server_close):
    open_container(plugin)
    set_items(plugin, 5)
    (send if server_close else receive)(plugin, 47)
    run_tasks(plugin)
    assert 'Player' not in plugin.namespace['container_snapshots']
    assert [row['action'] for row in plugin.namespace['data_buffers']['container_access']] == ['Container Add']


def test_request_response_and_close_share_one_capture(plugin):
    open_container(plugin)
    receive(plugin, 147)
    set_items(plugin, 2)
    send(plugin, 148)
    receive(plugin, 47)
    send(plugin, 47)
    assert len(plugin.tasks) == 1
    run_tasks(plugin)
    assert 'Player' not in plugin.namespace['container_snapshots']
    assert len(plugin.namespace['data_buffers']['container_access']) == 1


def test_rejected_request_adds_no_event_or_redundant_snapshot(plugin):
    open_container(plugin)
    opening_session = plugin.namespace['container_snapshots']['Player']
    for _ in range(3):
        receive(plugin, 147)
        send(plugin, 148)
        run_tasks(plugin)
    assert plugin.namespace['container_snapshots']['Player'] is opening_session
    assert not plugin.namespace['data_buffers']['container_access']
    assert len(plugin.namespace['data_buffers']['container_snapshot']) == 1


def test_noop_requests_and_close_skip_diff_and_snapshot_serialization(plugin):
    open_container(plugin)
    plugin.blockdata.diff_inventory = Mock(wraps=plugin.blockdata.diff_inventory)
    plugin._queue_container_snapshot = Mock(wraps=plugin._queue_container_snapshot)
    for _ in range(20):
        receive(plugin, 147)
        send(plugin, 148)
        run_tasks(plugin)
    receive(plugin, 47)
    run_tasks(plugin)
    plugin.blockdata.diff_inventory.assert_not_called()
    plugin._queue_container_snapshot.assert_not_called()
    assert not plugin.namespace['container_snapshots']


def test_container_access_is_quiet_and_keeps_unauthorized_audit_records(plugin):
    open_container(plugin)
    set_items(plugin, 4)
    receive(plugin, 147)
    run_tasks(plugin)
    receive(plugin, 47)
    run_tasks(plugin)
    plugin.logger.info.assert_not_called()
    plugin.logger.warning.assert_not_called()
    records = plugin.namespace['data_buffers']['container_access']
    assert [record['action'] for record in records] == ['Container Add']
    assert json.loads(records[0]['blockdata'])['unauthorized'] is True


@pytest.mark.parametrize('kind', ['minecraft:stone', 'minecraft:dirt', 'minecraft:oak_planks'])
def test_ordinary_placement_does_not_schedule_native_container_checks(plugin, kind):
    plugin.namespace['lang']['action_place'] = 'Place'
    plugin.blockdata.capture = Mock(side_effect=AssertionError('No container capture needed'))
    block = SimpleNamespace(type=kind, x=1, y=64, z=2)
    plugin.on_block_place(SimpleNamespace(
        player=plugin.player, block=block, block_placed_state=SimpleNamespace(type=kind),
    ))
    assert not plugin.tasks
    plugin.blockdata.capture.assert_not_called()
    assert plugin.namespace['data_buffers']['place'][0]['type'] == kind


def test_container_placement_still_registers_owner_without_console_output(plugin):
    plugin.namespace['lang']['action_place'] = 'Place'
    plugin._set_container_owner = Mock()
    block = SimpleNamespace(type='minecraft:chest', x=1, y=64, z=2)
    plugin.on_block_place(SimpleNamespace(
        player=plugin.player, block=block, block_placed_state=block,
    ))
    run_tasks(plugin)
    plugin._set_container_owner.assert_called_once_with(
        'overworld', 1, 64, 2, 'Player', 'container_placement',
    )
    plugin.logger.info.assert_not_called()


def test_duplicate_interaction_keeps_original_baseline(plugin):
    open_container(plugin)
    opening_session = plugin.namespace['container_snapshots']['Player']
    set_items(plugin, 4)
    open_container(plugin)
    assert plugin.namespace['container_snapshots']['Player'] is opening_session
    receive(plugin, 47)
    run_tasks(plugin)
    assert [row['action'] for row in plugin.namespace['data_buffers']['container_access']] == ['Container Add']


@pytest.mark.parametrize('action,cancelled', [('LEFT_CLICK_BLOCK', False), ('RIGHT_CLICK_BLOCK', True)])
def test_non_opening_interaction_does_not_start_tracking(plugin, action, cancelled):
    plugin.on_player_interact(SimpleNamespace(
        player=plugin.player, block=SimpleNamespace(type='minecraft:chest', x=1, y=64, z=2),
        action=action, is_cancelled=cancelled,
    ))
    assert not plugin.namespace['container_snapshots']


def test_legacy_inventory_fallback_keeps_a_running_baseline(plugin):
    plugin._capture_native_snapshot = Mock(return_value=(None, None))
    open_container(plugin)
    receive(plugin, 30)
    plugin.player.inventory.contents = [SimpleNamespace(type='minecraft:diamond', amount=4)]
    run_tasks(plugin)
    receive(plugin, 30)
    plugin.player.inventory.contents = []
    run_tasks(plugin)
    receive(plugin, 47)
    run_tasks(plugin)
    # The fallback reads the player's inventory; increasing it means a take.
    assert [row['action'] for row in plugin.namespace['data_buffers']['container_access']] == [
        'Container Take', 'Container Add',
    ]


def test_shutdown_flushes_pending_container_history_to_sqlite(plugin):
    open_container(plugin)
    receive(plugin, 147)
    set_items(plugin, 5)
    plugin.on_disable()
    run_tasks(plugin)
    assert not plugin.namespace['container_snapshots']
    with sqlite3.connect(plugin.namespace['DB_FILE']) as db:
        assert db.execute("SELECT action FROM interactions WHERE action LIKE 'Container%'").fetchall() == [
            ('Container Add',),
        ]


def test_native_tracking_recovers_connection_at_close(plugin):
    open_container(plugin)
    set_items(plugin, 4)
    plugin._blockdata_ready = False
    receive(plugin, 47)
    run_tasks(plugin)
    assert [row['action'] for row in plugin.namespace['data_buffers']['container_access']] == ['Container Add']


@pytest.mark.parametrize('waxed', ['', 'waxed_'])
@pytest.mark.parametrize('oxidation', ['', 'exposed_', 'weathered_', 'oxidized_'])
def test_copper_chest_variants_capture_item_moves(plugin, waxed, oxidation):
    kind = f'minecraft:{waxed}{oxidation}copper_chest'
    plugin.current['type'] = kind
    open_container(plugin, kind=kind)
    receive(plugin, 147)
    set_items(plugin, 7)
    plugin.current['type'] = kind
    run_tasks(plugin)
    records = plugin.namespace['data_buffers']['container_access']
    assert [row['action'] for row in records] == ['Container Add']
    assert json.loads(records[0]['blockdata'])['container_type'] == kind
    assert plugin.namespace['container_snapshots']['Player']['block_type'] == kind


@pytest.mark.parametrize('kind', ['minecraft:oak_shelf', 'minecraft:chiseled_bookshelf',
                                 'minecraft:decorated_pot', 'minecraft:lit_furnace',
                                 'minecraft:lit_blast_furnace', 'minecraft:lit_smoker'])
def test_interaction_alone_captures_committed_container_changes(plugin, kind):
    plugin.current['type'] = kind
    open_container(plugin, kind=kind)
    # No window-close packet or stack notification is needed for this path.
    set_items(plugin, 1)
    plugin.current['type'] = kind
    run_tasks(plugin)
    records = plugin.namespace['data_buffers']['container_access']
    assert [row['action'] for row in records] == ['Container Add']
    assert json.loads(records[0]['blockdata'])['container_type'] == kind
