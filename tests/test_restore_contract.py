from pathlib import Path


def test_plugin_uses_three_isolated_restore_phases():
    source = (
        Path(__file__).parents[1]
        / "src/endstone_antigrief/antigrief_plugin.py"
    ).read_text()
    assert "build_state_restore_patch" in source
    assert "build_metadata_restore_patch" in source
    assert "build_inventory_restore_patch" in source
    assert "Continuing with exact inventory restore" in source
    assert "Container verification mismatch" in source
    assert "Placed {block_type} without command states" in source


def test_inventory_phase_is_after_non_fatal_metadata_phase():
    source = (
        Path(__file__).parents[1]
        / "src/endstone_antigrief/antigrief_plugin.py"
    ).read_text()
    metadata_at = source.index("'metadata', self.blockdata.build_metadata_restore_patch")
    inventory_at = source.index("'inventory', self.blockdata.build_inventory_restore_patch")
    verification_at = source.index("Container verification mismatch")
    assert metadata_at < inventory_at < verification_at
