"""Closed, data-only player fact shapes in one pinned PE; never load a CLR.

Only the named constructor prefix, call-free hair branches, Count prefixes and
Constant rows are interpreted. Receipts are conditional byte evidence, not an
initialization, mutation-closure, rendering or publication certificate.
"""
from __future__ import annotations

import struct

from .item_texture_aliases import _Program, _Budget, ItemTextureAliasLimits, _constant, _CheckpointCancelled
from .security import PipelineError, canonical_json, sha256
from .static_il import ILUnsupported, _compressed

SOURCE_SHA256 = '960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3'
_METHODS = {
    'hair': (0x06000910, 'Terraria.Player', 'GetHairSettings', '20050110021002100210021002',
             'a2b4a95e9f342351293907125b823bd83a183d9d86215b1c460637845e49e67e'),
    'clothes': (0x06002db8, 'Terraria.GameContent.UI.States.UICharacterCreation', '.ctor', '200101128124',
                'e2c8995911dad11606ae3718fd318a61e73ec754c754b1bb0657d32fb9893939'),
    'buffCount': (0x060020d8, 'Terraria.ID.BuffID', '.cctor', '000001',
                  '79bbdb0f968b06a8182ef439a2d167112e0ce69492e8d6caf219769bb414ca11'),
    'faceCount': (0x06004595, 'Terraria.ID.ArmorIDs+Face', '.cctor', '000001',
                  'cca4ba46f034efc6a74b6d3988527ad7730d17e65fcd68cbfa70dc445a5805fb'),
}


def _need(ok, message):
    if not ok: raise PipelineError('Player source shape: ' + message)


def _field(program, token, owner, name, signature, *, static=False, literal=False):
    _, typedef = program.owner(owner)
    _need(token >> 24 == 4 and typedef['firstField'] <= token & 0xffffff < typedef['lastField'], 'field owner mismatch')
    row, offset = program.row(4, token & 0xffffff)
    raw, signature_offset = program.meta.blob(row[2])
    _need(program.meta.string(row[1]) == name and raw.hex() == signature
          and bool(row[0] & 0x10) == static and bool(row[0] & 0x40) == literal, 'field identity mismatch')
    return {'token': f'0x{token:08x}', 'owner': owner, 'name': name,
            'metadataOffset': offset, 'signatureOffset': signature_offset, 'signatureSha256': sha256(raw)}


def _method(program, key):
    token, owner, name, signature, digest = _METHODS[key]
    value = program.method(owner, name, bytes.fromhex(signature))
    _need(value['token'] == token and value['evidence']['ilSha256'] == digest, 'method pin mismatch')
    return value


def _count_prefix(instructions, field):
    _need(len(instructions) >= 3 and instructions[0].offset == 0
          and instructions[1].opcode == 0x80 and instructions[1].operand == field, 'Count prefix mismatch')
    count = _constant(instructions[0])
    _need(1 < count <= 4096, 'Count outside bounded domain')
    return count


