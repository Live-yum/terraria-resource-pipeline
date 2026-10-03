"""A closed straight-line first custom-call slice, never a whole initializer.

The size is symbolic: the precise Count read, not an earlier literal store.
Evidence describes single-thread CLI effects absent external interference.
"""
from .item_texture_aliases import _require, _shape, _hex, _compressed_bytes, _constant
from .static_il import _compressed, _type


def _no_initializer(p, owner):
    for rid in range(owner['firstMethod'], owner['lastMethod']):
        row, _ = p.row(6, rid)
        _require(p.meta.string(row[3]) != '.cctor', 'FIRST_CUSTOM_IMPLICIT_INITIALIZER')


def _default(p, cctor, il, at, signature):
    # CLI primitive int32 default, or a fully initialized, closed integer struct.
    if signature == b'\x08':
        return at + 1, {'kind': 'int32', 'value': _constant(il[at])}
    _require(signature[:1] == b'\x11', 'FIRST_CUSTOM_DEFAULT_TYPE')
    coded, end = _compressed(signature, 1)
    _require(end == len(signature) and coded & 3 == 0, 'FIRST_CUSTOM_DEFAULT_TYPE')
    token = 0x02000000 | coded >> 2
    owner = p.types.get(coded >> 2)
    _require(owner is not None, 'FIRST_CUSTOM_DEFAULT_TYPE')
    rid, owner = p.owner(owner['fullName'])
    row, offset = p.row(2, rid)
    _require(not row[0] & (0x10 | 0x20 | 0x80) and row[3] & 3 == 1,
             'FIRST_CUSTOM_DEFAULT_LAYOUT')
    p.external_type(0x01000000 | row[3] >> 2, 'System.ValueType')
    _require(owner['firstMethod'] == owner['lastMethod'], 'FIRST_CUSTOM_DEFAULT_METHODS')
    # Runtime-recognized attributes (for example IsByRefLike) can forbid T[].
    _require(not any(r[0] == rid << 5 | 3 for r in p.table(12)),
             'FIRST_CUSTOM_DEFAULT_ATTRIBUTES')
    _require(not any(r[2] == rid for r in p.table(15)), 'FIRST_CUSTOM_DEFAULT_LAYOUT')
    # First local must be exactly this concrete struct; no byrefs/generic aliases.
    local_count, pos = _compressed(cctor['local'], 1)
    _, local_end = _type(cctor['local'], pos)
    _require(local_count > 0 and cctor['local'][pos:local_end] == signature,
             'FIRST_CUSTOM_LOCAL_BINDING')
    _shape(il[at:at+2], [(0x12, 0), (0xfe15, token)])
    at += 2
    fields = {}
    laid_out_fields = {r[1] for r in p.table(16)}
    for fid in range(owner['firstField'], owner['lastField']):
        f, foffset = p.row(4, fid)
        sig, sigoffset = p.meta.blob(f[2])
        _require(not f[0] & (0x10 | 0x40 | 0x100 | 0x2000) and sig in (b'\x06\x08', b'\x06\x06'),
                 'FIRST_CUSTOM_DEFAULT_FIELD')
        _require(fid not in laid_out_fields, 'FIRST_CUSTOM_DEFAULT_LAYOUT')
        fields[0x04000000 | fid] = {'fieldToken': _hex(0x04000000 | fid),
            'signature': sig.hex(), 'metadataOffset': foffset, 'signatureOffset': sigoffset,
            'value': 0, 'source': 'initobj'}
    _require(fields, 'FIRST_CUSTOM_DEFAULT_FIELD')
    while at < len(il) and il[at].opcode == 0x12:
        p.budget.check()
        cap = _shape(il[at:at+3], [(0x12, 0), (il[at+1].opcode, 'literal'), (0x7d, 'field')])
        value = _constant(il[at+1])
        _require(cap['field'] in fields, 'FIRST_CUSTOM_DEFAULT_FIELD_BINDING')
        field = fields[cap['field']]
        _require(field['signature'] != '0606' or -32768 <= value <= 32767,
                 'FIRST_CUSTOM_DEFAULT_FIELD_RANGE')
        field.update(value=value, source='literal store', storeIlOffset=il[at+2].offset)
        at += 3
    _shape(il[at:at+1], [6])
    return at + 1, {'kind': 'closed integer value type', 'typeToken': _hex(token),
        'metadataOffset': offset, 'fields': list(fields.values())}


