"""Fail-closed composition of a straight-line fresh-factory initializer prefix.

This is a normal-return slice, not a runtime snapshot. Every supported call has
an exact IL or trusted core-library effect summary. Unknown effects stop the
prefix; no later call can borrow its heap facts. No game code is executed.
"""
import hashlib

from .item_texture_aliases import (
    _require, _shape, _hex, _constant, _compressed_bytes, _factory,
    _initialize_array,
)
from .static_il import ILUnsupported, EvidenceBudget, _compressed


def _store(p, token, signature):
    """Only publish to fields on the already-running Sets initializer owner."""
    _, owner = p.owner('Terraria.ID.ItemID+Sets')
    _require(token >> 24 == 4 and owner['firstField'] <= token & 0xffffff < owner['lastField'],
             'LIFECYCLE_STORE_OWNER')
    row, _ = p.row(4, token & 0xffffff)
    _require(row[0] & 0x10 and not row[0] & (0x40 | 0x100 | 0x2000)
             and p.meta.blob(row[2])[0] == signature, 'LIFECYCLE_STORE_SIGNATURE')


def _list_member(p, token, name, signature):
    _require(token >> 24 == 10, 'LIFECYCLE_LIST_MEMBER')
    row, offset = p.row(10, token & 0xffffff)
    _require(row[0] & 7 == 4 and p.meta.string(row[1]) == name
             and p.meta.blob(row[2])[0] == signature, 'LIFECYCLE_LIST_MEMBER')
    sig = p.type_signature(0x1b000000 | row[0] >> 3)
    _require(sig[:2] == b'\x15\x12', 'LIFECYCLE_LIST_TYPE')
    coded, pos = _compressed(sig, 2)
    _require(coded & 3 == 1 and sig[pos:] == b'\x01\x08', 'LIFECYCLE_LIST_TYPE')
    p.external_type(0x01000000 | coded >> 2, 'System.Collections.Generic.List`1')
    # This intrinsic includes List<int> initialization. int32 Add does not run
    # user constructors, equality comparers, conversion operators or callbacks.
    member_sig, member_sig_offset = p.meta.blob(row[2])
    spec_row, spec_offset = p.row(27, row[0] >> 3)
    _, spec_sig_offset = p.meta.blob(spec_row[0])
    return row[0], sig, {'memberToken': _hex(token), 'metadataOffset': offset,
        'signatureOffset': member_sig_offset, 'signatureSha256': hashlib.sha256(member_sig).hexdigest(),
        'ownerTypeSpecToken': _hex(0x1b000000 | row[0] >> 3), 'ownerMetadataOffset': spec_offset,
        'ownerSignatureOffset': spec_sig_offset, 'ownerSignatureSha256': hashlib.sha256(sig).hexdigest()}


def _list_slice(p, il, at):
    start = at
    owner, signature, ctor_evidence = _list_member(p, il[at].operand, '.ctor', b'\x20\x00\x01')
    calls = [il[at]]; values = []; members = {il[at].operand: ctor_evidence}; at += 1
    while at < len(il) and il[at].opcode == 0x25:
        p.budget.check()
        cap = _shape(il[at:at+3], [0x25, (il[at+1].opcode, 'value'), (0x6f, 'add')])
        value = _constant(il[at+1])
        add_owner, add_sig, add_evidence = _list_member(p, cap['add'], 'Add', b'\x20\x01\x01\x13\x00')
        _require((add_owner, add_sig) == (owner, signature), 'LIFECYCLE_LIST_OWNER')
        _require(len(values) < p.budget.limits.literal_ids, 'LIFECYCLE_LITERAL_LIMIT')
        members[cap['add']] = add_evidence
        values.append(value); calls.append(il[at+2]); at += 3
    _require(at < len(il) and il[at].opcode == 0x80, 'LIFECYCLE_LIST_STORE')
    _store(p, il[at].operand, b'\x06' + signature)
    return at+1, calls, {'kind': 'fresh List<int32>', 'allocationIlOffset': il[start].offset,
        'storeIlOffset': il[at].offset, 'fieldToken': _hex(il[at].operand),
        'count': len(values), 'values': values, 'memberEvidence': list(members.values()),
        'factoryOrPoolReferences': False, 'effects': 'new list/private storage and trusted core-owned initialization; no factory/pool/game-state effects'}


