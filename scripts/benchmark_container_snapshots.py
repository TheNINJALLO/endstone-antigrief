"""Compare Python snapshot work with a previous release; excludes native capture."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
from statistics import median
import subprocess
import timeit
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = 'src/endstone_antigrief/blockdata_adapter.py'


def load_adapter(source, name):
    module = ModuleType(name)
    exec(compile(source, ADAPTER_PATH, 'exec'), module.__dict__)
    return module.BlockDataAdapter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', default='v1.5.20')
    parser.add_argument('--iterations', type=int, default=100)
    parser.add_argument('--samples', type=int, default=3)
    args = parser.parse_args()
    if args.iterations < 1 or args.samples < 1:
        parser.error('iterations and samples must be positive')
    baseline = load_adapter(subprocess.check_output(
        ['git', 'show', f'{args.baseline}:{ADAPTER_PATH}'], cwd=ROOT, text=True,
        encoding='utf-8',
    ), 'baseline_adapter')
    updated = load_adapter((ROOT / ADAPTER_PATH).read_text(encoding='utf-8'), 'updated_adapter')
    item = {'Name': 'minecraft:bundle', 'Count': 1, 'tag': {
        'storage_item_component_content': [
            {'Name': 'minecraft:diamond_sword', 'Count': 1, 'tag': {
                'display': {'Name': 'Saved sword', 'Lore': ['Evidence'] * 8},
                'ench': [{'id': index, 'lvl': 5} for index in range(8)],
            }} for _ in range(8)
        ],
    }}
    before = {'type': 'minecraft:chest', 'revision': 1, 'states': {}, 'block_entity': {
        'is_container': True, 'container_size': 54,
        'inventory': [{'slot': slot, 'item': deepcopy(item)} for slot in range(54)],
    }}
    after = deepcopy(before)
    changed = deepcopy(before)
    changed['block_entity']['inventory'][20]['item']['Count'] = 2
    results = {}
    for label, operation in (
        ('unchanged_diff', lambda cls: cls.diff_inventory(before, after)),
        ('one_slot_diff', lambda cls: cls.diff_inventory(before, changed)),
        ('snapshot_summary', lambda cls: cls.snapshot_summary(before)),
    ):
        values = [median(timeit.repeat(
            lambda: operation(cls), number=args.iterations, repeat=args.samples,
        )) * 1000 / args.iterations for cls in (baseline, updated)]
        results[label] = {
            'previous_ms_per_call': round(values[0], 4),
            'updated_ms_per_call': round(values[1], 4),
            'speedup': round(values[0] / values[1], 2),
        }
    print(json.dumps({
        'baseline': args.baseline,
        'fixture': '54 occupied slots, each containing a bundle of 8 rich-NBT swords',
        'iterations_per_sample': args.iterations, 'samples': args.samples,
        'results': results,
    }, indent=2))


if __name__ == '__main__':
    main()
