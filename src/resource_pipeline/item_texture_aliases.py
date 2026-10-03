"""Bounded, conditional item-image alias evidence from PE bytes, never CLR code.

This recognizes closed compiler shapes at the alias store and at the first
consumer loop. It does not certify whole type initializers, runtime success,
subsequent mutation, client equivalence, image decoding or publication rights.
The module is deliberately independent of the real producer.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
import struct
import time

from .security import PipelineError
from .static_il import EvidenceSizeLimit, ILUnsupported, StaticILLimits, _compressed, _type, decode_il, json_evidence_size


@dataclass(frozen=True)
class ItemTextureAliasLimits:
    input_bytes: int = 128 * 1024 * 1024
    inventory_bytes: int = 32 * 1024 * 1024
    inventory_line_bytes: int = 16 * 1024
    inventory_rows: int = 100_000
    item_count: int = 100_000
    pair_count: int = 25_000
    method_bytes: int = 1024 * 1024
    total_method_bytes: int = 4 * 1024 * 1024
    instructions: int = 500_000
    steps: int = 2_000_000
    evidence_bytes: int = 32 * 1024 * 1024
    wall_seconds: float = 30

    def __post_init__(self):
        for name, value in vars(self).items():
            if name == 'wall_seconds':
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 120:
                    raise ValueError('Invalid alias wall-time budget')
            elif type(value) is not int or value <= 0:
                raise ValueError('Invalid alias integer budget')
        if self.evidence_bytes < 4096:
            raise ValueError('Alias evidence needs a diagnostic envelope')


def _hex(token):
    return f'0x{token:08x}'


def _compressed_bytes(value):
    if value < 0x80:
        return bytes((value,))
    if value < 0x4000:
        return bytes((0x80 | value >> 8, value & 255))
    if value < 0x20000000:
        return bytes((0xc0 | value >> 24, value >> 16 & 255, value >> 8 & 255, value & 255))
    raise ILUnsupported('ALIAS_SIGNATURE_INTEGER_LIMIT')


def _constant(ins):
    if 0x15 <= ins.opcode <= 0x1e:
        return ins.opcode - 0x16
    if ins.opcode in (0x1f, 0x20):
        return ins.operand
    raise ILUnsupported('ALIAS_EXPECTED_INTEGER_CONSTANT', offset=ins.offset)


def _require(condition, code):
    if not condition:
        raise ILUnsupported(code)


class _CheckpointCancelled(Exception):
    def __init__(self, original):
        self.original = original


class _Budget:
    def __init__(self, limits, checkpoint):
        self.limits = limits
        self.external = checkpoint or (lambda: None)
        self.deadline = time.monotonic() + limits.wall_seconds
        self.steps = self.instructions = self.method_bytes = 0

    def check(self):
        try:
            self.external()
        except Exception as exc:
            # Distinguish caller cancellation even when it uses PipelineError,
            # ValueError or another exception also used by malformed input.
            raise _CheckpointCancelled(exc) from exc
        if self.steps >= self.limits.steps:
            raise ILUnsupported('ALIAS_STEP_LIMIT')
        self.steps += 1
        if time.monotonic() >= self.deadline:
            raise ILUnsupported('ALIAS_TIME_LIMIT')

    def instruction(self):
        if self.instructions >= self.limits.instructions:
            raise ILUnsupported('ALIAS_INSTRUCTION_LIMIT')
        self.instructions += 1


class _Program:
    def __init__(self, data, budget):
        # Delayed import avoids server_semantics -> item_baseline/static_il cycles.
        from .server_semantics import SemanticLimits, _Metadata, _types
        self.budget = budget
        self.meta = _Metadata(data, SemanticLimits(input_bytes=budget.limits.input_bytes), budget.check)
        self.types = _types(self.meta)
        self.by_name = {}
        self.methods = {}
        self.fields = {}
        self.evidence = []
        self.tables = {}
        for rid, typedef in self.types.items():
            self.budget.check()
            self.by_name.setdefault(typedef['fullName'], []).append(rid)

    def table(self, number):
        if number not in self.tables:
            self.tables[number] = [self.row(number, rid)[0] for rid in range(1, self.meta.rows[number] + 1)]
        return self.tables[number]

    def row(self, table, rid):
        self.budget.check()
        return self.meta.row(table, rid)

    def owner(self, name):
        matches = self.by_name.get(name, [])
        _require(len(matches) == 1, 'ALIAS_OWNER_MISSING_OR_AMBIGUOUS')
        rid = matches[0]
        row, _ = self.row(2, rid)
        _require(not row[0] & 0x20 and not any(r[2] == rid << 1 for r in self.table(42)),
                 'ALIAS_UNSUPPORTED_OWNER')
        return rid, self.types[rid]

    def field(self, owner, name, signature=None):
        _, typedef = self.owner(owner)
        matches = []
        for rid in range(typedef['firstField'], typedef['lastField']):
            row, offset = self.row(4, rid)
            if self.meta.string(row[1]) == name:
                matches.append((0x04000000 | rid, row, offset))
        _require(len(matches) == 1, 'ALIAS_FIELD_MISSING_OR_AMBIGUOUS')
        token, row, offset = matches[0]
        sig, sig_offset = self.meta.blob(row[2])
        _require(bool(row[0] & 0x10) and not row[0] & (0x40 | 0x100 | 0x2000), 'ALIAS_INVALID_STATIC_FIELD')
        if signature is not None:
            _require(sig == signature, 'ALIAS_FIELD_SIGNATURE_MISMATCH')
        self.fields[token] = sig
        self.evidence.append({'fieldToken': _hex(token), 'owner': owner, 'name': name,
                              'metadataOffset': offset, 'signatureOffset': sig_offset,
                              'signatureSha256': hashlib.sha256(sig).hexdigest()})
        return token

    def method(self, owner, name, signature=None, *, allow_eh=False):
        _, typedef = self.owner(owner)
        found = []
        for rid in range(typedef['firstMethod'], typedef['lastMethod']):
            row, _ = self.row(6, rid)
            if self.meta.string(row[3]) != name:
                continue
            sig, _ = self.meta.blob(row[4])
            if signature is None or sig == signature:
                found.append(0x06000000 | rid)
        _require(len(found) == 1, 'ALIAS_METHOD_MISSING_OR_AMBIGUOUS')
        return self.body(found[0], allow_eh=allow_eh)

    def body(self, token, *, allow_eh=False):
        if token in self.methods:
            result = self.methods[token]
            _require(allow_eh or not result['eh'], 'ALIAS_UNSUPPORTED_EXCEPTION_REGIONS')
            return result
        _require(token >> 24 == 6, 'ALIAS_EXPECTED_LOCAL_METHOD')
        row, metadata_offset = self.row(6, token & 0xffffff)
        _require(row[0] and not row[1] and not row[2] & (0x40 | 0x400 | 0x2000), 'ALIAS_INVALID_METHOD_FLAGS')
        sig, sig_offset = self.meta.blob(row[4])
        _require(bool(sig), 'ALIAS_EMPTY_METHOD_SIGNATURE')
        generic_count = _compressed(sig, 1)[0] if sig[0] & 0x10 else 0
        generic_rows = [(rid, r) for rid, r in enumerate(self.table(42), 1)
                        if r[2] == ((token & 0xffffff) << 1 | 1)]
        _require(generic_count <= 16 and len(generic_rows) == generic_count
                 and sorted(r[0] for _, r in generic_rows) == list(range(generic_count))
                 and all(r[1] in (0, 4) for _, r in generic_rows), 'ALIAS_GENERIC_METHOD_MISMATCH')
        generic_rids = {rid for rid, _ in generic_rows}
        _require(not any(r[0] in generic_rids for r in self.table(44)), 'ALIAS_GENERIC_CONSTRAINT_UNSUPPORTED')
        _require(bool(row[2] & 0x10) != bool(sig[0] & 0x20), 'ALIAS_METHOD_THIS_MISMATCH')
        self.budget.check()
        start = self.meta.rva(row[0], 1)
        first = self.meta.reader.uint(start, 1)
        local = 0
        if first & 3 == 2:
            header, size, flags = 1, first >> 2, 2
        else:
            flags = self.meta.reader.uint(start, 2)
            _require(first & 3 == 3 and flags >> 12 == 3 and not flags & ~0x301b,
                     'ALIAS_INVALID_METHOD_HEADER')
            header, size = 12, self.meta.reader.uint(start + 4, 4)
            local = self.meta.reader.uint(start + 8, 4)
            _require(self.meta.reader.uint(start + 2, 2) <= 256, 'ALIAS_STACK_LIMIT')
        _require(allow_eh or not flags & 8, 'ALIAS_UNSUPPORTED_EXCEPTION_REGIONS')
        _require(size <= self.budget.limits.method_bytes, 'ALIAS_METHOD_BYTE_LIMIT')
        _require(size <= self.budget.limits.total_method_bytes - self.budget.method_bytes,
                 'ALIAS_TOTAL_METHOD_BYTE_LIMIT')
        self.budget.method_bytes += size
        offset = self.meta.rva(row[0] + header, size)
        code = self.meta.reader.take(offset, size)
        instructions = list(decode_il(code, StaticILLimits(method_bytes=self.budget.limits.method_bytes),
                                      self.budget.check, instruction_budget=self.budget.instruction).values())
        local_sig = b''
        if local:
            _require(local >> 24 == 17, 'ALIAS_INVALID_LOCAL_TOKEN')
            lr, _ = self.row(17, local & 0xffffff)
            local_sig, _ = self.meta.blob(lr[0])
            _require(len(local_sig) <= 4096 and local_sig[:1] == b'\x07', 'ALIAS_LOCAL_SIGNATURE_LIMIT')
            local_count, local_pos = _compressed(local_sig, 1)
            _require(local_count <= 256, 'ALIAS_LOCAL_COUNT_LIMIT')
            for _ in range(local_count):
                self.budget.check()
                kind, local_pos = _type(local_sig, local_pos)
                _require(kind != 'void', 'ALIAS_INVALID_LOCAL_TYPE')
            _require(local_pos == len(local_sig), 'ALIAS_LOCAL_SIGNATURE_TRAILING_BYTES')
        evidence = {'methodToken': _hex(token), 'metadataOffset': metadata_offset,
                    'bodyOffset': start, 'codeOffset': offset, 'codeBytes': size,
                    'ilSha256': hashlib.sha256(code).hexdigest(), 'signatureOffset': sig_offset,
                    'signatureSha256': hashlib.sha256(sig).hexdigest(), 'localSignatureSha256': hashlib.sha256(local_sig).hexdigest()}
        result = dict(token=token, signature=sig, instructions=instructions, local=local_sig,
                      flags=flags, eh=bool(flags & 8), evidence=evidence, rva=row[0], size=size, header=header)
        self.methods[token] = result
        self.evidence.append(evidence)
        return result

    def external_type(self, token, full_name, *, core=True):
        _require(token >> 24 == 1, 'ALIAS_EXPECTED_EXTERNAL_TYPE')
        row, _ = self.row(1, token & 0xffffff)
        _require(self.meta.string(row[2]) + '.' + self.meta.string(row[1]) == full_name,
                 'ALIAS_EXTERNAL_TYPE_MISMATCH')
        _require(row[0] & 3 == 2 and row[0] >> 2, 'ALIAS_EXTERNAL_SCOPE_MISMATCH')
        assembly, _ = self.row(35, row[0] >> 2)
        name, culture = self.meta.string(assembly[6]), self.meta.string(assembly[7])
        key, _ = self.meta.blob(assembly[5])
        if core:
            keys = {'mscorlib': bytes.fromhex('b77a5c561934e089'),
                    'System.Private.CoreLib': bytes.fromhex('7cec85d7bea7798e'),
                    'System.Runtime': bytes.fromhex('b03f5f7f11d50a3a')}
            _require(not assembly[4] and not culture and key == keys.get(name), 'ALIAS_CORE_IDENTITY_MISMATCH')
        return row

    def member(self, token, owner, name, signature):
        _require(token >> 24 == 10, 'ALIAS_EXPECTED_MEMBERREF')
        row, _ = self.row(10, token & 0xffffff)
        _require(row[0] & 7 == 1, 'ALIAS_MEMBER_OWNER_MISMATCH')
        self.external_type(0x01000000 | row[0] >> 3, owner)
        sig, _ = self.meta.blob(row[2])
        _require(self.meta.string(row[1]) == name and sig == signature, 'ALIAS_MEMBER_SIGNATURE_MISMATCH')

    def type_signature(self, token):
        if token >> 24 == 27:
            row, _ = self.row(27, token & 0xffffff)
            return self.meta.blob(row[0])[0]
        tag = {1: 1, 2: 0}.get(token >> 24)
        _require(tag is not None, 'ALIAS_TYPE_TOKEN_MISMATCH')
        return b'\x12' + _compressed_bytes((token & 0xffffff) << 2 | tag)

    def user_string(self, token):
        _require(token >> 24 == 0x70 and '#US' in self.meta.streams, 'ALIAS_INVALID_USER_STRING')
        stream = self.meta.streams['#US']
        at = stream.start + (token & 0xffffff)
        # Read at most the four-byte compressed header, then the bounded string.
        prefix = stream.take(at, min(4, stream.end - at))
        size, n = _compressed(prefix, 0)
        _require(1 <= size <= 4097 and size % 2 == 1, 'ALIAS_USER_STRING_LIMIT')
        raw = stream.take(at + n, size)
        _require(raw[-1] in (0, 1), 'ALIAS_INVALID_USER_STRING')
        return raw[:-1].decode('utf-16le', errors='strict')


def _shape(instructions, pattern, *, prefix=False):
    """A whole instruction pattern, with capture tokens and indexed branches."""
    _require(len(instructions) >= len(pattern) if prefix else len(instructions) == len(pattern),
             'ALIAS_UNSUPPORTED_IL_SHAPE')
    captures = {}
    for ins, expected in zip(instructions, pattern):
        opcode, operand = expected if isinstance(expected, tuple) else (expected, None)
        _require(ins.opcode == opcode, 'ALIAS_UNSUPPORTED_IL_SHAPE')
        if isinstance(operand, str):
            if operand.startswith('@'):
                target = instructions[int(operand[1:])].offset
                _require(ins.operand == target, 'ALIAS_UNSUPPORTED_CONTROL_FLOW')
            else:
                _require(operand not in captures or captures[operand] == ins.operand, 'ALIAS_TOKEN_BINDING_MISMATCH')
                captures[operand] = ins.operand
        else:
            _require(ins.operand == operand, 'ALIAS_UNSUPPORTED_IL_OPERAND')
    return captures


def _linear_store(method, field, before):
    ins = method['instructions']
    _require(ins and ins[-1].opcode == 0x2a and sum(i.opcode == 0x2a for i in ins) == 1,
             'ALIAS_UNSUPPORTED_INITIALIZER_RETURN')
    _require(not any(0x2b <= i.opcode <= 0x45 or i.opcode in (0x27, 0x29, 0xdd, 0xde, 0xfe14) for i in ins),
             'ALIAS_UNSUPPORTED_INITIALIZER_CONTROL_FLOW')
    stores = [n for n, i in enumerate(ins) if i.opcode == 0x80 and i.operand == field]
    _require(len(stores) == 1 and stores[0] >= before, 'ALIAS_INITIALIZER_STORE_MISSING_OR_AMBIGUOUS')
    _require(not any(i.opcode == 0x7f and i.operand == field for i in ins), 'ALIAS_INITIALIZER_FIELD_ADDRESS_ESCAPE')
    return ins[stores[0] - before:stores[0] + 1]


def _factory(p, token):
    method = p.method('Terraria.ID.SetFactory', 'CreateIntSet', b'\x20\x02\x1d\x08\x08\x1d\x08')
    _require(method['token'] == token and method['local'] == b'\x07\x03\x1d\x08\x08\x08',
             'ALIAS_FACTORY_SIGNATURE_MISMATCH')
    # Reject odd pair arrays; fill every result slot with the default; apply
    # [key,value] pairs in source order; return exactly that result array.
    pat = [4,0x8e,0x69,0x18,0x5d,(0x2c,'@9'),(0x72,'error'),(0x73,'throwctor'),0x7a,
           2,(0x28,'buffer'),0x0a,0x16,0x0b,(0x2b,'@23'),
           6,7,3,0x9e,7,0x17,0x58,0x0b,7,6,0x8e,0x69,(0x32,'@15'),
           0x16,0x0c,(0x2b,'@45'),6,4,8,0x94,4,8,0x17,0x58,0x94,0x9e,8,0x18,0x58,0x0c,
           8,4,0x8e,0x69,(0x32,'@31'),6,0x2a]
    cap = _shape(method['instructions'], pat)
    p.user_string(cap['error'])
    p.member(cap['throwctor'], 'System.Exception', '.ctor', b'\x20\x01\x01\x0e')
    getter = p.method('Terraria.ID.SetFactory', 'GetIntBuffer', b'\x20\x00\x1d\x08', allow_eh=True)
    _require(cap['buffer'] == getter['token'], 'ALIAS_FACTORY_BUFFER_BINDING_MISMATCH')
    # The closed getter may either allocate an int array or dequeue one. The
    # theorem needs normal return and sufficient length, not cache emptiness.
    if not getter['eh']:
        g = _shape(getter['instructions'], [2,(0x7b,'size'),(0x8d,'int'),0x2a])
    else:
        _require(getter['local'] == b'\x07\x03\x1c\x02\x1d\x08', 'ALIAS_GETTER_LOCALS_MISMATCH')
        g = _shape(getter['instructions'], [2,(0x7b,'lock'),0x0a,0x16,0x0b,6,(0x12,1),(0x28,'enter'),
             2,(0x7b,'queue'),(0x6f,'count'),(0x2d,'@17'),2,(0x7b,'size'),(0x8d,'int'),0x0c,(0xde,'@27'),
             2,(0x7b,'queue'),(0x6f,'dequeue'),0x0c,(0xde,'@27'),7,(0x2c,'@26'),6,(0x28,'exit'),0xdc,8,0x2a])
        p.member(g['enter'], 'System.Threading.Monitor', 'Enter', b'\x00\x02\x01\x1c\x10\x02')
        p.member(g['exit'], 'System.Threading.Monitor', 'Exit', b'\x00\x01\x01\x1c')
        _queue_contract(p, g)
        _finally_contract(p, getter)
    p.external_type(g['int'], 'System.Int32')
    _instance_field(p, g['size'], '_size', b'\x06\x08')
    return {'factoryMethodToken': _hex(token), 'bufferMethodToken': _hex(getter['token']),
            'semantics': 'fill returned int buffer with default; apply key/value pairs in source order',
            'bufferContract': 'normal return; buffer length must cover the consumer domain; pooled buffer contents are overwritten'}


def _instance_field(p, token, name, signature):
    _, owner = p.owner('Terraria.ID.SetFactory')
    _require(token >> 24 == 4 and owner['firstField'] <= token & 0xffffff < owner['lastField'], 'ALIAS_FACTORY_FIELD_OWNER')
    row, _ = p.row(4, token & 0xffffff)
    _require(not row[0] & (0x10 | 0x40 | 0x100 | 0x2000) and p.meta.string(row[1]) == name
             and p.meta.blob(row[2])[0] == signature, 'ALIAS_FACTORY_FIELD_SIGNATURE')


def _queue_contract(p, g):
    _instance_field(p, g['lock'], '_queueLock', b'\x06\x1c')
    rows = []
    for token, name, sig in ((g['count'], 'get_Count', b'\x20\x00\x08'),
                             (g['dequeue'], 'Dequeue', b'\x20\x00\x13\x00')):
        _require(token >> 24 == 10, 'ALIAS_QUEUE_MEMBER_MISMATCH')
        row, _ = p.row(10, token & 0xffffff)
        _require(row[0] & 7 == 4 and p.meta.string(row[1]) == name and p.meta.blob(row[2])[0] == sig,
                 'ALIAS_QUEUE_MEMBER_MISMATCH')
        rows.append(row)
    _require(rows[0][0] == rows[1][0], 'ALIAS_QUEUE_OWNER_MISMATCH')
    ts = p.type_signature(0x1b000000 | rows[0][0] >> 3)
    _require(ts[:2] == b'\x15\x12', 'ALIAS_QUEUE_TYPE_MISMATCH')
    coded, pos = _compressed(ts, 2)
    _require(coded & 3 == 1 and ts[pos:] == b'\x01\x1d\x08', 'ALIAS_QUEUE_TYPE_MISMATCH')
    # Queue<T> lives in System.dll on this supported profile, not mscorlib.
    row = p.external_type(0x01000000 | coded >> 2, 'System.Collections.Generic.Queue`1', core=False)
    assembly, _ = p.row(35, row[0] >> 2)
    _require(p.meta.string(assembly[6]) in ('System', 'mscorlib') and not assembly[4]
             and not p.meta.string(assembly[7]) and p.meta.blob(assembly[5])[0] == bytes.fromhex('b77a5c561934e089'),
             'ALIAS_QUEUE_ASSEMBLY_MISMATCH')
    _instance_field(p, g['queue'], '_intBufferCache', b'\x06' + ts)


def _finally_contract(p, method):
    # One small EH section with one finally clause, no chained sections.
    pos = (method['rva'] + method['header'] + method['size'] + 3) & ~3
    offset = p.meta.rva(pos, 16)
    raw = p.meta.reader.take(offset, 16)
    _require(raw[:4] == b'\x01\x10\x00\x00', 'ALIAS_UNSUPPORTED_GETTER_EH')
    flags, start, length, handler, hlength, extra = struct.unpack('<HHBHB I'.replace(' ', ''), raw[4:])
    ins = method['instructions']
    _require((flags, start, length, handler, hlength, extra) ==
             (2, ins[5].offset, ins[22].offset - ins[5].offset, ins[22].offset,
              ins[27].offset - ins[22].offset, 0), 'ALIAS_UNSUPPORTED_GETTER_EH')
    method['evidence']['exceptionSectionSha256'] = hashlib.sha256(raw).hexdigest()


def _consumer(p, alias_field, item_field):
    method = p.method('Terraria.Initializers.AssetInitializer', 'LoadTextures')
    sig = method['signature']
    _require(sig[:4] == b'\x00\x01\x01\x11', 'ALIAS_CONSUMER_SIGNATURE_MISMATCH')
    coded, end = _compressed(sig, 4)
    _require(end == len(sig) and coded & 3 == 1, 'ALIAS_CONSUMER_SIGNATURE_MISMATCH')
    p.external_type(0x01000000 | coded >> 2, 'ReLogic.Content.AssetRequestMode', core=False)
    local = method['local']
    _require(local[:1] == b'\x07', 'ALIAS_CONSUMER_LOCALS_MISMATCH')
    count, pos = _compressed(local, 1)
    _require(count >= 2 and local[pos:pos + 2] == b'\x08\x08', 'ALIAS_CONSUMER_LOCALS_MISMATCH')
    pat = [0x16,0x0a,(0x2b,'@30'),(0x7e,'aliases'),6,0x94,0x0b,7,0x15,(0x2e,'@17'),
           (0x7e,'items'),6,(0x7e,'items'),7,0x9a,0xa2,(0x2b,'@26'),
           (0x7e,'items'),6,(0x72,'prefix'),6,(0x8c,'int'),(0x28,'concat'),0x16,(0x28,'load'),0xa2,
           6,0x17,0x58,0x0a,6,(0x7e,'items'),0x8e,0x69,(0x32,'@3')]
    cap = _shape(method['instructions'], pat, prefix=True)
    _require(cap['aliases'] == alias_field and cap['items'] == item_field, 'ALIAS_CONSUMER_FIELD_MISMATCH')
    p.external_type(cap['int'], 'System.Int32')
    p.member(cap['concat'], 'System.String', 'Concat', b'\x00\x02\x0e\x1c\x1c')
    _require(p.user_string(cap['prefix']) == 'Images/Item_', 'ALIAS_CONSUMER_PREFIX_MISMATCH')
    end_offset = method['instructions'][len(pat) - 1].next_offset
    # The remaining method may not jump back into, address or mutate the item
    # array / alias field directly. Other calls are outside this slice's claim.
    for ins in method['instructions'][len(pat):]:
        p.budget.check()
        targets = ins.operand if ins.opcode == 0x45 else (ins.operand,) if 0x2b <= ins.opcode <= 0x44 or ins.opcode in (0xdd,0xde) else ()
        _require(all(t >= end_offset for t in targets), 'ALIAS_CONSUMER_REENTRY')
        _require(not (ins.opcode in (0x7e,0x7f,0x80) and ins.operand in (alias_field,item_field)),
                 'ALIAS_CONSUMER_LATER_ITEM_ACCESS')
    _asset_loader(p, cap['load'], p.fields[item_field][2:], sig[3:])
    return {'methodToken': _hex(method['token']), 'loopStartIl': 0, 'loopEndIlExclusive': end_offset,
            'iteration': 'ascending integer indexes, zero inclusive to TextureAssets.Item.Length exclusive',
            'aliasSemantics': 'copy the currently stored prior-item asset; no recursive graph lookup',
            'directPath': 'Images/Item_<id>.xnb', 'assetRequestMode': 0}


def _asset_loader(p, spec_token, asset_signature, mode_signature):
    _require(spec_token >> 24 == 43, 'ALIAS_EXPECTED_LOADER_METHODSPEC')
    row, _ = p.row(43, spec_token & 0xffffff)
    spec, _ = p.meta.blob(row[1])
    _require(row[0] & 1 == 0 and spec[:2] == b'\x0a\x01', 'ALIAS_LOADER_METHODSPEC_MISMATCH')
    texture = spec[2:]
    _require(texture[:1] == b'\x12', 'ALIAS_TEXTURE_SIGNATURE_MISMATCH')
    coded, end = _compressed(texture, 1)
    _require(end == len(texture) and coded & 3 == 1, 'ALIAS_TEXTURE_SIGNATURE_MISMATCH')
    p.external_type(0x01000000 | coded >> 2, 'Microsoft.Xna.Framework.Graphics.Texture2D', core=False)
    _require(asset_signature[:2] == b'\x15\x12', 'ALIAS_ASSET_SIGNATURE_MISMATCH')
    asset_coded, pos = _compressed(asset_signature, 2)
    _require(asset_coded & 3 == 1 and asset_signature[pos:] == b'\x01' + texture, 'ALIAS_ASSET_SIGNATURE_MISMATCH')
    p.external_type(0x01000000 | asset_coded >> 2, 'ReLogic.Content.Asset`1', core=False)
    returned = asset_signature[:pos] + b'\x01\x1e\x00'
    expected = b'\x10\x01\x02' + returned + b'\x0e' + mode_signature
    loader = p.method('Terraria.Initializers.AssetInitializer', 'LoadAsset', expected)
    _require(loader['token'] == 0x06000000 | row[0] >> 1, 'ALIAS_LOADER_BINDING_MISMATCH')
    cap = _shape(loader['instructions'], [(0x7e,'repository'),2,3,(0x6f,'request'),0x2a])
    # This confirms delegation of exactly the name and mode. External asset
    # manager behavior is a stated boundary, not simulated or executed.
    field = p.field('Terraria.Main', 'Assets')
    _require(cap['repository'] == field, 'ALIAS_REPOSITORY_BINDING_MISMATCH')
    _require(cap['request'] >> 24 == 43, 'ALIAS_REQUEST_METHODSPEC_MISMATCH')
    request, _ = p.row(43, cap['request'] & 0xffffff)
    _require(request[0] & 1 == 1 and p.meta.blob(request[1])[0] == b'\x0a\x01\x1e\x00', 'ALIAS_REQUEST_METHODSPEC_MISMATCH')
    member, _ = p.row(10, request[0] >> 1)
    _require(member[0] & 7 == 1 and p.meta.string(member[1]) == 'Request', 'ALIAS_REQUEST_MEMBER_MISMATCH')
    repository_type = 0x01000000 | member[0] >> 3
    p.external_type(repository_type, 'ReLogic.Content.IAssetRepository', core=False)
    _require(p.fields[field] == b'\x06' + p.type_signature(repository_type), 'ALIAS_REPOSITORY_SIGNATURE_MISMATCH')
    _require(p.meta.blob(member[2])[0] == b'\x30\x01\x02' + returned + b'\x0e' + mode_signature,
             'ALIAS_REQUEST_SIGNATURE_MISMATCH')


def _rva_pairs(p, token, element_count):
    _require(0 < element_count <= p.budget.limits.pair_count * 2 and element_count % 2 == 0,
             'ALIAS_PAIR_COUNT_LIMIT_OR_ODD')
    _require(token >> 24 == 4, 'ALIAS_RVA_FIELD_TOKEN_MISMATCH')
    rid = token & 0xffffff
    row, metadata_offset = p.row(4, rid)
    sig, _ = p.meta.blob(row[2])
    _require(row[0] & 0x110 == 0x110 and not row[0] & (0x40 | 0x2000) and sig[:2] == b'\x06\x11',
             'ALIAS_RVA_FIELD_SIGNATURE_MISMATCH')
    coded, end = _compressed(sig, 2)
    _require(end == len(sig) and coded & 3 == 0 and coded >> 2 in p.types, 'ALIAS_RVA_LAYOUT_MISMATCH')
    typedef, _ = p.row(2, coded >> 2)
    _require(typedef[3] & 3 == 1, 'ALIAS_RVA_BASE_MISMATCH')
    p.external_type(0x01000000 | typedef[3] >> 2, 'System.ValueType')
    owner = p.types[coded >> 2]
    _require(owner['firstField'] == owner['lastField'] and owner['firstMethod'] == owner['lastMethod']
             and not any(r[2] == coded >> 1 for r in p.table(42)),
             'ALIAS_RVA_LAYOUT_MEMBERS')
    _require(typedef[0] & 0x18 == 0x10 and typedef[0] & 0x100 and not typedef[0] & 0xa0,
             'ALIAS_RVA_LAYOUT_MISMATCH')
    layouts = [r for r in p.table(15) if r[2] == coded >> 2]
    size = element_count * 4
    _require(len(layouts) == 1 and layouts[0][0] == 1 and layouts[0][1] == size,
             'ALIAS_RVA_LAYOUT_MISMATCH')
    locations = [r for r in p.table(29) if r[1] == rid]
    _require(len(locations) == 1, 'ALIAS_RVA_LOCATION_MISSING_OR_AMBIGUOUS')
    offset = p.meta.rva(locations[0][0], size)
    raw = p.meta.reader.take(offset, size)
    pairs = []
    for at in range(0, size, 8):
        p.budget.check()
        pairs.append(struct.unpack_from('<ii', raw, at))
    return pairs, {'fieldToken': _hex(token), 'metadataOffset': metadata_offset,
                   'dataRva': locations[0][0], 'dataOffset': offset, 'dataBytes': size,
                   'dataSha256': hashlib.sha256(raw).hexdigest()}


def _initialize_array(p, token):
    _require(token >> 24 == 10, 'ALIAS_INITIALIZE_ARRAY_MEMBER_MISMATCH')
    row, _ = p.row(10, token & 0xffffff)
    sig, _ = p.meta.blob(row[2])
    _require(sig[:4] == b'\x00\x02\x01\x12', 'ALIAS_INITIALIZE_ARRAY_SIGNATURE_MISMATCH')
    array, pos = _compressed(sig, 4)
    _require(array & 3 == 1 and pos < len(sig) and sig[pos] == 0x11,
             'ALIAS_INITIALIZE_ARRAY_SIGNATURE_MISMATCH')
    handle, end = _compressed(sig, pos + 1)
    _require(handle & 3 == 1 and end == len(sig), 'ALIAS_INITIALIZE_ARRAY_SIGNATURE_MISMATCH')
    p.external_type(0x01000000 | array >> 2, 'System.Array')
    p.external_type(0x01000000 | handle >> 2, 'System.RuntimeFieldHandle')
    p.member(token, 'System.Runtime.CompilerServices.RuntimeHelpers', 'InitializeArray', sig)


def _extract(p):
    alias_field = p.field('Terraria.ID.ItemID+Sets', 'TextureCopyLoad', b'\x06\x1d\x08')
    count_field = p.field('Terraria.ID.ItemID', 'Count')
    _require(p.fields[count_field] in (b'\x06\x06', b'\x06\x08'), 'ALIAS_COUNT_FIELD_SIGNATURE_MISMATCH')
    count_method = p.method('Terraria.ID.ItemID', '.cctor', b'\x00\x00\x01')
    count_store = _linear_store(count_method, count_field, 1)
    count = _constant(count_store[0])
    _require(0 < count <= p.budget.limits.item_count and (p.fields[count_field] != b'\x06\x06' or count <= 32767),
             'ALIAS_ITEM_COUNT_LIMIT')
    factory_rid, _ = p.owner('Terraria.ID.SetFactory')
    factory_field = p.field('Terraria.ID.ItemID+Sets', 'Factory', b'\x06\x12' + _compressed_bytes(factory_rid << 2))
    cctor = p.method('Terraria.ID.ItemID+Sets', '.cctor', b'\x00\x00\x01')
    head = _shape(cctor['instructions'], [(0x7e,'count'),(0x73,'ctor'),(0x80,'factory')], prefix=True)
    _require(head['count'] == count_field and head['factory'] == factory_field, 'ALIAS_FACTORY_CREATION_MISMATCH')
    ctor = p.method('Terraria.ID.SetFactory', '.ctor', b'\x20\x01\x01\x08')
    _require(ctor['token'] == head['ctor'], 'ALIAS_FACTORY_CONSTRUCTOR_MISMATCH')
    _linear_store(cctor, factory_field, 2)  # Unique direct factory store, no branching.
    part = _linear_store(cctor, alias_field, 8)
    _require(part[0].opcode == 0x7e and part[0].operand == factory_field and _constant(part[1]) == -1,
             'ALIAS_INITIALIZER_FACTORY_OR_DEFAULT_MISMATCH')
    elements = _constant(part[2])
    cap = _shape(part[3:], [(0x8d,'int'),0x25,(0xd0,'data'),(0x28,'initialize'),(0x6f,'create'),(0x80,alias_field)])
    p.external_type(cap['int'], 'System.Int32')
    _initialize_array(p, cap['initialize'])
    pairs, rva = _rva_pairs(p, cap['data'], elements)
    factory = _factory(p, cap['create'])
    item_field = p.field('Terraria.GameContent.TextureAssets', 'Item')
    _require(p.fields[item_field][:2] == b'\x06\x1d', 'ALIAS_ITEM_ARRAY_SIGNATURE_MISMATCH')
    items_cctor = p.method('Terraria.GameContent.TextureAssets', '.cctor', b'\x00\x00\x01')
    allocation = _linear_store(items_cctor, item_field, 2)
    bind = _shape(allocation, [(0x7e,count_field),(0x8d,'element'),(0x80,item_field)])
    _require(p.type_signature(bind['element']) == p.fields[item_field][2:], 'ALIAS_ITEM_ALLOCATION_SIGNATURE_MISMATCH')
    consumer = _consumer(p, alias_field, item_field)
    return count, pairs, {'aliasFieldToken': _hex(alias_field), 'initializerMethodToken': _hex(cctor['token']),
                         'initializerStoreIl': part[-1].offset, 'defaultValue': -1,
                         'count': {'value': count, 'fieldToken': _hex(count_field),
                                   'methodToken': _hex(count_method['token']), 'storeIl': count_store[-1].offset},
                         'rva': rva, 'factory': factory, 'consumer': consumer}


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'ALIAS_INVENTORY_DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def _inventory(data, budget):
    _require(type(data) is bytes and len(data) <= budget.limits.inventory_bytes, 'ALIAS_INVENTORY_BYTE_LIMIT')
    paths = set()
    result = {}
    count = 0
    # Do not split the full input into a second unbounded list of lines.
    start = 0
    while start < len(data):
        budget.check()
        end = data.find(b'\n', start, min(len(data), start + budget.limits.inventory_line_bytes + 1))
        if end < 0:
            end = len(data)
        _require(end - start <= budget.limits.inventory_line_bytes, 'ALIAS_INVENTORY_LINE_LIMIT')
        line = data[start:end]
        start = end + 1
        _require(line and count < budget.limits.inventory_rows, 'ALIAS_INVENTORY_ROW_LIMIT_OR_EMPTY')
        count += 1
        row = json.loads(line, object_pairs_hook=_unique_json_object)
        _require(type(row) is dict and type(row.get('path')) is str, 'ALIAS_INVENTORY_INVALID_ROW')
        path = row['path']
        _require(path not in paths and len(path) <= 4096 and not path.startswith(('/', '\\'))
                 and '\\' not in path and all(p not in ('', '.', '..') for p in path.split('/')),
                 'ALIAS_INVENTORY_DUPLICATE_OR_UNSAFE_PATH')
        paths.add(path)
        match = re.fullmatch(r'Images/Item_(0|[1-9][0-9]{0,8})\.xnb', path)
        if match:
            _require(type(row.get('bytes')) is int and row['bytes'] > 0
                     and re.fullmatch(r'[0-9a-f]{40}', row.get('gitBlobSha1', '')) is not None
                     and row.get('mode') == '100644', 'ALIAS_INVENTORY_INVALID_ASSET')
            result[int(match[1])] = {'path': path, 'bytes': row['bytes'], 'gitBlobSha1': row['gitBlobSha1']}
    return result, count


def _resolve(count, pairs, inventory, budget):
    """Simulate only proven ascending assignment, never transitive graph lookup."""
    aliases = {}
    duplicate_keys = []
    for key, value in pairs:
        budget.check()
        _require(0 <= key < count and -1 <= value < count, 'ALIAS_PAIR_OUTSIDE_DOMAIN')
        if key in aliases:
            duplicate_keys.append(key)
        aliases[key] = value  # Actual factory semantics: later write wins.
    rows, resolved, missing, invalid, forward = [], {}, [], [], []
    direct_present = 0
    for item in range(count):
        budget.check()
        has_direct = item in inventory
        direct_present += int(has_direct)
        target = aliases.get(item, -1)
        row = {'id': item, 'directImagePresent': has_direct, 'aliasTarget': None if target == -1 else target}
        if target == -1:
            if has_direct:
                resolved[item] = (item, 0)
                row.update(status='DIRECT_IMAGE_PRESENT', ultimateItem=item, chainEdges=0, asset=inventory[item])
            else:
                missing.append(item)
                row['status'] = 'MISSING_DIRECT_IMAGE'
        elif target >= item:
            # It points to an as-yet-unassigned slot, even if a file exists or
            # the mathematical alias graph eventually reaches a real file.
            forward.append(item)
            invalid.append(item)
            row['status'] = 'UNSUPPORTED_NONPRIOR_ALIAS'
        elif target not in resolved:
            invalid.append(item)
            row['status'] = 'UNRESOLVED_PRIOR_ALIAS'
        else:
            base, hops = resolved[target]
            resolved[item] = (base, hops + 1)
            row.update(status='PRIOR_ITEM_COPY_RESOLVED', ultimateItem=base, chainEdges=hops + 1, asset=inventory[base])
        rows.append(row)
    missing_direct = [r['id'] for r in rows if not r['directImagePresent']]
    return {'itemCount': count, 'pairCount': len(pairs), 'effectiveAliasCount': sum(v != -1 for v in aliases.values()),
            'duplicatePairKeys': duplicate_keys, 'directImagesPresent': direct_present,
            'missingDirectImages': len(missing_direct), 'missingDirectIds': missing_direct,
            'missingDirectResolvedByAlias': sum(not r['directImagePresent'] and r['status'] == 'PRIOR_ITEM_COPY_RESOLVED' for r in rows),
            'logicalImagesResolved': len(resolved), 'logicalCoverageSatisfied': len(resolved) == count,
            'unsupportedNonpriorIds': forward, 'unresolvedIds': missing + invalid,
            'maxChainEdges': max((hops for _, hops in resolved.values()), default=0), 'items': rows}


def extract_item_texture_aliases(data: bytes, inventory_ndjson: bytes, *,
                                 limits=ItemTextureAliasLimits(), checkpoint=None,
                                 expected_input_sha256=None):
    """Return private hash-bound evidence; unsupported variants yield no table.

    expected_input_sha256 is an optional caller pin, never a trust assertion.
    The inventory is bound by its full byte hash; asset blob IDs are claims in
    that inventory, not verified pixel hashes. Cancellation exceptions propagate.
    """
    budget = _Budget(limits, checkpoint)
    result = {'schema': 'item-texture-alias-evidence-v1', 'status': 'UNSUPPORTED',
              'scope': 'conditional initializer-store and first consumer-loop semantics',
              'complete': False, 'publicationAllowed': False, 'executedInput': False,
              'runtimeStateCertified': False, 'imageBytesVerified': False,
              'inventoryRootPolicy': 'paths relative to the Content root; exact Images/Item_<id>.xnb only',
              'preconditions': ['the selected initializer store is reached and CreateIntSet returns normally',
                                'its returned buffer covers the consumer item domain',
                                'Count, TextureCopyLoad and TextureAssets.Item retain their evidenced values at consumer entry',
                                'the item loop completes normally with the external asset manager honoring the requested path',
                                'the inventory belongs to the matching client Content; version equivalence is not certified'],
              'excluded': ['whole initializer side effects and later mutation', 'constructor and pooled-buffer lifecycle closure',
                           'later consumer calls and client/server equivalence', 'pixel decoding and publication rights'],
              'aliases': [], 'coverage': None, 'evidence': []}
    try:
        budget.check()
        _require(type(data) is bytes and len(data) <= limits.input_bytes, 'ALIAS_INPUT_BYTE_LIMIT')
        digest = hashlib.sha256(data).hexdigest()
        result['inputSha256'] = digest
        _require(expected_input_sha256 is None or digest == expected_input_sha256, 'ALIAS_INPUT_HASH_MISMATCH')
        inventory, inventory_rows = _inventory(inventory_ndjson, budget)
        result['inventorySha256'] = hashlib.sha256(inventory_ndjson).hexdigest()
        result['inventoryRows'] = inventory_rows
        p = _Program(data, budget)
        count, pairs, proof = _extract(p)
        # Check projected construction before allocating all item evidence rows.
        _require(count * 640 + len(pairs) * 80 + 16384 <= limits.evidence_bytes, 'ALIAS_EVIDENCE_BYTE_LIMIT')
        coverage = _resolve(count, pairs, inventory, budget)
        result.update(status='PROVEN_CONDITIONAL_ITEM_TEXTURE_ALIASES',
                      aliases=[{'id': key, 'copyFrom': value} for key, value in pairs],
                      proof=proof, coverage=coverage, evidence=p.evidence)
        json_evidence_size(result, limits.evidence_bytes, budget.check)
    except _CheckpointCancelled as exc:
        raise exc.original
    except ILUnsupported as exc:
        result.update(status=exc.code, aliases=[], coverage=None, evidence=[],
                      diagnostic={'code': exc.code, 'ilOffset': exc.offset, 'token': _hex(exc.token) if exc.token else None})
        result.pop('proof', None)
    except (PipelineError, ValueError, UnicodeError, struct.error, IndexError, KeyError, RecursionError, TypeError) as exc:
        # Malformed untrusted bytes never escape as a partially certified table.
        result.update(status='ALIAS_MALFORMED_INPUT', aliases=[], coverage=None, evidence=[],
                      diagnostic={'code': 'ALIAS_MALFORMED_INPUT', 'errorType': type(exc).__name__})
        result.pop('proof', None)
    result['work'] = {'steps': budget.steps, 'decodedInstructions': budget.instructions,
                      'methodBytes': budget.method_bytes}
    try:
        json_evidence_size(result, limits.evidence_bytes, budget.check)
    except _CheckpointCancelled as exc:
        raise exc.original
    except (ILUnsupported, EvidenceSizeLimit) as exc:
        # Even the final serialization pass belongs to the same work/deadline
        # budget. Its failure must not retain a usable conditional table.
        result = {'schema': 'item-texture-alias-evidence-v1',
                  'status': getattr(exc, 'code', 'ALIAS_EVIDENCE_BYTE_LIMIT'),
                  'complete': False, 'publicationAllowed': False, 'executedInput': False,
                  'runtimeStateCertified': False, 'imageBytesVerified': False,
                  'aliases': [], 'coverage': None, 'evidence': []}
        json_evidence_size(result, limits.evidence_bytes)
    return result