def prove_first_custom_call(p, constructor, custom):
    _require(constructor['status'] == 'PROVEN_FRESH_CONSTRUCTOR_NORMAL_RETURN',
             'FIRST_CUSTOM_CONSTRUCTOR_UNPROVEN')
    cctor = p.method('Terraria.ID.ItemID+Sets', '.cctor', b'\x00\x00\x01')
    il = cctor['instructions']
    # Reject every branch, including later backedges into the certified prefix.
    _require(not any(0x2b <= i.opcode <= 0x45 or i.opcode in (0x27,0x29,0xdd,0xde,0xfe14)
                     for i in il), 'FIRST_CUSTOM_CONTROL_FLOW')
    count = p.field('Terraria.ID.ItemID', 'Count')
    _require(p.fields[count] in (b'\x06\x06', b'\x06\x08'), 'FIRST_CUSTOM_COUNT_TYPE')
    rid, owner = p.owner('Terraria.ID.SetFactory')
    _no_initializer(p, owner)
    factory = p.field('Terraria.ID.ItemID+Sets', 'Factory', b'\x06\x12' + _compressed_bytes(rid << 2))
    _shape(il[:4], [(0x7e,count), (0x73,int(constructor['methodToken'],16)),
                   (0x80,factory), (0x7e,factory)])
    _require(constructor['sizeFieldToken'] == custom['sizeFieldToken'], 'FIRST_CUSTOM_SIZE_BINDING')
    # Only the first candidate may be discharged; later calls have unknown state.
    calls = custom['emptyArgumentCalls']
    _require(calls, 'FIRST_CUSTOM_CALL_MISSING')
    candidate = calls[0]
    target = int(custom['methodToken'], 16)
    parameters = [r for r in p.table(42) if r[2] == ((target & 0xffffff) << 1 | 1)]
    _require(len(parameters) == 1 and parameters[0][0] == 0 and parameters[0][1] == 0,
             'FIRST_CUSTOM_GENERIC_CONSTRAINT')
    spec, _ = p.row(43, int(candidate['methodSpecToken'],16) & 0xffffff)
    signature = p.meta.blob(spec[1])[0]
    _require(signature[:2] == b'\x0a\x01', 'FIRST_CUSTOM_METHODSPEC')
    at, default = _default(p, cctor, il, 4, signature[2:])
    _require(_constant(il[at]) == 0, 'FIRST_CUSTOM_PAIRS_NOT_EMPTY')
    cap = _shape(il[at+1:at+3], [(0x8d,'object'), (0x6f,int(candidate['methodSpecToken'],16))])
    p.external_type(cap['object'], 'System.Object')
    _require(il[at+2].offset == candidate['callIlOffset'], 'FIRST_CUSTOM_CALL_BINDING')
    return {'status': 'PROVEN_FIRST_CUSTOM_CALL_NORMAL_RETURN_SLICE',
        'callerMethodToken': _hex(cctor['token']), 'callIlOffset': candidate['callIlOffset'],
        'targetToken': candidate['methodSpecToken'], 'startIlOffset': il[0].offset,
        'endExclusiveIlOffset': il[at+2].next_offset,
        'factoryFieldToken': _hex(factory), 'freshAllocationIlOffset': il[1].offset,
        'receiverIdentity': 'the unique new SetFactory allocated in this prefix',
        'size': {'kind': 'captured static read', 'fieldToken': _hex(count),
                 'readIlOffset': il[0].offset, 'numericValueProven': False},
        'defaultValue': default, 'genericInstantiationValidityProven': True,
        'result': 'fresh array; length equals captured Count read; every element copies defaultValue',
        'receiverMutatedBetweenConstructionAndCall': False,
        'receiverEscape': 'published only to the matched Factory static field',
        'preconditions': ['single-thread CLI execution without external interference',
            'initial Count read and all prefix operations return normally',
            'trusted core-library identities and valid CLI execution'],
        'callerReachabilityGuaranteed': False, 'normalReturnGuaranteed': False,
        'numericSizeProven': False, 'wholeInitializerProven': False,
        'runtimeSnapshotUsable': False}
