"""Conditional empty-pair CreateCustomSet effects, using PE/CLI bytes only.

No certificate for nonempty pairs, caller reachability, pool lifecycle, or a
whole initializer. Unreachable conversion IL is decoded and hash-bound but is
not assigned semantics or counted as proven.
"""
from __future__ import annotations

import hashlib
import struct

from .item_texture_aliases import (
    ItemTextureAliasLimits, _Budget, _Program, _CheckpointCancelled,
    _require, _shape, _instance_field, _hex, _constant,
)
from .security import PipelineError
from .static_il import ILUnsupported, EvidenceSizeLimit, _type, json_evidence_size


def prove_empty_custom_set(p):
    method = p.method('Terraria.ID.SetFactory', 'CreateCustomSet',
                      b'\x30\x01\x02\x1d\x1e\x00\x1e\x00\x1d\x1c')
    _require(method['local'] == b'\x07\x04\x1d\x1e\x00\x08\x08\x1e\x00',
             'EMPTY_CUSTOM_LOCALS')
    il = method['instructions']
    cap = _shape(il, [4,0x8e,0x69,0x18,0x5d,(0x2c,'@9'),(0x72,'error'),
        (0x73,'throwctor'),0x7a,2,(0x7b,'size'),(0x8d,'element'),0x0a,0x16,0x0b,
        (0x2b,'@24'),6,7,3,(0xa4,'element'),7,0x17,0x58,0x0b,
        7,6,0x8e,0x69,(0x32,'@16'),4,(0x39,'return'),0x16,0x0c,(0x38,'check')], prefix=True)
    _require(len(il) >= 42, 'EMPTY_CUSTOM_TRUNCATED')
    tail = il[-7:]
    _shape(tail, [8,4,0x8e,0x69,(0x3f,il[34].offset),6,0x2a])
    _require(cap['return'] == tail[-2].offset and cap['check'] == tail[0].offset
             and tail[0].offset > il[34].offset, 'EMPTY_CUSTOM_CONTROL_FLOW')
    _require(p.type_signature(cap['element']) == b'\x1e\x00', 'EMPTY_CUSTOM_GENERIC_ELEMENT')
    _instance_field(p, cap['size'], '_size', b'\x06\x08')
    field_row, field_offset = p.row(4, cap['size'] & 0xffffff)
    field_signature, field_signature_offset = p.meta.blob(field_row[2])
    p.evidence.append({'fieldToken': _hex(cap['size']), 'name': '_size',
        'metadataOffset': field_offset, 'signatureOffset': field_signature_offset,
        'signatureSha256': hashlib.sha256(field_signature).hexdigest()})
    p.user_string(cap['error'])
    p.member(cap['throwctor'], 'System.Exception', '.ctor', b'\x20\x01\x01\x0e')
    return {'status': 'PROVEN_EMPTY_PAIR_NORMAL_RETURN_EFFECTS',
        'methodToken': _hex(method['token']), 'sizeFieldToken': _hex(cap['size']),
        'genericElementToken': _hex(cap['element']),
        'preconditions': ['receiver is non-null; pairs is a non-null object array of length zero',
                          'method returns normally; valid CLI generic instantiation and core-library semantics'],
        'result': 'fresh distinct T array sized by the matched receiver._size read; every element copies the supplied default value',
        'defaultCopy': 'CLI value copy; referenced objects in the default are not cloned',
        'writes': 'new result array only', 'factoryOrPoolWrites': False,
        'receiverEscapes': False, 'pairsMutated': False,
        'executedCallsOnEmptyPath': [],
        'skippedConversionRegion': {'startIlOffset': il[34].offset, 'endExclusiveIlOffset': tail[0].offset,
                                    'reason': 'counter zero < empty pairs length zero is false'},
        'nonemptyPairsProven': False, 'normalReturnGuaranteed': False,
        'cacheStateAtCallSiteProven': False, 'wholeInitializerProven': False}