def _int_array(p, il, at):
    from .item_dispatch_sets import _rva_ids
    count = _constant(il[at])
    _require(0 <= count <= p.budget.limits.literal_ids and count * 4 <= p.budget.limits.literal_bytes,
             'LIFECYCLE_LITERAL_LIMIT')
    cap = _shape(il[at+1:at+2], [(0x8d, 'element')])
    p.external_type(cap['element'], 'System.Int32')
    allocation = il[at+1].offset; at += 2; calls = []; evidence = None
    values = [0] * count
    if at+2 < len(il) and il[at].opcode == 0x25 and il[at+1].opcode == 0xd0:
        cap = _shape(il[at:at+3], [0x25,(0xd0,'data'),(0x28,'initialize')])
        _initialize_array(p, cap['initialize'])
        values, evidence = _rva_ids(p, cap['data'], count)
        calls.append(il[at+2]); at += 3
    while at < len(il) and il[at].opcode == 0x25:
        p.budget.check()
        _require(at+3 < len(il), 'LIFECYCLE_ARRAY_TRUNCATED')
        _shape(il[at:at+4], [0x25,(il[at+1].opcode,'index'),(il[at+2].opcode,'value'),0x9e])
        index, value = _constant(il[at+1]), _constant(il[at+2])
        _require(0 <= index < count, 'LIFECYCLE_ARRAY_INDEX')
        values[index] = value; at += 4
    return at, values, calls, {'allocationIlOffset': allocation, 'length': count, 'rvaEvidence': evidence}


def _bind_getter(p, getter, constructor, element):
    """Called only AFTER the exact getter body/EH/type contract was proved."""
    il = getter['instructions']
    if getter['eh']:
        queue, lock, size = il[9].operand, il[1].operand, il[13].operand
        caches = {c['fieldToken']: c for c in constructor['caches']}
        _require(_hex(queue) in caches and constructor['lock'] is not None
                 and _hex(lock) == constructor['lock']['fieldToken'], 'LIFECYCLE_POOL_BINDING')
        expected = '_boolBufferCache' if element == 2 else '_intBufferCache'
        _require(caches[_hex(queue)]['fieldName'] == expected, 'LIFECYCLE_POOL_BINDING')
    else:
        size = il[1].operand; queue = None
    _require(_hex(size) == constructor['sizeFieldToken'], 'LIFECYCLE_SIZE_BINDING')
    return _hex(queue) if queue is not None else None


def _primitive_slice(p, il, at, constructor, bool_methods, summaries):
    start = at; at += 1
    # Both supported overload families use int32 literal arrays. Distinguish
    # explicit defaults by the exact following newarr, never a callee name.
    explicit = at+2 < len(il) and il[at+2].opcode == 0x8d
    default = _constant(il[at]) if explicit else None
    if explicit: at += 1
    at, values, calls, arg = _int_array(p, il, at)
    _require(at+1 < len(il) and il[at].opcode == 0x6f and il[at+1].opcode == 0x80,
             'LIFECYCLE_PRIMITIVE_CALL_STORE')
    token = il[at].operand
    wrapper, method, getter = bool_methods
    if token in (wrapper['token'], method['token']):
        _require(explicit == (token == method['token']) and default in (None, 0, 1),
                 'LIFECYCLE_BOOL_ARGUMENTS')
        default = 0 if default is None else default
        element = 2; overrides = [[index, 1-default] for index in values]
        summary = summaries.setdefault(token, {'queue': _bind_getter(p, getter, constructor, element)})
    else:
        # Verify the complete int factory and getter, including every call and
        # the exception handler, before the token is entered in the cache.
        _require(explicit, 'LIFECYCLE_UNSUPPORTED_OVERLOAD')
        if token not in summaries:
            proof = _factory(p, token)
            int_getter = p.body(int(proof['bufferMethodToken'],16), allow_eh=True)
            summaries[token] = {'queue': _bind_getter(p, int_getter, constructor, 8)}
        summary = summaries[token]; element = 8
        _require(len(values) % 2 == 0, 'LIFECYCLE_ODD_PAIRS')
        overrides = [values[i:i+2] for i in range(0,len(values),2)]
    _require(all(index >= 0 for index, _ in overrides), 'LIFECYCLE_NEGATIVE_INDEX')
    _store(p, il[at+1].operand, bytes((6,0x1d,element)))
    calls.append(il[at])
    return at+2, calls, {'kind': 'fresh primitive array', 'elementType': 'bool' if element==2 else 'int32',
        'allocationIdentity': {'callerIlOffset': il[at].offset, 'callee': _hex(token)},
        'fieldToken': _hex(il[at+1].operand), 'storeIlOffset': il[at+1].offset,
        'defaultValue': default, 'orderedOverrides': overrides, 'argumentArray': arg,
        'size': 'captured initial Count read', 'numericSizeProven': False,
        'queueFieldToken': summary['queue'], 'queueBefore': 'empty', 'queueAfter': 'empty',
        'normalReturnImplies': 'all override indexes are smaller than the captured array size',
        'bufferIdentity': 'fresh allocation; empty queue path does not dequeue',
        'startIlOffset': il[start].offset}


