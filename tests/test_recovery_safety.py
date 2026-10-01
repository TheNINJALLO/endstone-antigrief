"""Exercise recovery against SQLite and mutable inventories without loading BDS."""

import ast
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from test_blockdata_adapter import load_adapter, snapshot


SOURCE = Path(__file__).parents[1] / 'src/endstone_antigrief/antigrief_plugin.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
PLUGIN = next(node for node in TREE.body if isinstance(node, ast.ClassDef)
              and node.name == 'AntiGriefPlugin')


class Inventory:
    size = 3

    def __init__(self, amount=0):
        self.items = {0: SimpleNamespace(type='minecraft:diamond', amount=amount)} if amount else {}

    def get_item(self, slot):
        return deepcopy(self.items.get(slot))

    def set_item(self, slot, item):
        self.items[slot] = deepcopy(item)

    def clear(self, slot):
        self.items.pop(slot, None)

    def count(self):
        return sum(item.amount for item in self.items.values())


def make_plugin(db_path):
    methods = {
        '_canonical_item_signature', '_same_canonical_item', '_normalise_item_identifier',
        '_normalise_world_key', '_ensure_recovery_destination', '_remove_canonical_item_from_player',
        '_queue_confiscation', '_apply_pending_confiscations', '_rollback_cancelled',
        '_cancel_rollbacks', '_refresh_grief_report_recovery', '_schedule_native_restore',
        '_schedule_rollback_block_retry', '_schedule_grief_report_finalize', 'on_command',
        '_execute_rollback', '_normalize_rollback_block_type',
    }
    nodes = [node for node in PLUGIN.body if isinstance(node, ast.FunctionDef)
             and node.name in methods]
    harness = ast.ClassDef(name='Harness', bases=[], keywords=[], body=nodes, decorator_list=[])
    init = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
                and node.name == 'init_database')
    adapter = load_adapter()
    clock = SimpleNamespace(value=0)
    namespace = {
        'sqlite3': sqlite3, 'DB_FILE': str(db_path), 'json': json, 'deepcopy': deepcopy,
        'uuid4': uuid4, 're': re, 'ROLLBACK_RECOVERY_ENABLED': True,
        'now_est': lambda: datetime.now(timezone.utc), 'timedelta': timedelta,
        'tm': SimpleNamespace(monotonic=lambda: clock.value), 'data_buffers': defaultdict(list),
        'BlockDataAdapter': adapter, 'ColorFormat': SimpleNamespace(RED='', GREEN='', YELLOW='', AQUA=''),
        'CommandSender': object, 'Command': object, 'defaultdict': defaultdict,
        'flush_data_to_db': lambda: None, 'lang': {'rollback_start': 'Starting'},
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[init, harness], type_ignores=[])),
                 str(SOURCE), 'exec'), namespace)
    connection, _ = namespace['init_database']()
    connection.close()
    plugin = namespace['Harness']()
    plugin._active_rollbacks = set()
    plugin._cancelled_rollbacks = set()
    plugin._recovery_warning_times = {}
    plugin._shutting_down = False
    plugin.blockdata = adapter()
    plugin.blockdata.apply = Mock(side_effect=AssertionError('Recovery must not write containers'))
    plugin.logger = Mock()
    plugin.clock = clock
    plugin.tasks = []
    plugin.player = SimpleNamespace(name='Player', inventory=Inventory(), send_message=Mock())
    plugin.server = SimpleNamespace(
        get_player=lambda name: plugin.player if name.casefold() == 'player' else None,
        scheduler=SimpleNamespace(run_task=lambda owner, task, **kwargs: plugin.tasks.append(task)),
    )
    plugin.current = snapshot(1, [])
    plugin._capture_native_snapshot = Mock(side_effect=lambda *a, **kw: (deepcopy(plugin.current), None))
    plugin._ensure_blockdata_ready = lambda: True
    plugin._stack_matches_canonical_item = lambda stack, item: bool(
        stack and stack.type == adapter.item_id(item)
    )
    return plugin


