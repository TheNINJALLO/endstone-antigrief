from pathlib import Path


SOURCE = (
    Path(__file__).parents[1]
    / "src/endstone_antigrief/antigrief_plugin.py"
).read_text(encoding="utf-8")


def test_rollback_uses_pre_change_target_and_stable_order():
    assert "ORDER BY time ASC, id ASC" in SOURCE
    assert "earliest.setdefault(key, row)" in SOURCE
    assert "air_targets" in SOURCE
    assert "solid_targets" in SOURCE
    assert "container_targets" in SOURCE
    assert "[*air_targets, *solid_targets, *container_targets]" in SOURCE


def test_failed_blocks_are_verified_and_retried():
    assert "_live_block_matches" in SOURCE
    assert "_schedule_rollback_block_retry" in SOURCE
    assert "max_attempts=10" in SOURCE
    assert "Permanently failed to restore" in SOURCE


def test_container_inventory_waits_for_full_placement_pass():
    placement_loop = SOURCE.index("for target in [*air_targets, *solid_targets, *container_targets]")
    post_restore_loop = SOURCE.index("for target in successful_containers")
    queue_call = SOURCE.index("self._queue_post_block_restore(target)", post_restore_loop)
    assert placement_loop < post_restore_loop < queue_call


def test_unverified_container_inventory_is_retried():
    assert "restored = self._restore_native_snapshot" in SOURCE
    assert "Container restore attempt" in SOURCE
    assert "Container restore did not verify" in SOURCE
