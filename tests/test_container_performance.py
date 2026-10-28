"""Check bounded work, ownership freshness, and scheduled inventory capture."""

import ast
from collections import defaultdict, OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from test_blockdata_adapter import snapshot


SOURCE = Path(__file__).parents[1] / 'src/endstone_antigrief/antigrief_plugin.py'


@pytest.fixture
def plugin(tmp_path):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    plugin_class = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef) and node.name == 'AntiGriefPlugin')
    names = {
        '_normalise_world_key', '_container_position_key', '_is_container_block_type',
        '_set_container_owner', '_clear_container_owner', '_set_container_trust',
        '_infer_container_owner_from_logs', '_container_access_info', '_get_container_owner',
        '_is_container_authorized', '_start_player_inventory_snapshot_sweeper',
        '_queue_player_inventory_snapshot', '_player_inventory_key',
    }
    methods = [node for node in plugin_class.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    nodes = [next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == 'init_database')]
    nodes.append(next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'CONTAINER_BLOCKS'
                              for target in node.targets)))
    nodes.append(ast.ClassDef(name='Harness', bases=[], keywords=[], body=methods, decorator_list=[]))
    clock = SimpleNamespace(value=100.0)
    namespace = {
        'sqlite3': sqlite3, 'DB_FILE': str(tmp_path / 'agdata.db'), 'json': json, 'uuid4': uuid4,
        'now_est': lambda: datetime.now(timezone.utc),
        'tm': SimpleNamespace(monotonic=lambda: clock.value),
        'CONTAINER_OWNERSHIP_ENABLED': True, 'CONTAINER_ACCESS_CACHE_SIZE': 3,
        'CONTAINER_ACCESS_CACHE_TTL': 30.0, 'CAPTURE_PLAYER_INVENTORIES': True,
        'PLAYER_INVENTORY_CAPTURE_TICKS': 100, 'PLAYER_INVENTORY_CAPTURE_BATCH_SIZE': 3,
        'data_buffers': defaultdict(list),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 str(SOURCE), 'exec'), namespace)
    connection, _ = namespace['init_database']()
    connection.close()
    instance = namespace['Harness']()
    instance.namespace = namespace
    instance.clock = clock
    instance._container_access_cache = OrderedDict()
    instance._blockdata_ready = True
    instance._shutting_down = False
    instance._player_inventory_sweeper_started = False
    instance._player_inventory_cursor = 0
    instance._last_player_inventory_revisions = {}
    instance._player_inventory_keys_by_name = {}
    instance.blockdata = SimpleNamespace(player_inventory_available=True)
    instance._capture_player_inventory = Mock()
    instance.players = {name: SimpleNamespace(name=name) for name in ('Alice', 'Bob', 'Chris')}
    instance.tasks = []
    instance.server = SimpleNamespace(
        online_players=list(instance.players.values()),
        get_player=lambda name: instance.players.get(name),
        scheduler=SimpleNamespace(
            run_task=lambda owner, task, **kw: instance.tasks.append((task, kw)),
        ),
    )
    instance.database_connect = Mock(wraps=sqlite3.connect)
    namespace['sqlite3'] = SimpleNamespace(connect=instance.database_connect)
    return instance


def test_repeated_access_uses_one_database_connection_and_rechecks_operator_status(plugin):
    plugin._set_container_owner('overworld', 1, 64, 2, 'Owner')
    plugin.database_connect.reset_mock()
    player = SimpleNamespace(name='Visitor', is_op=False)
    for _ in range(100):
        assert plugin._is_container_authorized(player, 'Overworld', 1, 64, 2) == ('Owner', False)
    assert plugin.database_connect.call_count == 1
    player.is_op = True
    assert plugin._is_container_authorized(player, 'overworld', 1, 64, 2) == ('Owner', True)
    player.is_op = lambda: False
    assert plugin._is_container_authorized(player, 'overworld', 1, 64, 2) == ('Owner', False)
    assert plugin.database_connect.call_count == 1


def test_trust_owner_and_clear_changes_invalidate_cached_access_immediately(plugin):
    visitor = SimpleNamespace(name='Visitor', is_op=False)
    plugin._set_container_owner('overworld', 1, 64, 2, 'Owner')
    assert plugin._is_container_authorized(visitor, 'overworld', 1, 64, 2) == ('Owner', False)
    plugin._set_container_trust('overworld', 1, 64, 2, 'VISITOR')
    assert plugin._is_container_authorized(visitor, 'overworld', 1, 64, 2) == ('Owner', True)
    plugin._set_container_trust('overworld', 1, 64, 2, 'Visitor', trusted=False)
    assert plugin._is_container_authorized(visitor, 'overworld', 1, 64, 2) == ('Owner', False)
    plugin._set_container_owner('overworld', 1, 64, 2, 'Visitor')
    assert plugin._is_container_authorized(visitor, 'overworld', 1, 64, 2) == ('Visitor', True)
    plugin._clear_container_owner('overworld', 1, 64, 2)
    assert plugin._is_container_authorized(visitor, 'overworld', 1, 64, 2) == (None, True)


def test_unowned_legacy_lookup_is_cached_then_expires(plugin):
    plugin._infer_container_owner_from_logs = Mock(return_value=None)
    for _ in range(100):
        assert plugin._get_container_owner('overworld', 1, 64, 2) is None
    plugin._infer_container_owner_from_logs.assert_called_once()
    plugin.clock.value += 31
    assert plugin._get_container_owner('overworld', 1, 64, 2) is None
    assert plugin._infer_container_owner_from_logs.call_count == 2


