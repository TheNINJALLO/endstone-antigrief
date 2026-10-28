"""Exercise startup with real SQLite page limits instead of filling the disk."""

import ast
import builtins
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).parents[1] / 'src/endstone_antigrief/antigrief_plugin.py'
INDEX = 'idx_interactions_coordinates_time'


def load_startup(db_path):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    init = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == 'init_database')
    namespace = {'sqlite3': sqlite3, 'DB_FILE': str(db_path)}
    exec(compile(ast.Module(body=[init], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace


def has_index(connection):
    return connection.execute(
        'SELECT 1 FROM sqlite_master WHERE type=? AND name=?', ('index', INDEX),
    ).fetchone() is not None


def test_default_upgrade_preserves_history_without_building_new_index(tmp_path):
    namespace = load_startup(tmp_path / 'agdata.db')
    connection, _ = namespace['init_database']()
    connection.execute("INSERT INTO interactions(name, action) VALUES ('Player', 'Container Take')")
    connection.commit()
    connection.close()
    connection, _ = namespace['init_database']()
    try:
        assert not has_index(connection)
        assert connection.execute('SELECT name,action FROM interactions').fetchall() == [
            ('Player', 'Container Take'),
        ]
    finally:
        connection.close()


def test_optional_index_can_be_built_and_existing_index_is_retained(tmp_path):
    namespace = load_startup(tmp_path / 'agdata.db')
    connection, _ = namespace['init_database'](build_legacy_history_index=True)
    assert has_index(connection)
    connection.close()
    connection, _ = namespace['init_database']()
    try:
        assert has_index(connection)
    finally:
        connection.close()


@pytest.mark.parametrize('build_index', [False, True], ids=['default', 'optional-full'])
def test_full_optional_index_does_not_undo_migrations_or_prevent_startup(
    tmp_path, capsys, build_index,
):
    db_path = tmp_path / 'agdata.db'
    namespace = load_startup(db_path)
    connection, _ = namespace['init_database']()
    connection.executemany(
        'INSERT INTO interactions(name,action,x,y,z,type,world,time) VALUES (?,?,?,?,?,?,?,?)',
        [('Player', 'Place', index, 64, index, 'minecraft:chest', 'overworld',
          '2026-10-28T18:15:00-04:00') for index in range(2000)],
    )
    # This legacy row must be cancelled and committed before optional maintenance.
    connection.execute(
        "INSERT INTO pending_confiscations(theft_key,player_name,world,x,y,z,item_json,"
        "requested_amount,status,reason,created_at,updated_at) "
        "VALUES ('legacy','Player','overworld',1,64,2,'{}',1,'pending','old','now','now')"
    )
    connection.commit()
    connection.close()

    def limited_connect(*args, **kwargs):
        db = sqlite3.connect(*args, **kwargs)
        pages = db.execute('PRAGMA page_count').fetchone()[0]
        db.execute(f'PRAGMA max_page_count={pages}')
        return db

    namespace['sqlite3'] = SimpleNamespace(
        connect=limited_connect, OperationalError=sqlite3.OperationalError,
    )
    connection, _ = namespace['init_database'](build_legacy_history_index=build_index)
    try:
        assert not has_index(connection)
        assert not connection.in_transaction
        assert connection.execute('SELECT count(*) FROM interactions').fetchone()[0] == 2000
        assert connection.execute('SELECT status FROM pending_confiscations').fetchone()[0] == 'cancelled'
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='grief_reports'"
        ).fetchone() is not None
        warning = capsys.readouterr().out
        if build_index:
            assert 'database or disk is full' in warning
            assert 'startup will continue' in warning
        else:
            assert not warning
    finally:
        connection.close()

    # After restoring headroom, normal logging still works on the same database.
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO interactions(name, action) VALUES ('Player', 'Container Add')")
        assert db.execute('SELECT count(*) FROM interactions').fetchone()[0] == 2001


def test_required_schema_errors_are_not_hidden(tmp_path):
    namespace = load_startup(tmp_path / 'agdata.db')
    namespace['sqlite3'] = SimpleNamespace(
        connect=lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError('unable to open database file')),
        OperationalError=sqlite3.OperationalError,
    )
    with pytest.raises(sqlite3.OperationalError, match='unable to open database file'):
        namespace['init_database']()


def test_optional_setting_does_not_force_existing_config_rewrite(tmp_path):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    defaults = next(node for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'DEFAULT_CONFIG'
                            for target in node.targets))
    loader = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == 'load_config')
    old_config = ast.literal_eval(defaults.value)
    old_config.pop('build_legacy_history_index', None)
    path = tmp_path / 'config.json'
    original = json.dumps(old_config)
    path.write_text(original, encoding='utf-8')

    def read_only_open(path, mode='r', **kwargs):
        assert mode == 'r', 'Optional index setting must not force a config rewrite'
        return builtins.open(path, mode, **kwargs)

    namespace = {'os': os, 'json': json, 'CONFIG_FILE': str(path), 'open': read_only_open}
    exec(compile(ast.Module(body=[defaults, loader], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    config = namespace['load_config']()
    assert config.get('build_legacy_history_index', False) is False
    assert path.read_text(encoding='utf-8') == original
