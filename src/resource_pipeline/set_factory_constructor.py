"""Closed fresh SetFactory constructor effects, from bounded PE/CLI bytes only.

This certificate ends at normal constructor return. It never certifies a later
pool state, a whole type initializer, or a runtime snapshot.
"""
from __future__ import annotations

import hashlib
import struct

from .item_texture_aliases import (
    ItemTextureAliasLimits, _Budget, _Program, _CheckpointCancelled,
    _require, _shape, _instance_field, _hex,
)
from .security import PipelineError
from .static_il import ILUnsupported, EvidenceSizeLimit, _compressed, json_evidence_size


_CACHES = {'_intBufferCache': 8, '_ushortBufferCache': 7,
           '_boolBufferCache': 2, '_floatBufferCache': 12}


def _queue_constructor(p, token, field, name, element):
    _require(token >> 24 == 10, 'FACTORY_CTOR_QUEUE_MEMBER')
    member, _ = p.row(10, token & 0xffffff)
    _require(member[0] & 7 == 4 and 0 < member[0] >> 3 <= min(p.meta.rows[27], 0xffffff)
             and p.meta.string(member[1]) == '.ctor'
             and p.meta.blob(member[2])[0] == b'\x20\x00\x01', 'FACTORY_CTOR_QUEUE_MEMBER')
    sig = p.type_signature(0x1b000000 | member[0] >> 3)
    _require(sig[:2] == b'\x15\x12', 'FACTORY_CTOR_QUEUE_TYPE')
    coded, pos = _compressed(sig, 2)
    _require(coded & 3 == 1 and 0 < coded >> 2 <= min(p.meta.rows[1], 0xffffff)
             and sig[pos:] == bytes((1, 0x1d, element)), 'FACTORY_CTOR_QUEUE_TYPE')
    row = p.external_type(0x01000000 | coded >> 2, 'System.Collections.Generic.Queue`1', core=False)
    assembly, _ = p.row(35, row[0] >> 2)
    _require(p.meta.string(assembly[6]) in ('System', 'mscorlib') and not assembly[4]
             and not p.meta.string(assembly[7])
             and p.meta.blob(assembly[5])[0] == bytes.fromhex('b77a5c561934e089'),
             'FACTORY_CTOR_QUEUE_ASSEMBLY')
    _instance_field(p, field, name, b'\x06' + sig)


