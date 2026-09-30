"""Duplicate an independently verified Windows baseline without rebuilding it.

The receipt comes from an actual Windows preview. This command neither creates
that evidence nor applies expression sidecars or renders a finished video.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def duplicate(baseline, receipt_path, destination):
    baseline, destination = Path(baseline), Path(destination)
    receipt = json.loads(Path(receipt_path).read_text(encoding='utf-8'))
    if baseline.suffix != '.ymmp' or destination.suffix != '.ymmp':
        raise ValueError('YMM4 .ymmp baseline and destination required')
    if baseline.resolve() == destination.resolve():
        raise ValueError('baseline must remain unchanged')
    data = baseline.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if not data or receipt.get('baseline_sha256') != sha:
        raise ValueError('baseline is missing or changed since Windows verification')
    for key in ('windows_preview_verified', 'mouth_sync_verified',
                'semantic_expressions_verified', 'caption_phrase_color_verified'):
        if receipt.get(key) is not True:
            raise ValueError('Windows verification missing: '+key)
    if not receipt.get('reviewer') or not receipt.get('verified_at'):
        raise ValueError('Windows preview reviewer and date required')
    assets = receipt.get('assets')
    if not isinstance(assets, list) or not assets:
        raise ValueError('baseline asset bindings required')
    for asset in assets:
        path = Path(asset['path'])
        if not path.is_absolute(): path = baseline.parent / path
        if digest(path) != asset['sha256']:
            raise ValueError('baseline asset is missing or changed: '+str(path))
    # Keep relative asset references valid. Other directories need an editor
    # adapter that rebases paths and verifies the resulting Windows project.
    if baseline.parent.resolve() != destination.parent.resolve():
        raise ValueError('duplicate beside baseline to preserve asset references')
    if destination.exists():
        if destination.read_bytes() != data:
            raise FileExistsError('existing working project preserved')
        return {'status':'REUSED', 'baseline_sha256':sha, 'project':str(destination)}
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.baseline-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as stream: stream.write(data)
        # Exclusive creation preserves an existing project even across a race.
        os.link(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {'status':'COPIED', 'baseline_sha256':sha, 'project':str(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(duplicate(args.baseline, args.receipt, args.destination)))

if __name__ == '__main__': main()
