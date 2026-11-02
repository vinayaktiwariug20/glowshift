"""Write tests/golden/baseline.json from the unmodified prototypes.

Refuses to replace an existing baseline: run again only by deliberately deleting
the file, because a re-recorded baseline hides whatever changed.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import baseline_builder  # noqa: E402

TARGET = Path(__file__).parent / 'golden' / 'baseline.json'


def main():
    if TARGET.exists():
        raise SystemExit(f'{TARGET.name} already exists; not overwriting it')
    data = {'recorded_with': baseline_builder.environment(), **baseline_builder.build()}
    TARGET.parent.mkdir(exist_ok=True)
    TARGET.write_text(json.dumps(data, indent=0, sort_keys=False) + '\n', encoding='utf-8', newline='\n')
    print('recorded', sum(len(v) for v in data.values()), 'entries with', data['recorded_with'])


if __name__ == '__main__':
    main()
