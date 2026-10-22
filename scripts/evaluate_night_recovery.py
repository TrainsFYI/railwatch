"""Hash-pinned offline signal-candidate evaluation. Never uploads or changes capture settings."""
import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from railwatcher.config import Settings
from railwatcher.flashes import analyse_flashes
from railwatcher.night_recovery import NightRecovery


def evaluate(manifest, root, state_directory):
    settings = Settings(landmarks=manifest['landmarks'], night_review_timezone=manifest['timezone'])
    planner = NightRecovery(None, None, SimpleNamespace(directory=state_directory), None, None, None)
    planner.state = {'cursor': 0, 'episodes': []}
    signature = planner.signature(settings)
    results = []
    for fixture in sorted(manifest['fixtures'], key=lambda row: row['started_at']):
        path = (root/fixture['path']).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('Fixture must be under the private data root')
        if hashlib.sha256(path.read_bytes()).hexdigest() != fixture['sha256']:
            raise ValueError('Fixture media changed: '+fixture['path'])
        result = analyse_flashes(path, settings.landmarks)
        found = bool(result['windows'])
        results.append({'path': fixture['path'], 'sha256': fixture['sha256'],
                        'expected_candidate': fixture['expected_candidate'], 'candidate': found,
                        'passed': found == fixture['expected_candidate'], 'windows': result['windows']})
        planner.add_windows({'started': fixture['started_at'], 'ended': fixture['started_at']+fixture['duration']},
                            result['windows'], signature)
    episodes = [{key: row[key] for key in ['first_signal', 'last_signal', 'landmark_ids']}
                for row in planner.state['episodes']]
    return {'method': 'fixed_landmark_red_flash_v1', 'scope': manifest['scope'],
            'passed': all(row['passed'] for row in results) and len(episodes)==manifest['expected_episodes'],
            'false_candidates': sum(row['candidate'] and not row['expected_candidate'] for row in results),
            'misses': sum(not row['candidate'] and row['expected_candidate'] for row in results),
            'expected_episodes': manifest['expected_episodes'], 'episodes': episodes, 'fixtures': results,
            'limitation': 'Camera-specific crossing-signal candidate recall, not train classification or public-map validation.'}


if __name__ == '__main__':
    import tempfile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        report = evaluate(json.loads(args.manifest.read_text()), args.root, Path(directory))
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'passed': report['passed'], 'fixtures': len(report['fixtures']),
                      'episodes': len(report['episodes']), 'misses': report['misses'],
                      'false_candidates': report['false_candidates']}))
    raise SystemExit(0 if report['passed'] else 1)
