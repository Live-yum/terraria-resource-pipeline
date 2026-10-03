"""Bounded bool-set initializer recipes from PE bytes; never game execution.

The theorem is conditional: on a normal-returning factory call with a buffer
covering the declared domain, fill with default then assign its complement at
each literal ID. It is not a whole-cctor or mutable runtime-state certificate.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import struct

from .item_texture_aliases import (
    _Budget, _Program, _shape, _linear_store, _instance_field, _finally_contract,
    _initialize_array, _constant, _compressed_bytes, _require, _hex, _CheckpointCancelled,
)
from .security import PipelineError
from .static_il import EvidenceBudget, EvidenceSizeLimit, ILUnsupported, _compressed, json_evidence_size


@dataclass(frozen=True)
class ItemDispatchSetLimits:
    input_bytes: int = 128 * 1024 * 1024
    item_count: int = 100_000
    literal_ids: int = 25_000
    literal_bytes: int = 1024 * 1024
    method_bytes: int = 1024 * 1024
    total_method_bytes: int = 4 * 1024 * 1024
    instructions: int = 500_000
    steps: int = 2_000_000
    evidence_bytes: int = 4 * 1024 * 1024
    wall_seconds: float = 30

    def __post_init__(self):
        for name, value in vars(self).items():
            if name == 'wall_seconds':
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 120:
                    raise ValueError('Invalid dispatch-set wall-time budget')
            elif type(value) is not int or value <= 0:
                raise ValueError('Invalid dispatch-set integer budget')
        if self.evidence_bytes < 16 * 1024:
            raise ValueError('Dispatch-set evidence needs a diagnostic envelope')


_NAMES = ('IsFood', 'Deprecated', 'IsDrill', 'IsChainsaw')


def _bool_queue(p, capture):
    _instance_field(p, capture['lock'], '_queueLock', b'\x06\x1c')
    rows = []
    for token, name, sig in ((capture['count'], 'get_Count', b'\x20\x00\x08'),
                             (capture['dequeue'], 'Dequeue', b'\x20\x00\x13\x00')):
        _require(token >> 24 == 10, 'DISPATCH_QUEUE_MEMBER_MISMATCH')
        row, _ = p.row(10, token & 0xffffff)
        _require(row[0] & 7 == 4 and p.meta.string(row[1]) == name and p.meta.blob(row[2])[0] == sig,
                 'DISPATCH_QUEUE_MEMBER_MISMATCH')
        rows.append(row)
    _require(rows[0][0] == rows[1][0], 'DISPATCH_QUEUE_OWNER_MISMATCH')
    signature = p.type_signature(0x1b000000 | rows[0][0] >> 3)
    _require(signature[:2] == b'\x15\x12', 'DISPATCH_QUEUE_TYPE_MISMATCH')
    coded, pos = _compressed(signature, 2)
    _require(coded & 3 == 1 and signature[pos:] == b'\x01\x1d\x02', 'DISPATCH_QUEUE_TYPE_MISMATCH')
    row = p.external_type(0x01000000 | coded >> 2, 'System.Collections.Generic.Queue`1', core=False)
    assembly, _ = p.row(35, row[0] >> 2)
    _require(p.meta.string(assembly[6]) in ('System', 'mscorlib') and not assembly[4]
             and not p.meta.string(assembly[7]) and p.meta.blob(assembly[5])[0] == bytes.fromhex('b77a5c561934e089'),
             'DISPATCH_QUEUE_ASSEMBLY_MISMATCH')
    _instance_field(p, capture['queue'], '_boolBufferCache', b'\x06' + signature)


def _bool_factory(p):
    wrapper = p.method('Terraria.ID.SetFactory', 'CreateBoolSet', b'\x20\x01\x1d\x02\x1d\x08')
    method = p.method('Terraria.ID.SetFactory', 'CreateBoolSet', b'\x20\x02\x1d\x02\x02\x1d\x08')
    _require(not wrapper['local'] and method['local'] == b'\x07\x03\x1d\x02\x08\x08',
             'DISPATCH_FACTORY_LOCALS_MISMATCH')
    _shape(wrapper['instructions'], [2, 0x16, 3, (0x28, method['token']), 0x2a])
    # Two loops with exact bounds and local bindings. The second loop stores
    # !default; it does not toggle the old value at repeated indexes.
    capture = _shape(method['instructions'], [2,(0x28,'buffer'),0x0a,0x16,0x0b,(0x2b,'@14'),
        6,7,3,0x9c,7,0x17,0x58,0x0b,7,6,0x8e,0x69,(0x32,'@6'),
        0x16,0x0c,(0x2b,'@34'),6,4,8,0x94,3,0x16,0xfe01,0x9c,8,0x17,0x58,0x0c,
        8,4,0x8e,0x69,(0x32,'@22'),6,0x2a])
    getter = p.method('Terraria.ID.SetFactory', 'GetBoolBuffer', b'\x20\x00\x1d\x02', allow_eh=True)
    _require(capture['buffer'] == getter['token'], 'DISPATCH_FACTORY_BUFFER_BINDING_MISMATCH')
    if getter['eh']:
        _require(getter['local'] == b'\x07\x03\x1c\x02\x1d\x02', 'DISPATCH_GETTER_LOCALS_MISMATCH')
        g = _shape(getter['instructions'], [2,(0x7b,'lock'),0x0a,0x16,0x0b,6,(0x12,1),(0x28,'enter'),
            2,(0x7b,'queue'),(0x6f,'count'),(0x2d,'@17'),2,(0x7b,'size'),(0x8d,'bool'),0x0c,(0xde,'@27'),
            2,(0x7b,'queue'),(0x6f,'dequeue'),0x0c,(0xde,'@27'),7,(0x2c,'@26'),6,(0x28,'exit'),0xdc,8,0x2a])
        p.member(g['enter'], 'System.Threading.Monitor', 'Enter', b'\x00\x02\x01\x1c\x10\x02')
        p.member(g['exit'], 'System.Threading.Monitor', 'Exit', b'\x00\x01\x01\x1c')
        _bool_queue(p, g)
        _finally_contract(p, getter)
        provider = 'new bool[_size] when queue empty; otherwise dequeue; normal-return EH path verified'
    else:
        _require(not getter['local'], 'DISPATCH_GETTER_LOCALS_MISMATCH')
        g = _shape(getter['instructions'], [2,(0x7b,'size'),(0x8d,'bool'),0x2a])
        provider = 'new bool[_size]'
    p.external_type(g['bool'], 'System.Boolean')
    _instance_field(p, g['size'], '_size', b'\x06\x08')
    return wrapper, method, getter, {
        'status': 'PROVEN_NORMAL_RETURN_FACTORY_TRANSFORM',
        'wrapperMethodToken': _hex(wrapper['token']), 'defaultedMethodToken': _hex(method['token']),
        'bufferMethodToken': _hex(getter['token']), 'bufferProvider': provider,
        'wrapperDefault': False, 'semantics': 'fill every returned-buffer slot with default; store !default at every supplied ID in input order',
        'duplicates': 'idempotent assignment, not toggling',
        'bufferLengthStatus': 'UNPROVEN_AT_CALL_SITE',
        'precondition': 'factory returns normally with a bool buffer whose length covers [0, declaredCount); all literal indexes are in range',
    }


def _rva_ids(p, token, count):
    _require(0 < count <= p.budget.limits.literal_ids and count * 4 <= p.budget.limits.literal_bytes,
             'DISPATCH_LITERAL_LIMIT')
    _require(token >> 24 == 4, 'DISPATCH_RVA_FIELD_TOKEN_MISMATCH')
    row, field_offset = p.row(4, token & 0xffffff)
    signature, signature_offset = p.meta.blob(row[2])
    _require(row[0] & 0x110 == 0x110 and not row[0] & (0x40 | 0x2000) and signature[:2] == b'\x06\x11',
             'DISPATCH_RVA_FIELD_SIGNATURE_MISMATCH')
    coded, end = _compressed(signature, 2)
    _require(end == len(signature) and coded & 3 == 0 and coded >> 2 in p.types,
             'DISPATCH_RVA_LAYOUT_MISMATCH')
    typedef, _ = p.row(2, coded >> 2)
    _require(typedef[0] & 0x18 == 0x10 and typedef[0] & 0x100 and not typedef[0] & 0xa0,
             'DISPATCH_RVA_LAYOUT_MISMATCH')
    _require(typedef[3] & 3 == 1, 'DISPATCH_RVA_VALUE_TYPE_MISMATCH')
    p.external_type(0x01000000 | typedef[3] >> 2, 'System.ValueType')
    shape = p.types[coded >> 2]
    _require(shape['firstField'] == shape['lastField'] and shape['firstMethod'] == shape['lastMethod']
             and not any(r[2] == coded >> 1 for r in p.table(42)), 'DISPATCH_RVA_LAYOUT_MEMBERS')
    layouts = [r for r in p.table(15) if r[2] == coded >> 2]
    size = count * 4
    _require(len(layouts) == 1 and layouts[0][0] == 1 and layouts[0][1] == size, 'DISPATCH_RVA_LAYOUT_MISMATCH')
    rows = [(rid, r) for rid, r in enumerate(p.table(29), 1) if r[1] == token & 0xffffff]
    _require(len(rows) == 1, 'DISPATCH_RVA_LOCATION_MISSING_OR_AMBIGUOUS')
    rid, location = rows[0]
    _, metadata_offset = p.row(29, rid)
    offset = p.meta.rva(location[0], size)
    raw = p.meta.reader.take(offset, size)
    ids = []
    for start in range(0, size, 4):
        p.budget.check()
        ids.append(struct.unpack_from('<i', raw, start)[0])
    return ids, {'fieldToken': _hex(token), 'fieldMetadataOffset': field_offset,
        'signatureOffset': signature_offset, 'signatureSha256': hashlib.sha256(signature).hexdigest(),
        'fieldRvaMetadataOffset': metadata_offset, 'dataRva': location[0], 'dataOffset': offset,
        'dataBytes': size, 'dataSha256': hashlib.sha256(raw).hexdigest(), 'encoding': 'CLI little-endian signed int32'}


def _consumer(p, fields):
    method = p.method('Terraria.Item', 'SetDefaults')
    signature = method['signature']
    _require(signature in (b'\x20\x01\x01\x08',) or signature[:5] == b'\x20\x02\x01\x08\x12',
             'DISPATCH_CONSUMER_SIGNATURE_MISMATCH')
    if len(signature) > 4:
        coded, end = _compressed(signature, 5)
        _require(end == len(signature) and coded & 3 == 0 and p.types.get(coded >> 2, {}).get('fullName') ==
                 'Terraria.GameContent.Items.ItemVariant', 'DISPATCH_CONSUMER_SIGNATURE_MISMATCH')
    _, owner = p.owner('Terraria.Item')
    found = []
    for rid in range(owner['firstField'], owner['lastField']):
        row, offset = p.row(4, rid)
        if p.meta.string(row[1]) == 'type':
            _require(not row[0] & (0x10 | 0x40 | 0x100 | 0x2000) and p.meta.blob(row[2])[0] == b'\x06\x08',
                     'DISPATCH_CONSUMER_TYPE_FIELD_MISMATCH')
            found.append(0x04000000 | rid)
    _require(len(found) == 1, 'DISPATCH_CONSUMER_TYPE_FIELD_MISMATCH')
    by_token = {token: name for name, token in fields.items()}
    reads = []
    il = method['instructions']
    for n, ins in enumerate(il):
        p.budget.check()
        if ins.opcode in (0x7e, 0x7f, 0x80) and ins.operand in by_token:
            _require(ins.opcode == 0x7e and n + 4 < len(il), 'DISPATCH_UNSUPPORTED_CONSUMER_ACCESS')
            _shape(il[n:n + 4], [(0x7e, ins.operand), 2, (0x7b, found[0]), 0x91])
            branch = il[n + 4]
            _require(branch.opcode in (0x2c, 0x2d, 0x39, 0x3a), 'DISPATCH_UNSUPPORTED_CONSUMER_BRANCH')
            reads.append({'set': by_token[ins.operand], 'loadIlOffset': ins.offset,
                'index': 'this.type', 'indexFieldToken': _hex(found[0]), 'elementReadIlOffset': il[n + 3].offset,
                'branchIlOffset': branch.offset, 'branchWhen': bool(branch.opcode in (0x2d, 0x3a)),
                'branchTargetIlOffset': branch.operand, 'fallthroughIlOffset': branch.next_offset})
    _require(set(row['set'] for row in reads) == set(fields), 'DISPATCH_CONSUMER_READ_MISSING')
    return {'status': 'PROVEN_DIRECT_READ_BINDINGS_ONLY', 'methodToken': _hex(method['token']),
            'reads': reads, 'controlFlowOrRuntimeClosure': False}


def _extract(p, evidence):
    fields = {name: p.field('Terraria.ID.ItemID+Sets', name, b'\x06\x1d\x02') for name in _NAMES}
    count_field = p.field('Terraria.ID.ItemID', 'Count')
    _require(p.fields[count_field] in (b'\x06\x06', b'\x06\x08'), 'DISPATCH_COUNT_SIGNATURE_MISMATCH')
    count_method = p.method('Terraria.ID.ItemID', '.cctor', b'\x00\x00\x01')
    count_store = _linear_store(count_method, count_field, 1)
    count = _constant(count_store[0])
    _require(0 < count <= p.budget.limits.item_count and (p.fields[count_field] != b'\x06\x06' or count <= 32767),
             'DISPATCH_ITEM_COUNT_LIMIT')
    factory_rid, _ = p.owner('Terraria.ID.SetFactory')
    factory_field = p.field('Terraria.ID.ItemID+Sets', 'Factory', b'\x06\x12' + _compressed_bytes(factory_rid << 2))
    cctor = p.method('Terraria.ID.ItemID+Sets', '.cctor', b'\x00\x00\x01')
    head = _shape(cctor['instructions'], [(0x7e, count_field), (0x73, 'ctor'), (0x80, factory_field)], prefix=True)
    ctor = p.method('Terraria.ID.SetFactory', '.ctor', b'\x20\x01\x01\x08')
    _require(ctor['token'] == head['ctor'], 'DISPATCH_FACTORY_CONSTRUCTOR_MISMATCH')
    _linear_store(cctor, factory_field, 2)
    wrapper, factory, getter, factory_evidence = _bool_factory(p)
    # Constructor closure is a separate theorem, never a later-call pool-state
    # assumption. Preserve the existing conditional recipes on unsupported ctor
    # shapes, but never swallow a shared budget limit or cancellation.
    from .set_factory_constructor import prove_set_factory_constructor
    try:
        constructor = prove_set_factory_constructor(p)
    except ILUnsupported as exc:
        if 'LIMIT' in exc.code:
            raise
        constructor = {'status': exc.code, 'cacheStateAtLaterCallProven': False,
                       'wholeInitializerProven': False}
    evidence.charge(constructor)
    factory_evidence['freshConstructor'] = constructor
    recipes = []
    accepted_calls = {wrapper['token'], factory['token']}
    for name, field in fields.items():
        p.budget.check()
        # Wrapper invocation has eight instructions before its destination.
        part = _linear_store(cctor, field, 7)
        explicit = False
        if part[0].opcode != 0x7e:
            part = _linear_store(cctor, field, 8)
            explicit = True
        _require(part[0].opcode == 0x7e and part[0].operand == factory_field,
                 'DISPATCH_INITIALIZER_FACTORY_MISMATCH')
        default = _constant(part[1]) if explicit else 0
        _require(default in (0, 1), 'DISPATCH_DEFAULT_NOT_BOOLEAN')
        at = 2 if explicit else 1
        elements = _constant(part[at])
        cap = _shape(part[at + 1:], [(0x8d,'int'),0x25,(0xd0,'data'),(0x28,'initialize'),
                                   (0x6f, factory['token'] if explicit else wrapper['token']), (0x80,field)])
        p.external_type(cap['int'], 'System.Int32')
        _initialize_array(p, cap['initialize'])
        accepted_calls.add(cap['initialize'])
        ids, rva = _rva_ids(p, cap['data'], elements)
        _require(all(0 <= value < count for value in ids), 'DISPATCH_LITERAL_ID_OUT_OF_DOMAIN')
        unique = sorted(set(ids))
        record = {'name': name, 'fieldToken': _hex(field), 'status': 'PROVEN_INITIALIZER_RECIPE',
            'initializerMethodToken': _hex(cctor['token']), 'startIlOffset': part[0].offset,
            'callIlOffset': part[-2].offset, 'storeIlOffset': part[-1].offset,
            'declaredCount': count, 'domain': {'minInclusive': 0, 'maxExclusive': count},
            'arrayLengthStatus': 'CONDITIONAL_ON_RETURNED_BUFFER_DOMAIN_COVERAGE',
            'defaultValue': bool(default), 'overrideValue': not bool(default), 'literalIds': ids,
            'distinctOverrideIds': unique, 'literalCount': len(ids), 'distinctCount': len(unique),
            'duplicateCount': len(ids) - len(unique), 'rvaEvidence': rva,
            'cctorNormalReturnSnapshotUsable': False, 'runtimeSnapshotUsable': False}
        evidence.charge(record)
        recipes.append(record)
    residual = {}
    for method in (count_method, cctor, ctor):
        for ins in method['instructions']:
            p.budget.check()
            if ins.opcode in (0x28,0x6f,0x73) and ins.operand not in accepted_calls:
                key = (method['token'], ins.operand)
                if key not in residual:
                    record = {'callerMethodToken': _hex(method['token']), 'targetToken': _hex(ins.operand),
                              'firstIlOffset': ins.offset, 'occurrences': 0, 'status': 'UNSUPPORTED_EFFECT_SUMMARY'}
                    evidence.charge(record, extra=32)
                    residual[key] = record
                residual[key]['occurrences'] += 1
    domain = {'count': count, 'countFieldToken': _hex(count_field), 'methodToken': _hex(count_method['token']),
        'constantIlOffset': count_store[0].offset, 'storeIlOffset': count_store[-1].offset,
        'status': 'PROVEN_LITERAL_COUNT_STORE_ONLY', 'mutableCountSnapshotUsable': False}
    return recipes, factory_evidence, domain, list(residual.values()), fields


def _extract_item_dispatch_sets(data: bytes, *, limits=ItemDispatchSetLimits(), checkpoint=None):
    """Inspect input bytes and return bounded private conditional evidence.

    Input is never loaded, executed or imported. Cancellation exceptions from
    checkpoint propagate. Malformed/unsupported profiles return no usable table.
    Unknown cctor effects are retained as blockers, never treated as pure.
    """
    if type(data) is not bytes:
        raise PipelineError('Dispatch-set input must be immutable bytes')
    if len(data) > limits.input_bytes:
        raise PipelineError('Dispatch-set input byte limit exceeded')
    budget = _Budget(limits, checkpoint)
    evidence = EvidenceBudget(limits.evidence_bytes, budget.check)
    result = {'schemaVersion': 1, 'scope': 'ItemID.Sets four dispatch initializer recipes',
        'status': 'UNRESOLVED', 'executedInput': False, 'complete': False, 'finalItemDefaults': False,
        'publishable': False, 'inputSha256': hashlib.sha256(data).hexdigest(), 'sets': [], 'metadataEvidence': [],
        'cctorNormalReturnSnapshotUsable': False, 'runtimeSnapshotUsable': False,
        'assumptions': ['factory call returns normally with a buffer covering the declared domain',
            'literal Count store and fresh Factory binding identify an intended domain, not a proved mutable Count value at every read',
            'InitializeArray and core-library metadata identities are trusted intrinsics, not assembly signature verification',
            'constructor evidence ends at fresh normal return; later cache state, full type-initializer, mutation, client-equivalence and final-default closure remain unproved']}
    p = None
    try:
        evidence.charge(result, extra=2048)
        budget.check()
        p = _Program(data, budget)
        recipes, factory, domain, residual, fields = _extract(p, evidence)
        result.update(status='PROVEN_CONDITIONAL_INITIALIZER_RECIPES', sets=recipes, factory=factory,
            declaredDomain=domain, residualEffects={'status': 'UNSUPPORTED_WHOLE_CCTOR_EFFECT_CLOSURE',
                'unmodeledCalls': residual})
        try:
            consumer = _consumer(p, fields)
        except ILUnsupported as exc:
            if 'LIMIT' in exc.code:
                raise
            consumer = {'status': exc.code, 'controlFlowOrRuntimeClosure': False}
        evidence.charge(consumer)
        result['consumer'] = consumer
        evidence.charge(p.evidence)
        result['metadataEvidence'] = p.evidence
    except ILUnsupported as exc:
        result.update(status=exc.code, sets=[], diagnostic={'code': exc.code, 'ilOffset': exc.offset,
            'token': _hex(exc.token) if exc.token is not None else None})
    except (PipelineError, ValueError, UnicodeError, struct.error, IndexError, KeyError, RecursionError, TypeError) as exc:
        result.update(status='DISPATCH_MALFORMED_INPUT', sets=[], metadataEvidence=[],
            diagnostic={'code': 'DISPATCH_MALFORMED_INPUT', 'errorType': type(exc).__name__})
    result['work'] = {'steps': budget.steps, 'decodedInstructions': budget.instructions,
        'decodedMethodBytes': budget.method_bytes, 'constructionEvidenceBytes': evidence.used}
    # Final exact size check has the same cancellation/deadline gate; limit
    # failures never leave partially usable table evidence behind.
    try:
        json_evidence_size(result, limits.evidence_bytes, budget.check)
    except (ILUnsupported, EvidenceSizeLimit) as exc:
        result = {'schemaVersion': 1, 'status': getattr(exc, 'code', 'DISPATCH_EVIDENCE_BYTE_LIMIT'), 'executedInput': False, 'complete': False,
            'finalItemDefaults': False, 'publishable': False, 'sets': [],
            'cctorNormalReturnSnapshotUsable': False, 'runtimeSnapshotUsable': False}
    return result


def extract_item_dispatch_sets(data: bytes, *, limits=ItemDispatchSetLimits(), checkpoint=None):
    """Public bounded data-only API; caller cancellation always propagates."""
    try:
        return _extract_item_dispatch_sets(data, limits=limits, checkpoint=checkpoint)
    except _CheckpointCancelled as exc:
        raise exc.original
