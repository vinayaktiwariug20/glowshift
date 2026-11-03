"""Write missing tests/golden/ files from the unmodified prototypes.

Never replaces an existing file: a re-recorded baseline hides whatever changed.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import baseline_builder  # noqa: E402

GOLDEN = Path(__file__).parent / 'golden'
TARGETS = {'baseline.json': baseline_builder.build, 'sensitivity.json': baseline_builder.build_sensitivity}


def main():
    GOLDEN.mkdir(exist_ok=True)
    for name, build in TARGETS.items():
        target = GOLDEN / name
        if target.exists():
            print(f'{name} already exists; not overwriting it')
            continue
        data = {'recorded_with': baseline_builder.environment(), **build()}
        target.write_text(json.dumps(data, indent=0, sort_keys=False) + '\n', encoding='utf-8', newline='\n')
        print('recorded', name, sum(len(v) for v in data.values()), 'entries with', data['recorded_with'])


if __name__ == '__main__':
    main()
