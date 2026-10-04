"""Finite prefix-pool initializer boundary facts from PE bytes, never CLR code.

The two supported source hashes are fixed server-owned profiles. Whole-method
coverage proves fresh ordered arrays only at the normal-return boundary of the
pool initializer, against the independent PrefixID.Count initializer domain.
Public mutable pool fields and their array elements can change later; no final
runtime snapshot, prefix eligibility/effects, or publication is certified.
"""
from __future__ import annotations

from pathlib import Path

from .item_assembler import POOLS
from .item_dispatch_sets import ItemDispatchSetLimits
from .item_texture_aliases import (
    _Program, _Budget, _CheckpointCancelled, _constant, _hex, _require, _shape,
)
from .security import sha256
from .server_semantics import SemanticLimits, read_assembly_bytes
from .set_factory_lifecycle import _int_array
from .static_il import json_evidence_size


_OWNER = 'Terraria.GameContent.Prefixes.PrefixLegacy+Prefixes'
_COUNT_OWNER = 'Terraria.ID.PrefixID'
_CCTOR_SIG = b'\x00\x00\x01'
_SIGNATURE_SHA256 = 'cf7605ed1bc735f6c825554154627467e1cac9df54cee8699218ed434603c568'
_PROFILES = {
    '960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3': {
        'role': 'client',
        'poolToken': 0x06004c9d,
        'poolIlSha256': 'e595213c9306a08fa39006a28500f169bef535410924b766c061426f9031f07f',
        'countToken': 0x060020b0,
        'countIlSha256': 'fca1c245e79c02e94a828ead7e1b154480faa6e925d144d5123377b0fa9be65b',
    },
    'd87e3faf08637f6be8882c63e7f11fb7e792b0230006309618473ece0f863e1e': {
        'role': 'server',
        'poolToken': 0x0600492b,
        'poolIlSha256': '291adba37864c860fb2de43a5c498670f11eb13a2253e542d10812fc3f20129e',
        'countToken': 0x06001fad,
        'countIlSha256': '74a520d0d9f3cf4a07b3d811c0d91ce88281969e159aacd312c9337045d1cfea',
    },
}


def _cctor(p, owner):
    # Do not signature-filter before uniqueness: malformed overloads are refused.
    method = p.method(owner, '.cctor')
    row, _ = p.row(6, method['token'] & 0xffffff)
    _require(method['signature'] == _CCTOR_SIG and row[2] & 0x1810 == 0x1810
             and not method['local'] and not method['eh'], 'POOL_INITIALIZER_SIGNATURE_OR_FLAGS')
    il = method['instructions']
    _require(il and il[-1].opcode == 0x2a and sum(i.opcode == 0x2a for i in il) == 1,
             'POOL_SINGLE_FINAL_RETURN')
    stack = 8 if method['header'] == 1 else p.meta.reader.uint(method['evidence']['bodyOffset'] + 2, 2)
    return method, stack


