"""Data-only check of fixed C# descriptors against the exact client PE.

Never imports, loads or invokes CLR/game code. The report is not runtime evidence.
Use from the repository root with PYTHONPATH=src.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from resource_pipeline.item_texture_aliases import _Program, _Budget, ItemTextureAliasLimits
from resource_pipeline.security import sha256

ROOT = Path(__file__).resolve().parent


def audit(path: Path) -> dict:
    profile = json.loads((ROOT / 'fixed-profile.json').read_text())
    if not path.is_file() or not 0 < path.stat().st_size <= 64 * 1024 * 1024:
        raise ValueError('INPUT_BYTE_LIMIT')
    raw = path.read_bytes()
    if sha256(raw) != profile['sourceSha256']:
        raise ValueError('PINNED_CLIENT_HASH_MISMATCH')
    p = _Program(raw, _Budget(ItemTextureAliasLimits(total_method_bytes=8*1024*1024,
        instructions=2_000_000, steps=8_000_000, wall_seconds=120), None))
    for expected in profile['methods']:
        rid = expected['token'] & 0xffffff
        owners = [t['fullName'] for t in p.types.values() if t['firstMethod'] <= rid < t['lastMethod']]
        row, _ = p.row(6, rid)
        body = p.body(expected['token'], allow_eh=True)
        if (owners, p.meta.string(row[3]), sha256(p.meta.blob(row[4])[0]), body['evidence']['ilSha256']) != (
            [expected['owner']], expected['name'], expected['signatureSha256'], expected['ilSha256']):
            raise ValueError('METHOD_PIN_MISMATCH')
    for expected in profile['fields']:
        rid = expected['token'] & 0xffffff
        owners = [t['fullName'] for t in p.types.values() if t['firstField'] <= rid < t['lastField']]
        row, _ = p.row(4, rid)
        if (owners, p.meta.string(row[1]), p.meta.blob(row[2])[0].hex()) != (
            [expected['owner']], expected['name'], expected['signatureHex']):
            raise ValueError('FIELD_PIN_MISMATCH')
    return dict(status='PARTIAL', metadataPinsVerified=True, methods=len(profile['methods']),
                fields=len(profile['fields']), inputSha256=profile['sourceSha256'],
                executedInput=False, compile='NOT_RUN', initializationVerified=False,
                complete=False, publishable=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.client), indent=2))