@pytest.fixture
def plugin(tmp_path):
    return make_plugin(tmp_path / 'agdata.db')


def item(count=10, name='minecraft:diamond'):
    return {'Name': name, 'Count': count}


def queue(plugin, rollback_id='batch-one', player='Player', count=10, slot=0):
    return plugin._queue_confiscation(
        player, 'Owner', 'overworld', 1, 64, 2, item(count), count,
        'rollback_recovery:Container Take', destination_slot=slot, rollback_id=rollback_id,
    )


def rows(tmp_path):
    with sqlite3.connect(tmp_path / 'agdata.db') as db:
        return db.execute(
            'SELECT rollback_id,status,removed_amount,returned_amount FROM pending_confiscations ORDER BY id'
        ).fetchall()


@pytest.mark.parametrize('entries,slot,expected_count', [
    ([], 0, 10),  # Empty slot was previously refilled on every sweep.
    ([{'slot': 0, 'item': item(2)}], 0, 10),
    ([{'slot': 1, 'item': item()}], 0, 10),  # Another slot cannot authorize confiscation.
    ([{'slot': 0, 'item': item(name='minecraft:emerald')}], 0, 10),
    ([{'slot': 0, 'item': item(64)}], 0, 128),  # Historical overstack must not be written.
    ([], None, 10),
    ([], 40, 10),
])
def test_unverified_destination_never_writes_or_confiscates(plugin, entries, slot, expected_count):
    plugin.current = snapshot(1, entries)
    before = deepcopy(plugin.current)
    plugin.player.inventory = Inventory(64)
    queue(plugin, count=expected_count, slot=slot)
    for _ in range(5):
        assert plugin._apply_pending_confiscations('Player') == 0
    assert plugin.current == before
    assert plugin.player.inventory.count() == 64
    plugin.blockdata.apply.assert_not_called()


def test_owner_collecting_restored_items_does_not_cause_refill(plugin):
    queue(plugin)
    plugin.current = snapshot(1, [{'slot': 0, 'item': item()}])
    assert plugin._apply_pending_confiscations('Player') == 0
    plugin.current = snapshot(2, [])  # Owner collects the restored items.
    for _ in range(5):
        assert plugin._apply_pending_confiscations('Player') == 0
    assert plugin.blockdata.inventory_map(plugin.current) == {}
    plugin.blockdata.apply.assert_not_called()


def test_partial_recovery_is_accounted_once_without_rewriting_container(plugin, tmp_path):
    queue(plugin)
    plugin.current = snapshot(1, [{'slot': 0, 'item': item()}])
    before = deepcopy(plugin.current)
    plugin.player.inventory = Inventory(4)
    assert plugin._apply_pending_confiscations('Player') == 4
    assert rows(tmp_path) == [('batch-one', 'pending', 4, 4)]
    plugin.player.inventory = Inventory(20)
    assert plugin._apply_pending_confiscations('Player') == 6
    assert plugin._apply_pending_confiscations('Player') == 0
    assert plugin.player.inventory.count() == 14
    assert rows(tmp_path) == [('batch-one', 'complete', 10, 10)]
    assert plugin.current == before
    plugin.blockdata.apply.assert_not_called()


def test_missing_container_and_warning_backoff(plugin):
    plugin.current = None
    for _ in range(20):
        queue(plugin)
    for seconds in [0, 10, 20, 30, 40, 50]:
        plugin.clock.value = seconds
        assert plugin._apply_pending_confiscations('Player') == 0
    assert plugin.logger.warning.call_count == 1
    assert '/agstop batch-on' in plugin.logger.warning.call_args.args[0]
    plugin.clock.value = 60
    plugin._apply_pending_confiscations('Player')
    assert plugin.logger.warning.call_count == 2