def _hair_head_sets(instructions, end, head_field, checkpoint=lambda: None):
    """Evaluate only forward call-free integer branches over the consumer domain.

    References are tagged, locals must be assigned before reads, all four outputs
    must be initialized, and every decoded instruction in the region is checked,
    including unreachable instructions. No object, general call or heap model.
    """
    ops = {i.offset: i for i in instructions if i.offset < end}
    _need(0 in ops and 0 < len(ops) <= 1024 and end in {i.offset for i in instructions}, 'head region bounds')
    allowed = {2, 3, 4, 5, 0x0e, 6, 7, 8, 9, 0x0a, 0x0b, 0x0c, 0x0d,
               *range(0x15, 0x21), 0x25, 0x52, 0x7b, 0x59, 0x2b, 0x38, 0x39, 0x30, 0x3b, 0x3d, 0x45}
    for ins in ops.values():
        _need(ins.opcode in allowed, 'unsupported head opcode')
        if ins.opcode == 0x7b: _need(ins.operand == head_field, 'unexpected head field')
        if ins.opcode == 0x0e: _need(ins.operand in (1, 2, 3, 5), 'unexpected head argument')
        targets = ins.operand if ins.opcode == 0x45 else (ins.operand,) if ins.opcode in (0x2b, 0x38, 0x39, 0x30, 0x3b, 0x3d) else ()
        _need(all(ins.offset < target <= end and (target == end or target in ops) for target in targets), 'head branch leaves closed forward region')
    output = {'fullHairHeads': [], 'hatHairHeads': [], 'drawsBackWithoutHeadgear': []}
    for head in range(-1, 4096):
        checkpoint()
        args = [('this',)] + [('out', n) for n in range(1, 6)]
        stack, locals_, flags, pc = [], {}, {}, 0
        def pop_int():
            _need(bool(stack) and type(stack[-1]) is int, 'integer stack mismatch')
            return stack.pop()
        for _ in range(len(ops) + 1):
            if pc == end: break
            _need(pc in ops and len(stack) <= 16, 'head stack or PC bound')
            ins = ops[pc]; k, v, pc = ins.opcode, ins.operand, ins.next_offset
            if 2 <= k <= 5: stack.append(args[k - 2])
            elif k == 0x0e: stack.append(args[v])
            elif 6 <= k <= 9:
                _need(k - 6 in locals_, 'uninitialized head local'); stack.append(locals_[k - 6])
            elif 0x0a <= k <= 0x0d: locals_[k - 0x0a] = pop_int()
            elif 0x15 <= k <= 0x20: stack.append(_constant(ins))
            elif k == 0x25:
                _need(bool(stack), 'empty dup'); stack.append(stack[-1])
            elif k == 0x52:
                value = pop_int()
                _need(bool(stack) and stack[-1] in (('out', 1), ('out', 2), ('out', 3), ('out', 5))
                      and value in (0, 1), 'unexpected head output store')
                flags[stack.pop()[1]] = value
            elif k == 0x7b:
                _need(bool(stack) and stack.pop() == ('this',), 'unexpected head receiver'); stack.append(head)
            elif k == 0x59:
                y, x = pop_int(), pop_int(); value = x - y
                _need(-2**31 <= value < 2**31, 'head arithmetic overflow'); stack.append(value)
            elif k in (0x2b, 0x38): pc = v
            elif k == 0x39:
                if pop_int() == 0: pc = v
            elif k in (0x30, 0x3b, 0x3d):
                y, x = pop_int(), pop_int()
                if (x == y if k == 0x3b else x > y): pc = v
            elif k == 0x45:
                index = pop_int()
                if 0 <= index < len(v): pc = v[index]
        _need(pc == end and not stack and set(flags) == {1, 2, 3, 5}, 'incomplete head outputs')
        for key, argument in (('fullHairHeads', 1), ('hatHairHeads', 2), ('drawsBackWithoutHeadgear', 5)):
            if flags[argument]:
                _need(head >= 0, 'negative consumer head ID'); output[key].append(head)
    return output


def _back_hair(instructions, start, hair_field):
    """Recognize the complete bounded range/exclusion tail, preserving source IDs."""
    sequence = [i for i in instructions if i.offset >= start]
    at = 0
    def take(opcode, operand=None):
        nonlocal at
        _need(at < len(sequence), 'truncated back-hair shape')
        value = sequence[at]; at += 1
        _need(value.opcode == opcode and (operand is None or value.operand == operand), 'back-hair opcode/operand mismatch')
        return value
    def constant():
        nonlocal at
        _need(at < len(sequence), 'missing back-hair constant')
        value = _constant(sequence[at]); at += 1
        _need(0 <= value <= 4095, 'back-hair constant bound'); return value
    take(2); take(0x7b, hair_field); take(0x0a); take(0x0e, 4); take(6)
    low = constant(); false_target = take(0x31).operand
    ranges = []
    for _ in range(3):
        take(6); a = constant(); skip = take(0x32).operand
        take(6); b = constant(); take(0x31, false_target)
        _need(at < len(sequence) and sequence[at].offset == skip, 'back-hair range branch mismatch')
        ranges.append([a, b])
    excluded = []
    for _ in range(4):
        take(6); excluded.append(constant()); take(0x2e, false_target)
    take(6); high = constant(); take(0xfe04); store_target = take(0x2b).operand
    _need(take(0x16).offset == false_target and take(0x52).offset == store_target, 'back-hair false/store target mismatch')
    included = []; true_targets = []
    for _ in range(4):
        take(6); included.append(constant()); true_targets.append(take(0x2e).operand)
    take(6); included.append(constant()); return_target = take(0x33).operand
    yes = take(0x0e, 4).offset; take(0x17); take(0x52)
    _need(all(target == yes for target in true_targets) and take(0x2a).offset == return_target
          and at == len(sequence), 'back-hair terminal targets mismatch')
    previous = low
    for a, b in ranges:
        _need(previous < a <= b < high, 'invalid back-hair ranges'); previous = b
    _need(low < high and len(set(excluded)) == len(excluded) and all(low < n < high for n in excluded)
          and len(set(included)) == len(included), 'invalid back-hair ID sets')
    return {'lowerExclusive': low, 'upperExclusive': high, 'excludedRanges': ranges,
            'excludedIds': excluded, 'includedIds': included}