def prove_factory_lifecycle_prefix(p, constructor, first_custom, bool_methods):
    _require(first_custom['status'] == 'PROVEN_FIRST_CUSTOM_CALL_NORMAL_RETURN_SLICE',
             'LIFECYCLE_FIRST_CALL_UNPROVEN')
    method = p.method('Terraria.ID.ItemID+Sets', '.cctor', b'\x00\x00\x01')
    il = method['instructions']; factory = int(first_custom['factoryFieldToken'],16)
    at = next(n for n,i in enumerate(il) if i.offset == first_custom['endExclusiveIlOffset'])
    # The initial custom result may be discarded or published to a correctly
    # typed field. Either operation cannot mutate the factory or its queues.
    if il[at].opcode == 0x26:
        at += 1
    else:
        _require(il[at].opcode == 0x80, 'LIFECYCLE_CUSTOM_RESULT_ESCAPE')
        default = first_custom['defaultValue']
        element = b'\x08' if default['kind'] == 'int32' else b'\x11' + _compressed_bytes((int(default['typeToken'],16)&0xffffff)<<2)
        _store(p, il[at].operand, b'\x06\x1d'+element); at += 1
    records = []; calls = []; summaries = {}; stop = None
    custom_summary = None
    evidence = EvidenceBudget(p.budget.limits.evidence_bytes, p.budget.check)
    while at < len(il):
        p.budget.check(); ins = il[at]
        if ins.opcode == 0x2a:
            stop = {'reason': 'end of supported initializer', 'ilOffset': ins.offset}; break
        try:
            if ins.opcode == 0x73:
                end, newcalls, record = _list_slice(p, il, at)
            elif ins.opcode == 0x7e and ins.operand == factory:
                try:
                    end, newcalls, record = _primitive_slice(p, il, at, constructor, bool_methods, summaries)
                except ILUnsupported as primitive_error:
                    if 'LIMIT' in primitive_error.code: raise
                    from .set_factory_custom_literals import prove_literal_custom_method, literal_custom_slice
                    if custom_summary is None:
                        custom_summary = prove_literal_custom_method(p)
                    end, newcalls, record = literal_custom_slice(p, method, at, constructor, custom_summary)
            elif ins.opcode in (*range(0x15, 0x1f), 0x1f, 0x20):
                try:
                    end, values, newcalls, record = _int_array(p, il, at)
                    element = 8
                except ILUnsupported as array_error:
                    if 'LIMIT' in array_error.code: raise
                    _require(_constant(ins) == 0, 'LIFECYCLE_EMPTY_BOOL_ARRAY')
                    _shape(il[at+1:at+3], [(0x8d, 'bool'), (0x80, 'field')])
                    p.external_type(il[at+1].operand, 'System.Boolean')
                    end, values, newcalls, element = at+2, [], [], 2
                    record = {'allocationIlOffset': il[at+1].offset, 'length': 0}
                _require(end < len(il) and il[end].opcode == 0x80, 'LIFECYCLE_ARRAY_STORE')
                _store(p, il[end].operand, bytes((6,0x1d,element)))
                record.update(kind='fresh '+('int32' if element == 8 else 'bool')+' literal array', values=values,
                              fieldToken=_hex(il[end].operand), storeIlOffset=il[end].offset)
                end += 1
            else:
                raise ILUnsupported('LIFECYCLE_UNSUPPORTED_EFFECT')
        except ILUnsupported as exc:
            if 'LIMIT' in exc.code: raise
            stop = {'reason': exc.code, 'ilOffset': ins.offset,
                    'effectsAfterBoundary': 'unknown; all heap facts stop here'}
            break
        # Commit only an entire supported slice. A rejected candidate contributes
        # no occurrences, writes or heap transitions to the certificate.
        evidence.charge(record, extra=256 * len(newcalls) + 1)
        records.append(record); calls.extend(newcalls); at = end
    _require(stop is not None, 'LIFECYCLE_MISSING_RETURN')
    for later in il[at:]:
        p.budget.check()
        if later.opcode in (0x28, 0x6f, 0x73):
            stop['firstFollowingUnprovedCall'] = {'ilOffset': later.offset, 'targetToken': _hex(later.operand)}
            break
    return {'status': 'PROVEN_FRESH_FACTORY_NORMAL_RETURN_PREFIX',
        'callerMethodToken': _hex(method['token']), 'startIlOffset': il[0].offset,
        'endExclusiveIlOffset': il[at].offset, 'stop': stop,
        'factoryFieldToken': _hex(factory), 'size': first_custom['size'],
        'allocationsAndStores': records,
        'dischargedCalls': [{'callerMethodToken': _hex(method['token']), 'callIlOffset': i.offset,
                            'targetToken': _hex(i.operand)} for i in calls],
        'queues': [{'fieldToken': c['fieldToken'], 'stateAtBoundary': 'fresh empty queue',
                    'enqueues': 0, 'dequeues': 0} for c in constructor['caches']],
        'factoryPublication': 'the matched Sets.Factory static field only; no unknown calls crossed',
        'preconditions': first_custom['preconditions'] + ['every operation through the prefix boundary returns normally',
            'trusted List<int32> constructor/Add including core-owned type initialization have no user callbacks or factory/pool/game-state effects'],
        'normalReturnGuaranteed': False, 'numericSizeProven': False,
        'wholeInitializerProven': False, 'runtimeSnapshotUsable': False,
        'supportedRecycleOperations': 0, 'unknownCallsInvalidateContinuation': True}