def test_cancel_all_is_durable_for_online_offline_and_partial_recovery(plugin, tmp_path):
    queue(plugin)
    queue(plugin, rollback_id='offline-batch', player='Offline')
    plugin.current = snapshot(1, [{'slot': 0, 'item': item()}])
    plugin.player.inventory = Inventory(4)
    plugin._apply_pending_confiscations('Player')
    assert plugin._cancel_rollbacks() == (2, 2)
    assert rows(tmp_path) == [('batch-one', 'cancelled', 4, 4), ('offline-batch', 'cancelled', 0, 0)]
    restarted = make_plugin(tmp_path / 'agdata.db')
    restarted.player.inventory = Inventory(30)
    assert restarted._apply_pending_confiscations('Player') == 0
    restarted._capture_native_snapshot.assert_not_called()
    assert restarted.player.inventory.count() == 30


def test_targeted_cancel_leaves_other_batches_and_completed_rows(plugin, tmp_path):
    queue(plugin, rollback_id='abcd-first')
    queue(plugin, rollback_id='efgh-second')
    queue(plugin, rollback_id='done')
    with sqlite3.connect(tmp_path / 'agdata.db') as db:
        db.execute("UPDATE pending_confiscations SET status='complete',removed_amount=10,returned_amount=10 WHERE rollback_id='done'")
    assert plugin._cancel_rollbacks('abcd') == (1, 1)
    assert rows(tmp_path) == [
        ('abcd-first', 'cancelled', 0, 0), ('efgh-second', 'pending', 0, 0), ('done', 'complete', 10, 10),
    ]
    assert queue(plugin, rollback_id='abcd-first') is None
    assert plugin._cancel_rollbacks('unknown') == (0, 0)


def test_ambiguous_id_cancels_nothing(plugin, tmp_path):
    queue(plugin, rollback_id='abcd-first')
    queue(plugin, rollback_id='abcd-second')
    with pytest.raises(ValueError, match='ambiguous'):
        plugin._cancel_rollbacks('abcd')
    assert all(row[1] == 'pending' for row in rows(tmp_path))


@pytest.mark.parametrize('payload', [
    json.dumps({'status': 'processing', 'evidence_hash': 'original', 'rollback': {}}),
    '{invalid json',
])
def test_cancel_preserves_evidence_and_report_status_even_after_restart(plugin, tmp_path, payload):
    queue(plugin)
    with sqlite3.connect(tmp_path / 'agdata.db') as db:
        db.execute(
            """INSERT INTO grief_reports
               (report_id,rollback_id,created_at,admin_name,center_x,center_y,center_z,
                radius,hours,evidence_hash,players_json,worlds_json,summary_json,report_json)
               VALUES ('report','batch-one','now','Operator',1,64,2,5,1,'original','[]','[]','{}',?)""",
            (payload,),
        )
    assert plugin._cancel_rollbacks(actor_name='Admin') == (1, 1)
    restarted = make_plugin(tmp_path / 'agdata.db')
    restarted._refresh_grief_report_recovery('batch-one')
    with sqlite3.connect(tmp_path / 'agdata.db') as db:
        status, evidence_hash, serialized = db.execute(
            "SELECT status,evidence_hash,report_json FROM grief_reports WHERE report_id='report'"
        ).fetchone()
    assert status == 'cancelled'
    assert evidence_hash == 'original'
    if payload.startswith('{invalid'):
        assert serialized == payload
    else:
        report = json.loads(serialized)
        assert report['status'] == 'cancelled'
        assert report['rollback']['cancellation']['cancelled_by'] == 'Admin'
        assert report['rollback']['recovery']['pending_rows'] == 0


def test_cancelled_delayed_block_inventory_and_report_tasks_do_nothing(plugin):
    plugin._active_rollbacks.add('batch-one')
    target = {'rollback_id': 'batch-one', 'x': 1, 'y': 64, 'z': 2}
    plugin._attempt_rollback_block = Mock(return_value=True)
    plugin._queue_post_block_restore = Mock()
    plugin._restore_native_snapshot = Mock(return_value=True)
    plugin._finalize_grief_report = Mock()
    plugin._schedule_rollback_block_retry(target)
    plugin._schedule_native_restore({}, 'overworld', 1, 64, 2, rollback_id='batch-one')
    plugin._schedule_grief_report_finalize('report', 'batch-one', [])
    assert plugin._cancel_rollbacks() == (1, 0)
    for task in plugin.tasks:
        task()
    plugin._attempt_rollback_block.assert_not_called()
    plugin._restore_native_snapshot.assert_not_called()
    plugin._capture_native_snapshot.assert_not_called()
    plugin._finalize_grief_report.assert_not_called()