def _clothes(program, body, destination_field):
    seq = body['instructions'][:7]
    _need(len(seq) == 7 and [x.opcode for x in seq] == [2, seq[1].opcode, 0x8d, 0x25, 0xd0, 0x28, 0x7d]
          and seq[0].offset == 0 and seq[6].operand == destination_field, 'clothes constructor prefix mismatch')
    count = _constant(seq[1]); _need(0 < count <= 256, 'clothes count bound')
    program.external_type(seq[2].operand, 'System.Int32')
    # Only this core intrinsic's data layout is recognized; it is never called.
    callrow, _ = program.row(10, seq[5].operand & 0xffffff)
    sig, _ = program.meta.blob(callrow[2])
    _need(sig[:3] == b'\x00\x02\x01', 'InitializeArray signature prefix')
    position = 3
    for kind, name in ((0x12, 'System.Array'), (0x11, 'System.RuntimeFieldHandle')):
        _need(position < len(sig) and sig[position] == kind, 'InitializeArray parameter shape')
        coded, position = _compressed(sig, position + 1)
        _need(coded & 3 == 1, 'InitializeArray external parameter type')
        program.external_type(0x01000000 | coded >> 2, name)
    _need(position == len(sig), 'InitializeArray signature suffix')
    program.member(seq[5].operand, 'System.Runtime.CompilerServices.RuntimeHelpers', 'InitializeArray', sig)
    token = seq[4].operand
    _need(token >> 24 == 4, 'clothes expected local FieldRVA')
    row, metadata_offset = program.row(4, token & 0xffffff)
    signature, _ = program.meta.blob(row[2])
    _need(row[0] & 0x110 == 0x110 and signature[:2] == b'\x06\x11', 'clothes backing field shape')
    coded, end = _compressed(signature, 2)
    _need(end == len(signature) and coded & 3 == 0, 'clothes backing value type')
    sizes = [r[1] for r in program.table(15) if r[2] == coded >> 2]
    _need(sizes == [count * 4], 'clothes backing class size mismatch')
    rvas = [r[0] for r in program.table(29) if r[1] == token & 0xffffff]
    _need(len(rvas) == 1, 'missing/ambiguous clothes FieldRVA')
    offset = program.meta.rva(rvas[0], count * 4)
    raw = program.meta.reader.take(offset, count * 4)
    values = list(struct.unpack('<' + 'i' * count, raw))
    _need(all(0 <= n <= 255 for n in values) and len(set(values)) == len(values), 'invalid clothes ordering')
    return values, {'backingFieldToken': f'0x{token:08x}', 'metadataOffset': metadata_offset,
                    'sourceOffset': offset, 'bytes': len(raw), 'bytesSha256': sha256(raw)}