def _prove(p, pool_method=None, count_method=None):
    """Internal synthetic-test entry; not a configurable public source profile."""
    pool, pool_stack = _cctor(p, _OWNER)
    count_init, count_stack = _cctor(p, _COUNT_OWNER)
    if pool_method is not None:
        _require(pool_method is pool, 'POOL_METHOD_BINDING')
    if count_method is not None:
        _require(count_method is count_init, 'POOL_COUNT_METHOD_BINDING')

    count_token = p.field(_COUNT_OWNER, 'Count', b'\x06\x08')
    count_row, _ = p.row(4, count_token & 0xffffff)
    _require(count_row[0] == 0x36, 'POOL_COUNT_READONLY_FIELD_FLAGS')
    il = count_init['instructions']
    _require(len(il) == 3, 'POOL_WHOLE_COUNT_INITIALIZER')
    _shape(il[1:], [(0x80, count_token), 0x2a])
    count = _constant(il[0])
    _require(0 < count <= 256, 'POOL_PREFIX_DOMAIN_LIMIT')
    _require(count_stack >= 1, 'POOL_COUNT_MAXSTACK_TOO_SMALL')

    # Every pool field is present, typed and accounted for exactly once. The
    # consumer's names are an API contract, never a hard-coded table of IDs.
    _, owner = p.owner(_OWNER)
    _require(owner['lastField'] - owner['firstField'] == len(POOLS), 'POOL_EXACT_FIELD_SET')
    fields = {name: p.field(_OWNER, name, b'\x06\x1d\x08') for name in POOLS}
    for token in fields.values():
        _require(p.row(4, token & 0xffffff)[0][0] == 0x16, 'POOL_PUBLIC_STATIC_FIELD_FLAGS')
    by_token = {token: name for name, token in fields.items()}
    il = pool['instructions']
    at, required_stack = 0, 1
    values_by_name, rows = {}, []
    while at < len(il) - 1:
        p.budget.check()
        start = at
        # Shared typed/RVA reader proves newarr int32, trusted InitializeArray,
        # explicit value-type ClassLayout, exact data size and in-range stores.
        at, values, calls, array = _int_array(p, il, at)
        _require(at < len(il) - 1 and il[at].opcode == 0x80, 'POOL_ARRAY_PUBLICATION_SHAPE')
        token = il[at].operand
        _require(token in by_token, 'POOL_STORE_OWNER_OR_FIELD')
        name = by_token[token]
        _require(name not in values_by_name, 'POOL_DUPLICATE_FIELD_STORE')
        _require(all(type(value) is int and 0 <= value < count for value in values), 'POOL_ID_OUT_OF_DOMAIN')
        _require(len(values) == len(set(values)), 'POOL_DUPLICATE_ID')
        # Starting and ending each recipe with an empty evaluation stack makes
        # exact grammar coverage a whole-method effect proof. No opaque tail.
        stack = 4 if any(i.opcode == 0x9e for i in il[start:at]) else 3 if calls else 1
        required_stack = max(required_stack, stack)
        values_by_name[name] = values
        rows.append({
            'name': name, 'fieldToken': _hex(token), 'count': len(values),
            'allocationIdentity': f"{_hex(pool['token'])}:{array['allocationIlOffset']}",
            'recipeStartIlOffset': il[start].offset, 'storeIlOffset': il[at].offset,
            'requiredMaxStack': stack, 'arrayEvidence': array,
            'trustedCalls': [{'ilOffset': call.offset, 'memberToken': _hex(call.operand),
                              'effect': 'core RuntimeHelpers.InitializeArray copies exact RVA int32 payload into fresh array'}
                             for call in calls],
        })
        _require(len(rows) <= len(POOLS), 'POOL_EXCESS_INITIALIZER_RECIPES')
        at += 1
    _require(at == len(il) - 1 and set(values_by_name) == set(POOLS), 'POOL_COMPLETE_INITIALIZER_COVERAGE')
    _require(pool_stack >= required_stack, 'POOL_MAXSTACK_TOO_SMALL')
    pools = {name: values_by_name[name] for name in POOLS}
    union = {value for values in pools.values() for value in values}
    result = {
        'schemaVersion': 1, 'family': 'prefix-pools',
        'status': 'PROVEN_PREFIX_POOL_INITIALIZER_BOUNDARY', 'factScope': 'INITIALIZER_BOUNDARY',
        'wholeInitializerProven': True, 'independentDomainInitializerProven': True,
        'normalReturnGuaranteed': False, 'executedInput': False,
        'complete': False, 'publishable': False, 'runtimeSnapshotUsable': False,
        'scope': 'fresh ordered prefix arrays at the normal-return boundary of PrefixLegacy+Prefixes..cctor only',
        'pools': pools, 'poolEvidence': rows,
        'declaredDomain': {'owner': _COUNT_OWNER, 'field': 'Count', 'fieldToken': _hex(count_token),
                           'count': count, 'minInclusive': 0, 'maxExclusive': count,
                           'source': 'whole independent readonly Count initializer',
                           'factScope': 'INITIALIZER_BOUNDARY'},
        'ordering': 'source array index order preserved without sorting or deduplication',
        'stats': {'poolCount': len(pools), 'poolEntryCount': sum(map(len, pools.values())),
                  'uniqueReferencedPrefixCount': len(union), 'prefixDomainCount': count},
        'methodEvidence': {**pool['evidence'], 'declaredMaxStack': pool_stack, 'requiredMaxStack': required_stack},
        'countMethodEvidence': {**count_init['evidence'], 'declaredMaxStack': count_stack, 'requiredMaxStack': 1},
        'fieldEvidence': [row for row in p.evidence if 'fieldToken' in row],
        'preconditions': ['normal-returning CLI initialization and allocations',
                          'trusted core-library identity and InitializeArray intrinsic semantics',
                          'no concurrent mutation during the initializer boundary observation'],
        'unsupported': ['subsequent writes to public pool fields or array elements',
                        'final runtime pool snapshot', 'prefix names, effects or item eligibility',
                        'final item defaults, complete consumer group and publication'],
    }
    json_evidence_size(result, p.budget.limits.evidence_bytes, p.budget.check)
    return result


def extract_prefix_pool_semantics(input_path: Path, checkpoint=None):
    """Read one fixed source as data; return private boundary facts and evidence.

    There is deliberately no profile/hash/owner override. Unknown source bytes,
    unsupported instructions and exhausted budgets raise rather than returning
    partially usable pools. Callers must not publish the returned source arrays.
    """
    check = checkpoint or (lambda: None)
    check()
    raw = read_assembly_bytes(input_path, SemanticLimits(), check)
    digest = sha256(raw)
    profile = _PROFILES.get(digest)
    _require(profile is not None, 'POOL_UNSUPPORTED_INPUT_PROFILE')
    limits = ItemDispatchSetLimits(literal_ids=256, literal_bytes=1024,
                                   method_bytes=8192, total_method_bytes=16384,
                                   instructions=4096, steps=1000000, evidence_bytes=1024 * 1024)
    try:
        p = _Program(raw, _Budget(limits, check))
        pool, _ = _cctor(p, _OWNER)
        count, _ = _cctor(p, _COUNT_OWNER)
        for method, key in ((pool, 'pool'), (count, 'count')):
            _require(method['token'] == profile[key + 'Token']
                     and method['signature'] == _CCTOR_SIG
                     and method['evidence']['ilSha256'] == profile[key + 'IlSha256']
                     and method['evidence']['signatureSha256'] == _SIGNATURE_SHA256,
                     'POOL_PROFILE_METHOD_PIN')
        result = _prove(p, pool, count)
        result.update(sourceRole=profile['role'], inputSha256=digest, gameVersion='1.4.5.8')
        json_evidence_size(result, limits.evidence_bytes, p.budget.check)
        return result
    except _CheckpointCancelled as exc:
        raise exc.original