def _empty_calls(p, proof):
    """Identify top-of-stack empty arrays, not the rest of the caller state."""
    cctor = p.method('Terraria.ID.ItemID+Sets', '.cctor', b'\x00\x00\x01')
    method_token = int(proof['methodToken'], 16)
    calls = []
    il = cctor['instructions']
    # A branch into the allocation/call slice invalidates the local stack fact.
    _require(not any(0x2b <= i.opcode <= 0x45 or i.opcode in (0x27,0x29,0xdd,0xde,0xfe14)
                     for i in il), 'EMPTY_CUSTOM_CALLER_CONTROL_FLOW')
    for n, ins in enumerate(il):
        p.budget.check()
        if ins.opcode not in (0x28, 0x6f) or ins.operand >> 24 != 43:
            continue
        spec, offset = p.row(43, ins.operand & 0xffffff)
        if spec[0] != (method_token & 0xffffff) << 1:
            continue
        signature, signature_offset = p.meta.blob(spec[1])
        _require(signature[:2] == b'\x0a\x01', 'EMPTY_CUSTOM_METHODSPEC')
        kind, end = _type(signature, 2)
        _require(end == len(signature) and kind != 'void', 'EMPTY_CUSTOM_METHODSPEC')
        if n < 2 or il[n-1].opcode != 0x8d:
            continue
        try:
            length = _constant(il[n-2])
        except ILUnsupported:
            continue
        if length != 0:
            continue
        p.external_type(il[n-1].operand, 'System.Object')
        calls.append({'callerMethodToken': _hex(cctor['token']), 'callIlOffset': ins.offset,
            'methodSpecToken': _hex(ins.operand), 'methodSpecMetadataOffset': offset,
            'instantiationSignatureOffset': signature_offset,
            'instantiationSignatureSha256': hashlib.sha256(signature).hexdigest(),
            'arrayAllocationIlOffset': il[n-1].offset,
            'pairs': 'fresh non-null empty object[] on normal allocation return',
            'status': 'PROVEN_EMPTY_ARGUMENT_SLICE_ONLY', 'receiverAndSizeAtCallProven': False,
            'defaultValueAtCallProven': False, 'callerReachabilityProven': False,
            'genericInstantiationValidityProven': False,
            'precondition': 'MethodSpec denotes a valid CLI instantiation in this caller context'})
    return calls


def extract_empty_custom_set(data: bytes, *, limits=ItemTextureAliasLimits(), checkpoint=None):
    budget = _Budget(limits, checkpoint)
    result = {'schemaVersion': 1, 'status': 'UNRESOLVED', 'complete': False, 'publishable': False,
        'executedInput': False, 'runtimeSnapshotUsable': False, 'wholeInitializerProven': False,
        'scope': 'conditional empty-pair CreateCustomSet effects and local empty-argument slices',
        'metadataEvidence': []}
    try:
        budget.check()
        _require(type(data) is bytes and len(data) <= limits.input_bytes, 'EMPTY_CUSTOM_INPUT_LIMIT')
        result['inputSha256'] = hashlib.sha256(data).hexdigest()
        p = _Program(data, budget)
        proof = prove_empty_custom_set(p)
        calls = _empty_calls(p, proof)
        result.update(status=proof['status'], proof=proof, emptyArgumentCalls=calls, metadataEvidence=p.evidence)
        json_evidence_size(result, limits.evidence_bytes, budget.check)
    except _CheckpointCancelled as exc:
        raise exc.original
    except (ILUnsupported, EvidenceSizeLimit) as exc:
        result.update(status=getattr(exc, 'code', 'EMPTY_CUSTOM_EVIDENCE_LIMIT'), metadataEvidence=[])
        result.pop('proof', None); result.pop('emptyArgumentCalls', None)
    except (PipelineError, ValueError, UnicodeError, struct.error, IndexError, KeyError, RecursionError, TypeError):
        result.update(status='EMPTY_CUSTOM_MALFORMED_INPUT', metadataEvidence=[])
        result.pop('proof', None); result.pop('emptyArgumentCalls', None)
    return result