def _current_constants(program, release_field, version_field, display_field):
    wanted = {release_field, version_field, display_field}; values = {}; evidence = []
    for row in program.table(11):
        kind, parent, index = row
        if parent & 3 or (0x04000000 | parent >> 2) not in wanted: continue
        token = 0x04000000 | parent >> 2
        _need(token not in values, 'duplicate current-version Constant')
        raw, offset = program.meta.blob(index)
        if token == release_field:
            _need(kind == 8 and len(raw) == 4, 'release Constant type')
            value = struct.unpack('<i', raw)[0]; _need(38 <= value <= 326, 'release consumer bound')
        else:
            _need(kind == 14 and 0 < len(raw) <= 128, 'version Constant type')
            value = raw.decode('utf-16-le')
        values[token] = value
        evidence.append({'fieldToken': f'0x{token:08x}', 'blobOffset': offset, 'bytesSha256': sha256(raw)})
    _need(set(values) == wanted and values[display_field] == 'v' + values[version_field], 'incomplete/inconsistent version Constants')
    return {'saveVersion': values[release_field], 'gameVersion': values[version_field]}, evidence


def extract_player_static_facts(pe_bytes, checkpoint=lambda: None):
    """Inspect pinned operator-supplied PE bytes; return partial facts and receipt."""
    _need(type(pe_bytes) is bytes and 0 < len(pe_bytes) <= 64 * 1024 * 1024, 'PE byte bound')
    _need(sha256(pe_bytes) == SOURCE_SHA256, 'PE source pin mismatch')
    try:
        p = _Program(pe_bytes, _Budget(ItemTextureAliasLimits(steps=2_000_000, wall_seconds=30), checkpoint))
        bodies = {key: _method(p, key) for key in _METHODS}
        fields = [
            _field(p, 0x040006cd, 'Terraria.Player', 'head', '0608'),
            _field(p, 0x04000891, 'Terraria.Player', 'hair', '0608'),
            _field(p, 0x0400551e, 'Terraria.GameContent.UI.States.UICharacterCreation', '_validClothStyles', '061d08'),
            _field(p, 0x04002497, 'Terraria.ID.BuffID', 'Count', '0608', static=True),
            _field(p, 0x04007192, 'Terraria.ID.ArmorIDs+Face', 'Count', '0604', static=True),
            _field(p, 0x04000a2a, 'Terraria.Main', 'curRelease', '0608', static=True, literal=True),
            _field(p, 0x04000a2b, 'Terraria.Main', 'assemblyVersionNumber', '060e', static=True, literal=True),
            _field(p, 0x04000a29, 'Terraria.Main', 'versionStringBecauseTheyreTheSame', '060e', static=True, literal=True),
        ]
        hair = _hair_head_sets(bodies['hair']['instructions'], 988, 0x040006cd, p.budget.check)
        hair['backHairStyle'] = _back_hair(bodies['hair']['instructions'], 1034, 0x04000891)
        clothes, clothes_proof = _clothes(p, bodies['clothes'], 0x0400551e)
        _need(clothes_proof['backingFieldToken'] == '0x04006216'
              and clothes_proof['bytesSha256'] == 'ce9276d2ac08edb0060baecb9d6d7f02cee40e19b8854e488196dc06eb5307f9', 'clothes data pin mismatch')
        current, constants_proof = _current_constants(p, 0x04000a2a, 0x04000a2b, 0x04000a29)
        result = {'hairRules': hair, 'clothes': clothes, 'currentVersion': current,
                  'buffCount': _count_prefix(bodies['buffCount']['instructions'], 0x04002497),
                  'faceCount': _count_prefix(bodies['faceCount']['instructions'], 0x04007192)}
        receipt = {'schemaVersion': 1, 'status': 'BOUNDED_SOURCE_SHAPES', 'sourceSha256': SOURCE_SHA256,
                   'resultSha256': sha256(canonical_json(result)), 'methods': [b['evidence'] for b in bodies.values()],
                   'fields': fields, 'clothesRva': clothes_proof, 'currentVersionConstants': constants_proof,
                   'headDomain': [-1, 4095], 'executedInput': False, 'initializationVerified': False,
                   'sourceSemanticsVerified': False, 'complete': False, 'publishable': False,
                   'scope': 'named source slices only; not full Player state or global mutation closure'}
        return result, receipt
    except _CheckpointCancelled as exc:
        raise exc.original
    except PipelineError:
        raise
    except (ILUnsupported, UnicodeError, ValueError, IndexError, struct.error) as exc:
        raise PipelineError('Unsupported or malformed pinned player source shape') from exc