def test_access_cache_is_bounded_and_keeps_recent_containers(plugin):
    for x in range(3):
        plugin._set_container_owner('overworld', x, 64, 2, 'Owner')
        assert plugin._get_container_owner('overworld', x, 64, 2) == 'Owner'
    assert plugin._get_container_owner('overworld', 0, 64, 2) == 'Owner'
    plugin._set_container_owner('overworld', 3, 64, 2, 'Owner')
    plugin._get_container_owner('overworld', 3, 64, 2)
    assert len(plugin._container_access_cache) == 3
    assert ('overworld', 0, 64, 2) in plugin._container_access_cache
    assert ('overworld', 1, 64, 2) not in plugin._container_access_cache


def test_historical_owner_lookup_uses_coordinate_index(plugin):
    with sqlite3.connect(plugin.namespace['DB_FILE']) as db:
        db.execute(
            'INSERT INTO interactions(name,world,type,x,y,z,action,time) VALUES (?,?,?,?,?,?,?,?)',
            ('Builder', 'Overworld', 'minecraft:chest', 1, 64, 2, 'Place', '2026-10-28'),
        )
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT name,world,type FROM interactions "
            "WHERE x=? AND y=? AND z=? AND action LIKE '%Place%' ORDER BY time ASC LIMIT 100",
            (1, 64, 2),
        ).fetchall()
    assert any('SEARCH' in row[3] and 'idx_interactions_coordinates_time' in row[3] for row in plan)
    assert plugin._get_container_owner('overworld', 1, 64, 2) == 'Builder'


def test_player_inventory_batch_is_spread_across_ticks_and_resolves_fresh_players(plugin):
    plugin._start_player_inventory_snapshot_sweeper()
    plugin._start_player_inventory_snapshot_sweeper()
    assert len(plugin.tasks) == 1
    sweep, options = plugin.tasks.pop()
    assert options['period'] == 100
    sweep()
    assert [options['delay'] for _, options in plugin.tasks] == [1, 34, 67]
    plugin._capture_player_inventory.assert_not_called()
    fresh_alice = SimpleNamespace(name='Alice', updated=True)
    plugin.players['Alice'] = fresh_alice
    del plugin.players['Bob']
    for capture, _ in plugin.tasks:
        capture()
    assert [call.args[0] for call in plugin._capture_player_inventory.call_args_list] == [
        fresh_alice, plugin.players['Chris'],
    ]


def test_scheduled_inventory_work_stops_after_shutdown(plugin):
    plugin._start_player_inventory_snapshot_sweeper()
    sweep, _ = plugin.tasks.pop()
    sweep()
    plugin._shutting_down = True
    for capture, _ in plugin.tasks:
        capture()
    plugin.tasks.clear()
    sweep()
    assert not plugin.tasks
    plugin._capture_player_inventory.assert_not_called()


def test_inventory_scan_rotates_small_batches_over_all_players(plugin):
    plugin.namespace['PLAYER_INVENTORY_CAPTURE_BATCH_SIZE'] = 2
    plugin._start_player_inventory_snapshot_sweeper()
    sweep, _ = plugin.tasks.pop()
    for _ in range(3):
        sweep()
        tasks, plugin.tasks = plugin.tasks, []
        for capture, _ in tasks:
            capture()
    assert [call.args[0].name for call in plugin._capture_player_inventory.call_args_list] == [
        'Alice', 'Bob', 'Chris', 'Alice', 'Bob', 'Chris',
    ]


def test_unchanged_player_inventory_skips_nested_summary_and_json(plugin):
    plugin._last_player_inventory_revisions['name:alice'] = '42'
    plugin.blockdata.player_inventory_summary = Mock(side_effect=AssertionError('Unchanged snapshot'))
    assert plugin._queue_player_inventory_snapshot({'player_name': 'Alice', 'revision': 42}) is None
    assert not plugin.namespace['data_buffers']['player_inventory_snapshot']


def test_diff_copies_only_changes_and_detaches_recovery_payloads(monkeypatch):
    path = SOURCE.with_name('blockdata_adapter.py')
    spec = spec_from_file_location('performance_adapter_test', path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    before = snapshot(1, [{'slot': slot, 'item': {
        'Name': 'minecraft:bundle', 'Count': 1,
        'tag': {'storage_item_component_content': [{'Name': 'minecraft:diamond', 'Count': 4}]},
    }} for slot in range(27)])
    after = deepcopy(before)
    copier = Mock(wraps=deepcopy)
    monkeypatch.setattr(module, 'deepcopy', copier)
    adapter = module.BlockDataAdapter
    assert adapter.diff_inventory(before, after) == []
    copier.assert_not_called()
    after['block_entity']['inventory'][0]['item']['Count'] = 2
    changes = adapter.diff_inventory(before, after)
    assert len(changes) == 1
    assert copier.call_count == 2
    changes[0]['before']['tag'].clear()
    assert before['block_entity']['inventory'][0]['item']['tag']
    detached = adapter.inventory_map(after)
    detached[0]['tag'].clear()
    assert after['block_entity']['inventory'][0]['item']['tag']