def test_uncertain_inventory_write_is_not_replayed(plugin):
    plugin._restore_native_snapshot = Mock(return_value=False)
    plugin._schedule_native_restore(plugin.current, 'overworld', 1, 64, 2, rollback_id='batch-one')
    plugin.tasks.pop(0)()
    plugin._restore_native_snapshot.assert_called_once()
    assert plugin.tasks == []


def test_actor_readiness_can_retry_without_replaying_inventory(plugin):
    plugin._restore_native_snapshot = Mock(return_value=True)
    saved = plugin.current
    plugin.current = None
    plugin._schedule_native_restore(saved, 'overworld', 1, 64, 2, rollback_id='batch-one')
    plugin.tasks.pop(0)()
    plugin._restore_native_snapshot.assert_not_called()
    plugin.current = saved
    plugin.tasks.pop(0)()
    plugin._restore_native_snapshot.assert_called_once()
    assert plugin.tasks == []


def test_stop_command_works_for_console_and_defaults_to_all(plugin, tmp_path):
    queue(plugin)
    sender = SimpleNamespace(name='Console', send_message=Mock())
    assert plugin.on_command(sender, SimpleNamespace(name='agstop'), []) is True
    assert rows(tmp_path)[0][1] == 'cancelled'
    assert 'Cancelled 1 rollback(s)' in sender.send_message.call_args.args[0]
    commands = next(node.value for node in PLUGIN.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'commands' for target in node.targets))
    entry = next(value for key, value in zip(commands.keys, commands.values)
                 if ast.literal_eval(key) == 'agstop')
    assert ast.literal_eval(entry)['permissions'] == ['antigrief.command.op']


def test_recovery_uses_restored_target_count_and_batch_callback_obeys_stop(plugin, tmp_path):
    saved = snapshot(1, [{'slot': 0, 'item': item()}])
    plugin._load_container_snapshot = lambda key: saved
    plugin._attempt_rollback_block = Mock(return_value=True)
    plugin._queue_post_block_restore = Mock(return_value=True)
    plugin._build_grief_report = Mock(return_value={})
    plugin._store_grief_report = Mock(return_value='report')
    plugin._update_grief_report_execution = Mock()
    plugin._schedule_grief_report_finalize = Mock()
    with sqlite3.connect(tmp_path / 'agdata.db') as db:
        for count, amount in [(10, 5), (64, 4)]:
            db.execute(
                'INSERT INTO interactions (name,action,x,y,z,type,world,time,blockdata) VALUES (?,?,?,?,?,?,?,?,?)',
                ('Player', 'Container Take', 1, 64, 2, 'minecraft:chest', 'overworld',
                 datetime.now(timezone.utc).isoformat(), json.dumps({
                     'before_snapshot_id': 'saved', 'before_item': item(count), 'amount': amount, 'slot': 0,
                 })),
            )
    sender = SimpleNamespace(name='Operator', send_message=Mock())
    plugin._execute_rollback(sender, 1, 64, 2, 1, 5)
    with sqlite3.connect(tmp_path / 'agdata.db') as db:
        queued = db.execute('SELECT item_json,requested_amount FROM pending_confiscations ORDER BY id').fetchall()
    assert [(json.loads(payload)['Count'], count) for payload, count in queued] == [(10, 5), (10, 4)]
    assert plugin._cancel_rollbacks() == (1, 2)
    plugin._apply_pending_confiscations = Mock()
    for task in plugin.tasks:
        task()
    plugin._apply_pending_confiscations.assert_not_called()