def prove_set_factory_constructor(p):
    """Prove all direct effects of the supported fresh constructor body.

    Trusted core-library metadata identities are semantic intrinsics, not a
    cryptographic verification of the referenced assemblies.
    """
    rid, owner = p.owner('Terraria.ID.SetFactory')
    row, _ = p.row(2, rid)
    _require(not row[0] & (0x18 | 0x20 | 0x80) and row[3] & 3 == 1
             and 0 < row[3] >> 2 <= min(p.meta.rows[1], 0xffffff),
             'FACTORY_CTOR_OWNER')
    p.external_type(0x01000000 | row[3] >> 2, 'System.Object')
    method = p.method('Terraria.ID.SetFactory', '.ctor', b'\x20\x01\x01\x08')
    _require(not method['local'], 'FACTORY_CTOR_LOCALS')
    fields = {}
    for fid in range(owner['firstField'], owner['lastField']):
        p.budget.check()
        f, _ = p.row(4, fid)
        name = p.meta.string(f[1])
        _require(name not in fields and name in (*_CACHES, '_size', '_queueLock'),
                 'FACTORY_CTOR_UNSUPPORTED_FIELD')
        fields[name] = 0x04000000 | fid
    _require('_size' in fields, 'FACTORY_CTOR_SIZE_MISSING')
    # Even normal-return IL cannot prove distinct fields if managed references
    # overlap in explicit layout. Reject layout metadata rather than assume CLR
    # validation or native offsets not established by this byte-only proof.
    _require(not any(r[2] == rid for r in p.table(15))
             and not any((0x04000000 | r[1]) in fields.values() for r in p.table(16)),
             'FACTORY_CTOR_FIELD_LAYOUT')
    il = method['instructions']; at = 0; stores = set(); caches = []; lock = None
    while at + 2 < len(il) and il[at].opcode == 2 and il[at + 1].opcode == 0x73:
        p.budget.check()
        cap = _shape(il[at:at + 3], [2, (0x73, 'ctor'), (0x7d, 'field')])
        field = cap['field']
        names = [name for name, value in fields.items() if value == field]
        _require(len(names) == 1 and field not in stores, 'FACTORY_CTOR_STORE_BINDING')
        name = names[0]
        if name in _CACHES:
            _queue_constructor(p, cap['ctor'], field, name, _CACHES[name])
            caches.append({'fieldToken': _hex(field), 'fieldName': name,
                           'constructorToken': _hex(cap['ctor']), 'storeIlOffset': il[at + 2].offset,
                           'state': 'fresh distinct empty queue'})
        else:
            _require(name == '_queueLock', 'FACTORY_CTOR_UNSUPPORTED_ALLOCATION')
            p.member(cap['ctor'], 'System.Object', '.ctor', b'\x20\x00\x01')
            _instance_field(p, field, '_queueLock', b'\x06\x1c')
            lock = {'fieldToken': _hex(field), 'storeIlOffset': il[at + 2].offset,
                    'state': 'fresh distinct non-null object'}
        stores.add(field); at += 3
    base = _shape(il[at:at + 2], [2, (0x28, 'base')])
    p.member(base['base'], 'System.Object', '.ctor', b'\x20\x00\x01')
    at += 2
    guard = False
    if len(il) - at == 9:
        g = _shape(il[at:], [3, (0x2d, '@5'), (0x72, 'message'), (0x73, 'error'),
                             0x7a, 2, 3, (0x7d, fields['_size']), 0x2a])
        p.user_string(g['message'])
        p.member(g['error'], 'System.ArgumentOutOfRangeException', '.ctor', b'\x20\x01\x01\x0e')
        guard = True
    else:
        _shape(il[at:], [2, 3, (0x7d, fields['_size']), 0x2a])
    _instance_field(p, fields['_size'], '_size', b'\x06\x08')
    stores.add(fields['_size'])
    _require(stores == set(fields.values()), 'FACTORY_CTOR_UNINITIALIZED_FIELD')
    _require(not caches or lock is not None, 'FACTORY_CTOR_LOCK_MISSING')
    return {'status': 'PROVEN_FRESH_CONSTRUCTOR_NORMAL_RETURN', 'methodToken': _hex(method['token']),
            'sizeFieldToken': _hex(fields['_size']), 'sizeValue': 'argument 1 unchanged',
            'rejectsZeroSize': guard, 'negativeSizeRejected': False,
            'caches': caches, 'lock': lock, 'thisEscapes': False,
            'cacheStateAtLaterCallProven': False, 'wholeInitializerProven': False,
            'coreLibraryIdentityAssumed': True}


def extract_set_factory_constructor(data: bytes, *, limits=ItemTextureAliasLimits(), checkpoint=None):
    """Data-only public evidence API; unsupported bodies yield no certificate."""
    budget = _Budget(limits, checkpoint)
    result = {'schemaVersion': 1, 'status': 'UNRESOLVED', 'complete': False, 'publishable': False,
              'executedInput': False, 'runtimeSnapshotUsable': False,
              'scope': 'fresh SetFactory constructor normal-return state only', 'metadataEvidence': []}
    try:
        budget.check()
        _require(type(data) is bytes and len(data) <= limits.input_bytes, 'FACTORY_CTOR_INPUT_LIMIT')
        result['inputSha256'] = hashlib.sha256(data).hexdigest()
        p = _Program(data, budget)
        proof = prove_set_factory_constructor(p)
        result.update(status=proof['status'], proof=proof, metadataEvidence=p.evidence)
        json_evidence_size(result, limits.evidence_bytes, budget.check)
    except _CheckpointCancelled as exc:
        raise exc.original
    except (ILUnsupported, EvidenceSizeLimit) as exc:
        result.update(status=getattr(exc, 'code', 'FACTORY_CTOR_EVIDENCE_LIMIT'), metadataEvidence=[])
        result.pop('proof', None)
    except (PipelineError, ValueError, UnicodeError, struct.error, IndexError, KeyError, RecursionError, TypeError):
        result.update(status='FACTORY_CTOR_MALFORMED_INPUT', metadataEvidence=[])
        result.pop('proof', None)
    return result
