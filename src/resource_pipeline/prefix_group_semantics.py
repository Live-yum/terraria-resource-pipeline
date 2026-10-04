"""Private, finite PrefixLegacy.ItemSets initializer proof from PE/CLI bytes.

This is a normal-return initializer-boundary theorem under explicit intrinsic
and no-interference assumptions. No PE/CLR execution, final mutable snapshot,
item eligibility decision, production adapter, or publication is implemented.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct

from .item_assembler import GROUPS
from .item_dispatch_sets import ItemDispatchSetLimits, _bool_factory
from .item_texture_aliases import (
    _Budget, _CheckpointCancelled, _Program, _constant, _hex, _require, _shape,
)
from .server_semantics import SemanticLimits, read_assembly_bytes
from .set_factory_constructor import prove_set_factory_constructor
from .set_factory_lifecycle import _bind_getter, _int_array
from .static_il import json_evidence_size


_OWNER = 'Terraria.GameContent.Prefixes.PrefixLegacy+ItemSets'
_COUNT_OWNER = 'Terraria.ID.ItemID'
_SIGNATURE = b'\x00\x00\x01'
_SIGNATURE_SHA256 = 'cf7605ed1bc735f6c825554154627467e1cac9df54cee8699218ed434603c568'
_PROFILES = {
    '960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3': {
        'role': 'client', 'methodToken': 0x06004c9f,
        'ilSha256': '6119c0e5d1558fc746611e65d43daae63245597333aa7e7bd2a56b9dd27c9497',
    },
    'd87e3faf08637f6be8882c63e7f11fb7e792b0230006309618473ece0f863e1e': {
        'role': 'server', 'methodToken': 0x0600492d,
        'ilSha256': 'c51c51e5f0b66470f2b82317761d252d8df079efd9afa8b87b33d7726a8cc196',
    },
}


def _maxstack(p, method, required):
    declared = 8 if method['header'] == 1 else p.meta.reader.uint(method['evidence']['bodyOffset'] + 2, 2)
    _require(declared >= required, 'GROUP_MAXSTACK_TOO_SMALL')
    return {**method['evidence'], 'declaredMaxStack': declared, 'requiredMaxStack': required}


def _no_initializer(p, owner):
    _, typedef = p.owner(owner)
    _require(not any(p.meta.string(p.row(6, rid)[0][3]) == '.cctor'
                     for rid in range(typedef['firstMethod'], typedef['lastMethod'])),
             'GROUP_UNMODELED_INITIALIZER')


def _cctor(p):
    # Check name uniqueness before matching the signature, including malformed
    # overloads that could otherwise hide beside the expected .cctor.
    method = p.method(_OWNER, '.cctor')
    row, _ = p.row(6, method['token'] & 0xffffff)
    _require(method['signature'] == _SIGNATURE and row[2] == 0x1891
             and not method['local'] and not method['eh'], 'GROUP_INITIALIZER_CONTRACT')
    il = method['instructions']
    _require(il and il[-1].opcode == 0x2a and sum(i.opcode == 0x2a for i in il) == 1,
             'GROUP_SINGLE_FINAL_RETURN')
    return method


def _count_binding(p, domain):
    """Bind a completed independent Count proof, never a literal-store slice.

    Private composition input comes only from id_count_semantics in the public
    API. Booleans supplied by an external caller are not an evidence route.
    """
    token = p.field(_COUNT_OWNER, 'Count', b'\x06\x06')
    _require(p.row(4, token & 0xffffff)[0][0] == 0x36, 'GROUP_COUNT_FIELD_FLAGS')
    method = p.method(_COUNT_OWNER, '.cctor')
    _require(domain.get('owner') == _COUNT_OWNER and domain.get('fieldToken') == _hex(token)
             and domain.get('wholeInitializerProven') is True
             and domain.get('independentDomainInitializerProven') is True
             and domain.get('externalTailCountNonmutationProven') is True
             and domain.get('factScope') == 'INITIALIZER_BOUNDARY'
             and all(domain.get('countMethodEvidence', {}).get(key) == method['evidence'][key]
                     for key in ('methodToken', 'ilSha256', 'signatureSha256', 'codeBytes')),
             'GROUP_INDEPENDENT_COUNT_PROOF_BINDING')
    count = domain.get('count')
    _require(type(count) is int and 0 < count <= min(32767, p.budget.limits.item_count),
             'GROUP_ITEM_DOMAIN_LIMIT')
    # A method hash must not be paired with a different numeric conclusion.
    # This additional equality binds the completed theorem's value; it does
    # not replace the mandatory independent external-tail/whole-cctor proof.
    _require(method['instructions'] and _constant(method['instructions'][0]) == count,
             'GROUP_COUNT_VALUE_BINDING')
    return token, count


def _prove(p, domain):
    """Internal composition and original-fixture entry, not a public profile API."""
    p.budget.check()
    count_token, count = _count_binding(p, domain)
    method = _cctor(p)
    owner_rid, _ = p.owner(_OWNER)
    owner_row, _ = p.row(2, owner_rid)
    _require(owner_row[0] & 7 == 2 and not owner_row[0] & (0x18 | 0x20 | 0x80)
             and owner_row[3] & 3 == 1, 'GROUP_OWNER_CONTRACT')
    p.external_type(0x01000000 | (owner_row[3] >> 2), 'System.Object')
    _require(not any(r[2] == owner_rid for r in p.table(15)), 'GROUP_OWNER_LAYOUT')
    _no_initializer(p, '<Module>')
    _no_initializer(p, 'Terraria.ID.SetFactory')
    constructor = prove_set_factory_constructor(p)
    wrapper, fill, getter, transform = _bool_factory(p)
    queue = _bind_getter(p, getter, constructor, 2)
    _require(queue is not None and constructor['lock'] is not None,
             'GROUP_FRESH_BOOL_QUEUE_REQUIRED')
    # Exact helpers close all branches, calls, fields and the getter's finally
    # region. Check declared stack capacity too: these maxima follow directly
    # from those matched helper grammars (not an optimistic runtime assumption).
    helper_methods = [p.body(int(constructor['methodToken'], 16)), wrapper, fill, getter]
    helper_evidence = [_maxstack(p, helper, required)
                       for helper, required in zip(helper_methods, (2, 3, 4, 2))]

    factory_rid, _ = p.owner('Terraria.ID.SetFactory')
    factory = p.field(_OWNER, 'Factory', b'\x06' + p.type_signature(0x02000000 | factory_rid))
    fields = {name: p.field(_OWNER, name, b'\x06\x1d\x02') for name in GROUPS}
    _, owner = p.owner(_OWNER)
    _require(owner['lastField'] - owner['firstField'] == 1 + len(GROUPS), 'GROUP_EXACT_FIELD_SET')
    for token in (factory, *fields.values()):
        _require(p.row(4, token & 0xffffff)[0][0] == 0x16, 'GROUP_PUBLIC_STATIC_FIELD_FLAGS')
    _require(not any((0x04000000 | r[1]) in (factory, *fields.values()) for r in p.table(16)),
             'GROUP_STATIC_FIELD_LAYOUT')
    by_token = {token: name for name, token in fields.items()}
    il = method['instructions']
    _shape(il[:3], [(0x7e, count_token), (0x73, int(constructor['methodToken'], 16)), (0x80, factory)])
    at, required_stack = 3, 1
    groups, records, discharged = {}, [], [
        {'ilOffset': il[1].offset, 'targetToken': constructor['methodToken'],
         'effect': 'fresh factory; size is independent completed Count; distinct empty queues and lock'}]
    while at < len(il) - 1:
        p.budget.check()
        start = at
        _shape(il[at:at + 1], [(0x7e, factory)])
        at, values, calls, array = _int_array(p, il, at + 1)
        _require(at + 1 < len(il) - 1, 'GROUP_TRUNCATED_RECIPE')
        capture = _shape(il[at:at + 2], [(0x6f, wrapper['token']), (0x80, 'field')])
        token = capture['field']
        _require(token in by_token, 'GROUP_STORE_OWNER_OR_FIELD')
        name = by_token[token]
        _require(name not in groups, 'GROUP_DUPLICATE_FIELD_STORE')
        _require(all(type(value) is int and 0 <= value < count for value in values), 'GROUP_ID_OUT_OF_DOMAIN')
        _require(len(values) == len(set(values)), 'GROUP_DUPLICATE_LITERAL_ID')
        # Every recipe starts and ends with an empty evaluation stack. Its one
        # receiver remains below the entire fresh int[] construction. No other
        # effects are admitted and no aliases to the factory queues escape.
        stack = 5 if any(i.opcode == 0x9e for i in il[start:at]) else 4 if calls else 2
        required_stack = max(required_stack, stack)
        literal_hash = hashlib.sha256(b''.join(struct.pack('<i', value) for value in values)).hexdigest()
        identity = f"{_hex(method['token'])}:{il[at].offset}:{_hex(getter['token'])}"
        records.append({
            'name': name, 'fieldToken': _hex(token), 'literalCount': len(values),
            'uniqueLiteralCount': len(set(values)), 'orderedLiteralSha256': literal_hash,
            'recipeStartIlOffset': il[start].offset, 'factoryCallIlOffset': il[at].offset,
            'storeIlOffset': il[at + 1].offset, 'requiredMaxStack': stack,
            'arrayEvidence': array, 'allocationIdentity': identity, 'length': count,
            'defaultValue': False, 'overrideValue': True, 'allIdsInIndependentDomain': True,
            'queueFieldToken': queue, 'queueBefore': 'empty', 'queueAfter': 'empty',
            'enqueues': 0, 'dequeues': 0, 'bufferIdentity': 'fresh distinct bool[] allocation',
        })
        discharged.extend({'ilOffset': call.offset, 'targetToken': _hex(call.operand),
                           'effect': 'typed InitializeArray copies exact RVA int32 bytes into fresh argument array'}
                          for call in calls)
        discharged.append({'ilOffset': il[at].offset, 'targetToken': _hex(wrapper['token']),
                           'effect': 'empty queue yields fresh bool[Count]; fill false then assign true at literal IDs; queue remains empty'})
        groups[name] = values
        _require(len(groups) <= len(GROUPS), 'GROUP_EXCESS_RECIPES')
        at += 2
    _require(at == len(il) - 1 and set(groups) == set(GROUPS), 'GROUP_COMPLETE_INITIALIZER_COVERAGE')
    method_evidence = _maxstack(p, method, required_stack)
    method_evidence.update(instructionCount=len(il), allInstructionsCovered=True,
                          recipeBoundaryStackEmpty=True, returnStackEmpty=True,
                          branchCount=0, localCount=0, exceptionRegionCount=0)
    result = {
        'schemaVersion': 1, 'family': 'prefix-groups',
        'status': 'PROVEN_PREFIX_GROUP_INITIALIZER_BOUNDARY', 'factScope': 'INITIALIZER_BOUNDARY',
        'wholeInitializerProven': True, 'independentDomainInitializerProven': True,
        'numericSizeProven': True, 'normalReturnGuaranteed': False,
        'executedInput': False, 'complete': False, 'publishable': False, 'runtimeSnapshotUsable': False,
        'runtimeDependencyBindingVerified': False,
        'scope': 'seven fresh distinct bool arrays at normal return of PrefixLegacy+ItemSets..cctor only',
        'groups': {name: groups[name] for name in GROUPS}, 'groupEvidence': records,
        'declaredDomain': {**domain, 'field': 'Count', 'minInclusive': 0, 'maxExclusive': count,
                           'source': 'independent whole ItemID initializer with external tail nonmutation proof'},
        'methodEvidence': method_evidence, 'helperMethodEvidence': helper_evidence,
        'fieldEvidence': [row for row in p.evidence if 'fieldToken' in row],
        'factory': {'fieldToken': _hex(factory), 'constructor': constructor,
                    'transform': transform, 'freshDistinctOutputCount': len(records),
                    'boolQueueFieldToken': queue, 'boolQueueAtBoundary': 'empty',
                    'bufferLengthStatus': 'PROVEN_FRESH_COUNT_AT_INITIALIZER_BOUNDARY',
                    'supportedRecycleOperations': 0, 'freshSize': count},
        'dischargedCalls': discharged,
        'stats': {'groupCount': len(groups), 'groupEntryCount': sum(map(len, groups.values())),
                  'uniqueReferencedItemCount': len({v for values in groups.values() for v in values}),
                  'itemDomainCount': count},
        'ordering': 'original literal order retained; bool membership is false except at those IDs',
        'preconditions': ['independent ItemID initializer completes normally before the Count read; no reentrant default Count observation',
                          'group initializer, helper calls, allocations and CLI type initialization return normally',
                          'ordinary trusted core Object/Queue/Monitor/InitializeArray contracts including core-owned type initialization',
                          'no concurrent/external substitution of public Factory or group fields, mutation of arrays, recycling, reflection/unsafe/native tampering',
                          'independent Count dependency intrinsic and callback-cache assumptions remain in force',
                          'modeled ReLogic reference binds to the independently audited dependency bytes; actual CLR/AssemblyResolve binding is not verified'],
        'unsupported': ['mutable state after the initializer boundary', 'full per-item prefix eligibility',
                        'runtime item defaults, prefix effects, complete consumer group or publication',
                        'actual CLR/AssemblyResolve dependency binding',
                        'Count interval alone as evidence of valid item or material membership'],
    }
    json_evidence_size(result, p.budget.limits.evidence_bytes, p.budget.check)
    return result


def extract_prefix_group_semantics(input_path: Path, checkpoint=None):
    """Fixed-hash, fail-closed private source proof; callers must not publish IDs.

    There is no hash, profile, owner, Count value or dependency-proof override.
    Unknown shapes and exhausted budgets raise; partial groups never return.
    """
    from .id_count_semantics import prove_id_count_program
    check = checkpoint or (lambda: None)
    check()
    raw = read_assembly_bytes(input_path, SemanticLimits(), check)
    digest = hashlib.sha256(raw).hexdigest()
    profile = _PROFILES.get(digest)
    _require(profile is not None, 'GROUP_UNSUPPORTED_INPUT_PROFILE')
    limits = ItemDispatchSetLimits(literal_ids=1024, literal_bytes=4096,
                                   instructions=1000000, steps=20000000,
                                   evidence_bytes=4 * 1024 * 1024, wall_seconds=120)
    try:
        p = _Program(raw, _Budget(limits, check))
        method = _cctor(p)
        _require(method['token'] == profile['methodToken']
                 and method['evidence']['ilSha256'] == profile['ilSha256']
                 and method['evidence']['signatureSha256'] == _SIGNATURE_SHA256
                 and method['size'] == 246 and len(method['instructions']) == 61,
                 'GROUP_PROFILE_METHOD_PIN')
        independent = prove_id_count_program(p)
        _require(independent['inputSha256'] == digest, 'GROUP_COUNT_INPUT_BINDING')
        result = _prove(p, independent['domains']['ItemID'])
        _require(independent['runtimeDependencyBindingVerified'] is False, 'GROUP_DEPENDENCY_BOUNDARY')
        result['modeledReLogicDependency'] = independent['modeledReLogicDependency']
        result['countDependencyEvidence'] = {key: value for key, value in independent.items()
                                             if key != 'domains'}
        result.update(inputSha256=digest, sourceRole=profile['role'], gameVersion='1.4.5.8')
        json_evidence_size(result, limits.evidence_bytes, p.budget.check)
        return result
    except _CheckpointCancelled as exc:
        raise exc.original
