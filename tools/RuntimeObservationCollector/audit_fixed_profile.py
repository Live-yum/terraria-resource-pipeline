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


def audit(path: Path, xna_core: Path | None = None) -> dict:
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
    xna_verified = False
    if xna_core is not None:
        if not xna_core.is_file() or not 0 < xna_core.stat().st_size <= 16 * 1024 * 1024:
            raise ValueError('XNA_INPUT_BYTE_LIMIT')
        xna_raw = xna_core.read_bytes()
        expected_hash = next(d['sha256'] for d in profile['dependencies'] if d['name'] == 'Microsoft.Xna.Framework')
        if sha256(xna_raw) != expected_hash: raise ValueError('XNA_CORE_HASH_MISMATCH')
        xp = _Program(xna_raw, _Budget(ItemTextureAliasLimits(), None))
        for expected in profile['xnaVectorFields'] + [profile['xnaColorField']]:
            rid = expected['token'] & 0xffffff
            owners = [t['fullName'] for t in xp.types.values() if t['firstField'] <= rid < t['lastField']]
            row, _ = xp.row(4, rid)
            if (owners, xp.meta.string(row[1]), xp.meta.blob(row[2])[0].hex()) != (
                [expected['owner']], expected['name'], expected['signatureHex']):
                raise ValueError('XNA_FIELD_PIN_MISMATCH')
        xna_verified = True
    return dict(status='PARTIAL', metadataPinsVerified=xna_verified, gameMetadataPinsVerified=True,
                xnaFieldPinsVerified=xna_verified, methods=len(profile['methods']),
                fields=len(profile['fields']), inputSha256=profile['sourceSha256'],
                executedInput=False, compile='NOT_RUN', initializationVerified=False,
                complete=False, publishable=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', type=Path, required=True)
    parser.add_argument('--xna-core', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.client, args.xna_core), indent=2))
