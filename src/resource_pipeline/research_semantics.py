"""Bounded, data-only creative research evidence for one audited server profile.

No assembly/dependency is loaded or invoked. This deliberately separate family
contains a shared definition table and a shared single-hop override table; it is
not an Item.SetDefaults implementation, viewer export, or publishable bundle.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import struct
import time

from .security import PipelineError, canonical_json
from .static_il import ILUnsupported, StaticILLimits, _compressed, decode_il, json_evidence_size

REFERENCE = {
    'repository': 'Live-yum/TerrariaDecompiledSource',
    'commit': '8255d34616c780af12079425ac92a0a7aed87d71',
    'catalogPath': 'Terraria.GameContent.Creative/CreativeItemSacrificesCatalog.cs',
    'catalogGitBlob': '92588219aee5537119650f416dea615495114add',
    'contentSamplesPath': 'Terraria.ID/ContentSamples.cs',
    'contentSamplesGitBlob': '728464f8e9bcf6ee2f617851dab32a4a43a62188',
}
PROFILE = {
    'id': 'windows-server-research-v1',
    'platform': 'windows',
    'inputSha256': 'd87e3faf08637f6be8882c63e7f11fb7e792b0230006309618473ece0f863e1e',
    'resourceSha256': '6123e477a86dc3dfa1c4f3465c8287677e0f9e54eaa5e14ea8adffa473cdfcb2',
}
_RESOURCE = 'Terraria.GameContent.Creative.Content.Sacrifices.tsv'
_CATALOG = 'Terraria.GameContent.Creative.CreativeItemSacrificesCatalog'
_SAMPLES = 'Terraria.ID.ContentSamples'
_BASE = (_CATALOG, '_sacrificeCountNeededByItemId')
_OVERRIDES = (_SAMPLES, 'CreativeResearchItemPersistentIdOverride')
_CORE_KEYS = {name: bytes.fromhex('b77a5c561934e089') for name in ('mscorlib', 'System')}
# Rule constants, not an embedded game item table. Binary category paths are
# checked independently before these rules are used for the fixed profile.
_CAPS = {'': 50, 'a': 50, 'b': 25, 'c': 5, 'd': 1, 'e': None, 'f': 2,
         'g': 3, 'h': 10, 'i': 15, 'j': 30, 'k': 99, 'l': 100, 'm': 200,
         'n': 20, 'o': 400}


@dataclass(frozen=True)
class ResearchLimits:
    input_bytes: int = 128 * 1024 * 1024
    resource_bytes: int = 1024 * 1024
    rows: int = 20000
    row_bytes: int = 4096
    names: int = 20000
    name_bytes: int = 1024
    instructions: int = 8192
    array_items: int = 1024
    override_calls: int = 256
    evidence_bytes: int = 8 * 1024 * 1024
    wall_seconds: float = 30

    def __post_init__(self):
        for key, value in vars(self).items():
            if key == 'wall_seconds':
                if type(value) not in (int, float) or not 0 < value <= 120:
                    raise ValueError('Invalid research time budget')
            elif type(value) is not int or value <= 0:
                raise ValueError('Invalid research integer budget')



def _research_checkpoint(limits, checkpoint=None):
    external = checkpoint or (lambda: None)
    deadline = time.monotonic() + limits.wall_seconds
    def check():
        external()
        if time.monotonic() >= deadline:
            raise PipelineError('Research wall time budget')
    check()
    return check


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _int32(value):
    if type(value) is not int or not -(1 << 31) <= value < (1 << 31):
        raise PipelineError('Research identifier is not int32')
    return value


def parse_research_catalog(payload, names, *, culture="invariant", limits=ResearchLimits(), checkpoint=None):
    """Model the ASCII input subset of Initialize under an explicit culture.

    No stripping, numeric-ID fallback, case-insensitive name search, or arbitrary
    Python Unicode lowercasing. The runtime uses culture-sensitive ToLower; this
    subset specifies invariant culture and uses only its ASCII A-Z case map.
    Unknown recognized categories fail, and an excluded row never removes an
    earlier assignment. Unknown item names are ignored before category handling.
    """
    checkpoint = _research_checkpoint(limits, checkpoint)
    if culture != 'invariant':
        raise PipelineError('Unsupported research culture; invariant model required')
    if type(payload) is not bytes or len(payload) > limits.resource_bytes:
        raise PipelineError('Research resource byte limit')
    if type(names) is not dict or len(names) > limits.names:
        raise PipelineError('Research name binding limit')
    for name, value in names.items():
        checkpoint()
        if type(name) is not str or len(name.encode('utf-8')) > limits.name_bytes:
            raise PipelineError('Research name binding limit')
        _int32(value)
    # StreamReader's default UTF-8 with optional BOM is sufficient for this
    # observed ASCII resource. Reject other encodings instead of approximating.
    if payload.startswith(b'\xef\xbb\xbf'):
        payload = payload[3:]
    try:
        text = payload.decode('ascii')
    except UnicodeError as exc:
        raise PipelineError('Unsupported non-ASCII research resource') from exc
    lines = re.split(r'\r\n|\r|\n', text)
    if len(lines) > limits.rows:
        raise PipelineError('Research row limit')
    base, origins, seen = {}, {}, set()
    stats = dict(rows=len(lines), comments=0, shortRows=0, unknownNames=0,
                 recognizedRows=0, duplicateRecognizedRows=0, excludedRows=0,
                 assignments=0)
    for number, line in enumerate(lines):
        checkpoint()
        if len(line) > limits.row_bytes:
            raise PipelineError('Research row byte limit')
        if line.startswith('//'):
            stats['comments'] += 1
            continue
        columns = line.split('\t')
        if len(columns) < 3:
            stats['shortRows'] += 1
            continue
        name, category = columns[:2]
        if name not in names:
            stats['unknownNames'] += 1
            continue
        category = category.translate(str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'))
        if category not in _CAPS:
            raise PipelineError('Unsupported research category or culture-sensitive casing')
        identifier = names[name]
        stats['recognizedRows'] += 1
        stats['duplicateRecognizedRows'] += identifier in seen
        seen.add(identifier)
        cap = _CAPS[category]
        if cap is None:
            stats['excludedRows'] += 1
            continue
        base[str(identifier)] = cap
        origins[str(identifier)] = {'row': number, 'name': name, 'category': category}
        stats['assignments'] += 1
    stats['distinctRecognizedIds'] = len(seen)
    stats['baseDefinitionCount'] = len(base)
    result = {'baseCounts': base, 'baseOrigins': origins, 'statistics': stats}
    json_evidence_size(result, limits.evidence_bytes, checkpoint)
    return result


def lookup_research_count(model, item_id):
    """One override hop, including absence; this does not enumerate definitions."""
    _int32(item_id)
    key = str(item_id)
    resolved = model['persistentIdOverrides'].get(key, item_id)
    _int32(resolved)
    cap = model['baseCounts'].get(str(resolved))
    return {'itemId': item_id, 'definitionId': resolved, 'available': cap is not None,
            'count': cap, 'overridden': key in model['persistentIdOverrides'],
            'hasOwnDefinition': key in model['baseCounts']}


class _Binary:
    """Small structural metadata reader; never a CLR or input-call evaluator."""
    def __init__(self, meta, types, limits, checkpoint):
        self.meta, self.types, self.limits, self.checkpoint = meta, types, limits, checkpoint
        self.methods = {}
        self.evidence = []
        self.total_instructions = 0

    def owner(self, table, rid):
        first, last = ('firstMethod', 'lastMethod') if table == 6 else ('firstField', 'lastField')
        found = [row for row in self.types.values() if row[first] <= rid < row[last]]
        if len(found) != 1:
            raise PipelineError('Research metadata owner ambiguity')
        return found[0]['fullName']

    def core_type(self, token):
        if token >> 24 != 1:
            raise PipelineError('Research intrinsic requires external core TypeRef')
        row, _ = self.meta.row(1, token & 0xffffff)
        if row[0] & 3 != 2:
            raise PipelineError('Research intrinsic has unsupported assembly scope')
        assembly, _ = self.meta.row(35, row[0] >> 2)
        name, culture = self.meta.string(assembly[6]), self.meta.string(assembly[7])
        key, _ = self.meta.blob(assembly[5])
        if assembly[4] or culture or key != _CORE_KEYS.get(name):
            raise PipelineError('Research intrinsic core assembly identity mismatch')
        return self.meta.string(row[2]) + '.' + self.meta.string(row[1])

    def type_name(self, table, rid):
        if table == 2:
            return self.types[rid]['fullName']
        if table == 1:
            row, _ = self.meta.row(1, rid)
            return self.meta.string(row[2]) + '.' + self.meta.string(row[1])
        if table == 27:
            row, _ = self.meta.row(27, rid)
            sig, _ = self.meta.blob(row[0])
            if sig[:2] != b'\x15\x12':
                raise PipelineError('Unsupported research TypeSpec')
            coded, pos = _compressed(sig, 2)
            if coded & 3 != 1 or sig[pos:] != b'\x02\x08\x08':
                raise PipelineError('Research dictionary must be int32/int32')
            name = self.core_type(0x01000000 | coded >> 2)
            if name != 'System.Collections.Generic.Dictionary`2':
                raise PipelineError('Research dictionary owner mismatch')
            return 'Dictionary<int32,int32>'
        raise PipelineError('Unsupported research owner token')

    def member(self, token):
        table, rid = token >> 24, token & 0xffffff
        row, _ = self.meta.row(table, rid)
        if table in (4, 6):
            return (self.owner(table, rid), self.meta.string(row[1 if table == 4 else 3]),
                    self.meta.blob(row[2 if table == 4 else 4])[0])
        if table == 10:
            tag, parent = row[0] & 7, row[0] >> 3
            owner_table = {0: 2, 1: 1, 4: 27}.get(tag)
            owner = self.type_name(owner_table, parent)
            if owner_table == 1 and owner.startswith('System.'):
                self.core_type(0x01000000 | parent)
            return owner, self.meta.string(row[1]), self.meta.blob(row[2])[0]
        raise PipelineError('Unsupported research member token')

    def string(self, token):
        if token >> 24 != 0x70 or '#US' not in self.meta.streams:
            raise PipelineError('Invalid research user-string token')
        heap = self.meta.streams['#US']
        start = heap.start + (token & 0xffffff)
        # Decode within the heap, not the containing PE buffer.
        end = min(heap.end, start + 4)
        size, prefix = _compressed(heap.take(start, end - start), 0)
        if not size or size > self.limits.row_bytes * 2 + 1 or not size & 1:
            raise PipelineError('Research user-string limit/width')
        raw = heap.take(start + prefix, size)
        if raw[-1] not in (0, 1):
            raise PipelineError('Invalid research user-string flag')
        try:
            return raw[:-1].decode('utf-16-le', errors='strict')
        except UnicodeError as exc:
            raise PipelineError('Invalid research user-string') from exc

    def field(self, token, expected, *, static):
        if token >> 24 != 4 or self.member(token)[:2] != expected:
            raise PipelineError('Research field binding mismatch')
        row, _ = self.meta.row(4, token & 0xffffff)
        if bool(row[0] & 0x10) != static:
            raise PipelineError('Research field storage mismatch')
        sig = self.member(token)[2]
        if sig[:3] != b'\x06\x15\x12':
            raise PipelineError('Research field is not dictionary')
        coded, pos = _compressed(sig, 3)
        if coded & 3 != 1 or sig[pos:] != b'\x02\x08\x08' or self.core_type(0x01000000 | coded >> 2) != 'System.Collections.Generic.Dictionary`2':
            raise PipelineError('Research dictionary field signature mismatch')
        return token

    def method(self, owner, name, signature, local_signature=None):
        matches = []
        for typedef in self.types.values():
            if typedef['fullName'] == owner:
                for rid in range(typedef['firstMethod'], typedef['lastMethod']):
                    self.checkpoint()
                    row, offset = self.meta.row(6, rid)
                    if self.meta.string(row[3]) == name:
                        matches.append((rid, row, offset))
        if len(matches) != 1:
            raise PipelineError('Research method missing or ambiguous')
        rid, row, metadata_offset = matches[0]
        token = 0x06000000 | rid
        if token in self.methods:
            return self.methods[token]
        sig, _ = self.meta.blob(row[4])
        if sig != signature or row[1] or row[2] & (0x2000 | 0x40 | 0x400) or bool(row[2] & 0x10) == bool(sig[0] & 0x20):
            raise PipelineError('Unsupported research method signature/flags')
        body = self.meta.rva(row[0], 1)
        first = self.meta.reader.uint(body, 1)
        locals_token = 0
        if first & 3 == 2:
            header, size = 1, first >> 2
        elif first & 3 == 3:
            flags = self.meta.reader.uint(body, 2)
            if flags & ~0xf013 or flags >> 12 != 3:
                raise PipelineError('Unsupported research method header/exception regions')
            header, size = 12, self.meta.reader.uint(body + 4, 4)
            locals_token = self.meta.reader.uint(body + 8, 4)
        else:
            raise PipelineError('Unsupported research method header')
        if size > 65536:
            raise PipelineError('Research method byte limit')
        if locals_token:
            if locals_token >> 24 != 17:
                raise PipelineError('Research local signature token mismatch')
            local_row, _ = self.meta.row(17, locals_token & 0xffffff)
            locals_blob, _ = self.meta.blob(local_row[0])
        else:
            locals_blob = None
        if locals_blob != local_signature:
            raise PipelineError('Research method local signature mismatch')
        code_offset = self.meta.rva(row[0] + header, size)
        code = self.meta.reader.take(code_offset, size)
        instructions = list(decode_il(code, StaticILLimits(method_instructions=self.limits.instructions), self.checkpoint).values())
        self.total_instructions += len(instructions)
        if self.total_instructions > self.limits.instructions:
            raise PipelineError('Research total instruction limit')
        evidence = {'owner': owner, 'method': name, 'token': f'0x{token:08x}',
                    'metadataOffset': metadata_offset, 'bodyOffset': body,
                    'codeOffset': code_offset, 'codeBytes': size, 'ilSha256': _sha(code),
                    'signatureSha256': _sha(sig), 'instructions': len(instructions)}
        result = {'token': token, 'instructions': instructions, 'evidence': evidence}
        self.methods[token] = result
        self.evidence.append(evidence)
        return result

    def resource(self, name):
        matches = []
        if self.meta.rows[40] > 4096:
            raise PipelineError('Research manifest resource limit')
        for rid in range(1, self.meta.rows[40] + 1):
            self.checkpoint()
            row, offset = self.meta.row(40, rid)
            if self.meta.string(row[2]) == name:
                matches.append((rid, row, offset))
        if len(matches) != 1 or matches[0][1][3] or self.meta.resources is None:
            raise PipelineError('Research embedded resource missing or ambiguous')
        rid, row, offset = matches[0]
        start = self.meta.resources.start + row[0]
        size = self.meta.resources.uint(start, 4)
        if size > self.limits.resource_bytes:
            raise PipelineError('Research resource byte limit')
        payload = self.meta.resources.take(start + 4, size)
        return payload, {'manifestToken': f'0x{0x28000000 | rid:08x}', 'metadataOffset': offset,
                         'dataOffset': start + 4, 'bytes': size, 'sha256': _sha(payload)}


def _expect(instructions, pattern):
    if len(instructions) != len(pattern):
        raise PipelineError('Unsupported research control-flow shape')
    for instruction, expected in zip(instructions, pattern):
        op, operand = expected
        if instruction.opcode != op or (operand is not Ellipsis and instruction.operand != operand):
            raise PipelineError('Research control-flow pattern mismatch')


def _call(binary, instruction, owner, name, signature):
    if instruction.opcode not in (0x28, 0x6f, 0x73) or binary.member(instruction.operand) != (owner, name, signature):
        raise PipelineError('Research call identity/signature mismatch')


def _integer(instruction):
    op = instruction.opcode
    if 0x15 <= op <= 0x1e:
        return op - 0x16
    if op in (0x1f, 0x20):
        return instruction.operand
    raise PipelineError('Research grammar requires int32 constant')


def _verify_helpers(binary):
    inner = binary.method(_SAMPLES, 'AddItemResearchOverride_Inner', b'\x00\x02\x01\x08\x08')
    ins = inner['instructions']
    _expect(ins, [(0x7e, ...), (2, None), (3, None), (0x6f, ...), (0x2a, None)])
    field = binary.field(ins[0].operand, _OVERRIDES, static=True)
    _call(binary, ins[3], 'Dictionary<int32,int32>', 'set_Item', b'\x20\x02\x01\x13\x00\x13\x01')
    helper = binary.method(_SAMPLES, 'AddItemResearchOverride', b'\x00\x02\x01\x08\x1d\x08', b'\x07\x01\x08')
    ins = helper['instructions']
    _expect(ins, [(0x16,None),(0x0a,None),(0x2b,17),(3,None),(6,None),(0x94,None),
                  (2,None),(0x28,inner['token']),(6,None),(0x17,None),(0x58,None),
                  (0x0a,None),(6,None),(3,None),(0x8e,None),(0x69,None),(0x32,4),(0x2a,None)])
    query = binary.method(_CATALOG, 'TryGetSacrificeCountCapToUnlockInfiniteItems',
                          b'\x20\x02\x02\x08\x10\x08', b'\x07\x01\x08')
    ins = query['instructions']
    _expect(ins, [(0x7e,field),(3,None),(0x12,0),(0x6f,...),(0x2c,18),(6,None),
                  (0x10,1),(2,None),(0x7b,...),(3,None),(4,None),(0x6f,...),(0x2a,None)])
    binary.field(ins[8].operand, _BASE, static=False)
    for i in (3, 11):
        _call(binary, ins[i], 'Dictionary<int32,int32>', 'TryGetValue', b'\x20\x02\x02\x13\x00\x10\x13\x01')
    return helper['token'], {'overrideHelper': 'array[index] -> dictionary[source] = target; ascending index; later writes win',
                             'lookup': 'one override TryGetValue; assign argument on success; one base TryGetValue',
                             'recursiveOverrides': False}


def extract_override_initializer(instructions, helper_token, *, int_type, initialize_array,
                                 read_rva, limits=ResearchLimits(), checkpoint=None):
    """Recognize only straight-line int32/array construction and verified calls.

    read_rva returns (list[int], evidence) for exactly the requested array size.
    No unrecognized instruction or partially initialized array yields a result.
    This grammar is also exercised using wholly synthetic instructions.
    """
    checkpoint = _research_checkpoint(limits, checkpoint)
    if len(instructions) > limits.instructions:
        raise PipelineError('Research initializer instruction limit')
    pos, result, calls, sources = 0, {}, [], []
    def take(op=None):
        nonlocal pos
        checkpoint()
        if pos >= len(instructions):
            raise PipelineError('Truncated research initializer')
        ins = instructions[pos]; pos += 1
        if op is not None and ins.opcode != op:
            raise PipelineError('Unsupported research initializer grammar')
        return ins
    while pos < len(instructions) and instructions[pos].opcode != 0x2a:
        if len(calls) >= limits.override_calls:
            raise PipelineError('Research override call limit')
        call_start = instructions[pos].offset
        target, length = _integer(take()), _integer(take())
        if not 0 < length <= limits.array_items:
            raise PipelineError('Research override array limit')
        if take(0x8d).operand != int_type:
            raise PipelineError('Research override array element type mismatch')
        values = [None] * length
        take(0x25)
        if pos < len(instructions) and instructions[pos].opcode == 0xd0:
            field = take(0xd0).operand
            if take(0x28).operand != initialize_array:
                raise PipelineError('Research array initialization helper mismatch')
            values, evidence = read_rva(field, length)
            if len(values) != length or any(type(value) is not int for value in values):
                raise PipelineError('Research RVA array length/type mismatch')
            sources.append(evidence)
        else:
            for index in range(length):
                if index:
                    take(0x25)
                if _integer(take()) != index:
                    raise PipelineError('Research array index is not ascending/complete')
                values[index] = _integer(take())
                take(0x9e)
        if take(0x28).operand != helper_token:
            raise PipelineError('Research override writer mismatch')
        for source in values:
            _int32(source); _int32(target)
            result[str(source)] = target
        calls.append({'ilOffset': call_start, 'target': target, 'sources': values})
    take(0x2a)
    if pos != len(instructions):
        raise PipelineError('Research initializer has trailing instructions')
    output = {'persistentIdOverrides': result, 'overrideCalls': calls, 'rvaArrays': sources}
    json_evidence_size(output, limits.evidence_bytes, checkpoint)
    return output


def _rva_array(binary, token, count):
    if token >> 24 != 4:
        raise PipelineError('Research RVA requires field token')
    row, field_offset = binary.meta.row(4, token & 0xffffff)
    if row[0] != 0x133:
        raise PipelineError('Research RVA field flags mismatch')
    sig, _ = binary.meta.blob(row[2])
    if sig[:2] != b'\x06\x11':
        raise PipelineError('Research RVA field type mismatch')
    coded, end = _compressed(sig, 2)
    if end != len(sig) or coded & 3 or not coded >> 2:
        raise PipelineError('Research RVA layout type mismatch')
    layout = [r for i in range(1, binary.meta.rows[15] + 1)
              if (r := binary.meta.row(15, i)[0])[2] == coded >> 2]
    if len(layout) != 1 or layout[0][1] != count * 4:
        raise PipelineError('Research RVA exact size mismatch')
    rows = [(r, offset) for i in range(1, binary.meta.rows[29] + 1)
            if (r := binary.meta.row(29, i)[0])[1] == token & 0xffffff
            for offset in (binary.meta.row(29, i)[1],)]
    if len(rows) != 1:
        raise PipelineError('Research RVA field binding ambiguity')
    offset = binary.meta.rva(rows[0][0][0], count * 4)
    raw = binary.meta.reader.take(offset, count * 4)
    return list(struct.unpack('<' + 'i' * count, raw)), {
        'fieldToken': f'0x{token:08x}', 'fieldMetadataOffset': field_offset,
        'rvaMetadataOffset': rows[0][1], 'dataOffset': offset, 'bytes': len(raw), 'sha256': _sha(raw)}


def _verify_hash(binary):
    method = binary.method('<PrivateImplementationDetails>', 'ComputeStringHash',
                           b'\x00\x01\x09\x0e', b'\x07\x02\x09\x08')
    ins = method['instructions']
    _expect(ins, [(2,None),(0x2c,42),(0x20,-2128831035),(0x0a,None),(0x16,None),
                  (0x0b,None),(0x2b,33),(2,None),(7,None),(0x6f,...),(6,None),
                  (0x61,None),(0x20,16777619),(0x5a,None),(0x0a,None),(7,None),
                  (0x17,None),(0x58,None),(0x0b,None),(7,None),(2,None),(0x6f,...),
                  (0x32,13),(6,None),(0x2a,None)])
    _call(binary, ins[9], 'System.String', 'get_Chars', b'\x20\x01\x03\x08')
    _call(binary, ins[21], 'System.String', 'get_Length', b'\x20\x00\x08')
    return method['token']


def _fnv(value):
    result = 2166136261
    for char in value:
        result = ((result ^ ord(char)) * 16777619) & 0xffffffff
    return result


def _category_path(binary, instructions, category, hash_token):
    """Evaluate only a closed branch-and-scalar category dispatch, not methods."""
    by_offset = {i.offset: i for i in instructions}
    pc, stack, locals_, path = 0x7a, [], {5: 0, 6: 0, 7: category}, []
    for _ in range(512):
        binary.checkpoint()
        if pc == 0x36c:
            if stack:
                raise PipelineError('Research category leaves stack values')
            return (None if locals_[6] else locals_[5]), path
        if pc == 0x352:
            return 'invalid-category', path
        if pc not in by_offset:
            raise PipelineError('Research category escaped dispatch')
        ins = by_offset[pc]
        path.append(pc)
        op, operand, pc = ins.opcode, ins.operand, ins.next_offset
        if op in (0x11, 0x13):
            if operand not in (5, 6, 7, 8):
                raise PipelineError('Research category local mismatch')
            if op == 0x11:
                if operand not in locals_:
                    raise PipelineError('Research category uninitialized local')
                stack.append(locals_[operand])
            else:
                locals_[operand] = stack.pop()
        elif 0x15 <= op <= 0x20:
            stack.append(_integer(ins))
        elif op == 0x72:
            stack.append(binary.string(operand))
        elif op == 0x28:
            if operand == hash_token:
                value = stack.pop()
                stack.append(_fnv(value))
            else:
                owner, name, sig = binary.member(operand)
                if (owner,name,sig) == ('System.String','op_Equality',b'\x00\x02\x02\x0e\x0e'):
                    right, left = stack.pop(), stack.pop()
                    stack.append(left == right)
                elif (owner,name,sig) == ('System.String','get_Length',b'\x20\x00\x08'):
                    stack.append(len(stack.pop()))
                else:
                    raise PipelineError('Research category unknown intrinsic')
        elif op in (0x2b, 0x38):
            pc = operand
        elif op in (0x2c, 0x39, 0x2d, 0x3a):
            value = stack.pop()
            # A CLR string's reference truth is independent of emptiness.
            truth = value is not None if type(value) is str else bool(value)
            if truth == (op in (0x2d, 0x3a)):
                pc = operand
        elif op in (0x3b, 0x35, 0x42):
            right, left = stack.pop(), stack.pop()
            match = (left & 0xffffffff) == (right & 0xffffffff) if op == 0x3b else (left & 0xffffffff) > (right & 0xffffffff)
            if match:
                pc = operand
        else:
            raise PipelineError('Research category unsupported instruction')
    raise PipelineError('Research category step limit')


def _verify_initialize(binary):
    method = binary.method(_CATALOG, 'Initialize', b'\x20\x00\x01',
                           b'\x07\x09\x1d\x0e\x08\x0e\x1d\x0e\x08\x08\x02\x0e\x09')
    ins = method['instructions']
    prefix = [i for i in ins if i.offset < 0x7a]
    _expect(prefix, [(2,None),(0x7b,...),(0x6f,...),(0x72,...),(0x28,...),(0x72,...),
        (0x28,...),(0x0a,None),(0x16,None),(0x0b,None),(0x38,899),(6,None),(7,None),
        (0x9a,None),(0x0c,None),(8,None),(0x72,...),(0x6f,...),(0x3a,895),(8,None),
        (0x17,None),(0x8d,...),(0x25,None),(0x16,None),(0x1f,9),(0x9d,None),
        (0x6f,...),(0x0d,None),(9,None),(0x8e,None),(0x69,None),(0x19,None),
        (0x3f,895),(0x7e,...),(9,None),(0x16,None),(0x9a,None),(0x12,4),
        (0x6f,...),(0x39,895),(0x16,None),(0x13,5),(0x16,None),(0x13,6),
        (9,None),(0x17,None),(0x9a,None),(0x6f,...),(0x13,7)])
    base_field = binary.field(prefix[1].operand, _BASE, static=False)
    _call(binary,prefix[2],'Dictionary<int32,int32>','Clear',b'\x20\x00\x01')
    if [binary.string(prefix[x].operand) for x in (3,5,16)] != [_RESOURCE, '\r\n|\r|\n', '//']:
        raise PipelineError('Research text splitting literals mismatch')
    _call(binary,prefix[4],'Terraria.Utils','ReadEmbeddedResource',b'\x00\x01\x0e\x0e')
    _call(binary,prefix[6],'System.Text.RegularExpressions.Regex','Split',b'\x00\x02\x1d\x0e\x0e\x0e')
    _call(binary,prefix[17],'System.String','StartsWith',b'\x20\x01\x02\x0e')
    if binary.core_type(prefix[21].operand) != 'System.Char':
        raise PipelineError('Research separator array type mismatch')
    _call(binary,prefix[26],'System.String','Split',b'\x20\x01\x1d\x0e\x1d\x03')
    if binary.member(prefix[33].operand)[:2] != ('Terraria.ID.ItemID','Search'):
        raise PipelineError('Research ItemID search field mismatch')
    _call(binary,prefix[38],'ReLogic.Reflection.IdDictionary','TryGetId',b'\x20\x02\x02\x0e\x10\x08')
    _call(binary,prefix[47],'System.String','ToLower',b'\x20\x00\x0e')
    suffix = [i for i in ins if i.offset >= 0x36c]
    _expect(suffix,[(0x11,6),(0x2d,895),(2,None),(0x7b,base_field),(0x11,4),(0x11,5),
                    (0x6f,...),(7,None),(0x17,None),(0x58,None),(0x0b,None),
                    (7,None),(6,None),(0x8e,None),(0x69,None),(0x3f,39),(0x2a,None)])
    _call(binary,suffix[6],'Dictionary<int32,int32>','set_Item',b'\x20\x02\x01\x13\x00\x13\x01')
    invalid = [i for i in ins if 0x352 <= i.offset < 0x36c]
    _expect(invalid,[(0x72,...),(9,None),(0x16,None),(0x9a,None),(0x72,...),(0x11,7),
                     (0x28,...),(0x73,...),(0x7a,None)])
    _call(binary,invalid[6],'System.String','Concat',b'\x00\x04\x0e\x0e\x0e\x0e\x0e')
    _call(binary,invalid[7],'System.Exception','.ctor',b'\x20\x01\x01\x0e')
    hash_token = _verify_hash(binary)
    dispatch = [i for i in ins if 0x7a <= i.offset < 0x352]
    # Validate all opcodes and branch bounds, including untaken dispatch edges.
    allowed = {0x11,0x13,*range(0x15,0x21),0x72,0x28,0x2b,0x38,0x2c,0x39,0x2d,0x3a,0x3b,0x35,0x42}
    offsets = {i.offset for i in dispatch} | {0x352,0x36c}
    for i in dispatch:
        if i.opcode not in allowed:
            raise PipelineError('Research dispatch opcode not in closed grammar')
        if i.opcode in {0x2b,0x38,0x2c,0x39,0x2d,0x3a,0x3b,0x35,0x42} and i.operand not in offsets:
            raise PipelineError('Research dispatch branch escapes closed region')
    paths = []
    for category, expected in list(_CAPS.items()) + [('unsupported-synthetic-category', 'invalid-category')]:
        actual, path = _category_path(binary, dispatch, category, hash_token)
        if actual != expected:
            raise PipelineError('Research category control-flow disagrees with model')
        paths.append({'category': category, 'result': actual, 'ilOffsets': path})
    return {'clearBeforeRows': True, 'split': 'CRLF|CR|LF then literal tab; no trim',
            'ignore': 'prefix //, fewer than 3 columns, or unknown exact name',
            'categoryInputScope': 'ASCII under explicit invariant-culture model; ambient runtime culture not assumed',
            'unknownCategory': 'throw before assignment', 'duplicatePolicy': 'last active assignment wins',
            'excludedPolicy': 'skip without removing an earlier assignment', 'categoryPaths': paths}


def _name_bindings(binary):
    method = binary.method('Terraria.ID.ItemID', '.cctor', b'\x00\x00\x01')
    ins = method['instructions']
    _expect(ins,[(0x20,...),(0x80,...),(0x28,...),(0x80,...),(0x2a,None)])
    count = _integer(ins[0])
    if count <= 0 or count > 32767:
        raise PipelineError('Research ItemID Count is outside positive short range')
    if binary.member(ins[1].operand) != ('Terraria.ID.ItemID','Count',b'\x06\x06'):
        raise PipelineError('Research Count assignment mismatch')
    count_row, _ = binary.meta.row(4, ins[1].operand & 0xffffff)
    if count_row[0] != 0x36:
        raise PipelineError('Research Count must be public static readonly short')
    if binary.member(ins[3].operand)[:2] != ('Terraria.ID.ItemID','Search'):
        raise PipelineError('Research Search assignment mismatch')
    if ins[2].operand >> 24 != 43:
        raise PipelineError('Research Search requires generic MethodSpec')
    spec, _ = binary.meta.row(43, ins[2].operand & 0xffffff)
    if spec[0] & 1 != 1:
        raise PipelineError('Research Search Create must be external MemberRef')
    create = binary.member(0x0a000000 | spec[0] >> 1)
    if create[:2] != ('ReLogic.Reflection.IdDictionary','Create') or create[2][:4] != b'\x10\x02\x00\x12':
        raise PipelineError('Research Search generic Create binding mismatch')
    args, _ = binary.meta.blob(spec[1])
    if args[:3] != b'\x0a\x02\x12':
        raise PipelineError('Research Search generic arguments mismatch')
    coded, end = _compressed(args,3)
    if coded & 3 or args[end:] != b'\x06' or binary.type_name(2,coded >> 2) != 'Terraria.ID.ItemID':
        raise PipelineError('Research Search requires ItemID and short')
    typedef = binary.types[coded >> 2]
    definition, _ = binary.meta.row(2,coded >> 2)
    if definition[3] & 3 != 1 or binary.core_type(0x01000000 | definition[3] >> 2) != 'System.Object':
        raise PipelineError('Research ItemID inherited fields unsupported')
    constants = {}
    for rid in range(1,binary.meta.rows[11]+1):
        binary.checkpoint()
        row, offset = binary.meta.row(11,rid)
        if row[1] & 3 == 0 and typedef['firstField'] <= row[1] >> 2 < typedef['lastField']:
            field = row[1] >> 2
            if field in constants:
                raise PipelineError('Research duplicate field Constant')
            constants[field] = row, offset
    names, values, proof = {}, set(), []
    eligible = 0
    for rid in range(typedef['firstField'],typedef['lastField']):
        binary.checkpoint()
        row, offset = binary.meta.row(4,rid)
        signature, _ = binary.meta.blob(row[2])
        if row[0] & 7 != 6 or not row[0] & 0x10 or signature != b'\x06\x06':
            continue
        eligible += 1
        name = binary.meta.string(row[1])
        if len(names) >= binary.limits.names or len(name.encode('utf-8')) > binary.limits.name_bytes:
            raise PipelineError('Research literal name limit')
        if rid == ins[1].operand & 0xffffff:
            value = count
        else:
            if row[0] != 0x8056 or rid not in constants or constants[rid][0][0] != 6:
                raise PipelineError('Research requires literal eligible short fields')
            raw, value_offset = binary.meta.blob(constants[rid][0][2])
            if len(raw) != 2:
                raise PipelineError('Research short constant width mismatch')
            value = struct.unpack('<h',raw)[0]
        if value >= count:
            continue
        if name in names or value in values:
            raise PipelineError('Research reflection dictionary duplicate name or ID')
        names[name] = value; values.add(value)
        proof.append({'name': name,'value': value,'fieldToken': f'0x{0x04000000 | rid:08x}',
                      'fieldMetadataOffset': offset,'valueOffset': value_offset})
    return names, {'count': count, 'eligiblePublicStaticShortFields': eligible,
                   'includedLiteralNames': len(names),'literalBindings': proof,
                   'caseSensitive': True,'negativeIdsAllowed': True,
                   'selection': 'public static fields of exact Int16 type, value < initialized Count',
                   'ordering': 'all included names and IDs are unique; reflection order cannot affect lookup'}


def _dependency_evidence(binary):
    """Bind the separately audited reflection adapter to the embedded bytes.

    This is a fixed-profile audit contract, not a general implementation of CLR
    reflection/LINQ. The contract states concrete branch/call behavior as well as
    method identity; the primary research methods are structurally checked above.
    """
    from .server_semantics import _Metadata, _types, SemanticLimits
    payload, resource = binary.resource('Terraria.Libraries.ReLogic.ReLogic.dll')
    if resource['sha256'] != 'e1c5dccefff5fd1c789ff712babfa1a305fced0d03c96ef30f2c14d99aa0af29':
        raise PipelineError('Research reflection dependency profile mismatch')
    meta = _Metadata(payload, SemanticLimits(), binary.checkpoint)
    audited = [
        (0x9a,'67f9f7401630d6052f74f83db761df261bad818b7feaaec3a8541315eea98068',
         '0:load this; 1:new Dictionary<string,int>() without comparer; 6:store _nameToId; 19:store Count'),
        (0x9c,'a97fd72d86322d0a22202aaaa68d2084d331d23727500cacfe1ca74bdd2b5a1c',
         '1:load _nameToId; 6,7:forward name/out argument; 8:Dictionary.TryGetValue; 13:return'),
        (0xa4,'fb4c560025bba21c3c869b412335ca37c7a73a5807878cc07c7f9fd0207124bb',
         '13:default Count=Int32.MaxValue; 20:GetFields; 39:Count-name predicate; 56:FirstOrDefault; '
         '69:absent branch to 98; 73:GetValue(null); 78:Convert.ToInt32; 85:nonzero branch to 98; '
         '97:throw for zero; 100:new dictionary; 111:BindingFlags=24; 113:GetFields; 119:type predicate; '
         '130:Where; 135:ToList; 141:insertion visitor; 152:ForEach; 236:reverse ToDictionary'),
        (0xa5,'55679e4ef1aadd702c988ee5d9579a6018d81ba589b9f7cd2607770d963690ca',
         '0,10:generic type handles; 5,15:GetTypeFromHandle; 20:Create(Type,Type); 25:return'),
        (0x415,'fd4b46cef5d86ca078c23493c5cdaca7990a41e28e5c75d9abcb466192cede4c',
         '1:FieldInfo.FieldType; 7:requested idType; 12:Type.op_Equality'),
        (0x416,'1b98b42d46b85aa5b85d93c4f9ef2baf0ff65f04a1c19c4f979b78d7f78deef8',
         '2:GetValue(null); 7:Convert.ToInt32; 20:Count; 25:signed bge to return; '
         '33:_nameToId; 39:FieldInfo.Name; 45:Dictionary.Add'),
        (0x419,'eb9c7f8bf8efd9f61b90a3ffc41b9e3e25d86b7c5b8c94f0b7e1e306b6bf13aa',
         '1:FieldInfo.Name; 6:Count literal; 11:String.op_Equality'),
        (0x41a,'af94ff3d8601150f4819c6a9d6951d89be3f641231d730662cebec5cf63a1103',
         '0:address pair argument; 2:KeyValuePair<string,int>.Value; 7:return'),
        (0x41b,'e544dc3f5fd6200d7dc71b2bcfa319d91a527a472dd9b39d4043386a7b812211',
         '0:address pair argument; 2:KeyValuePair<string,int>.Key; 7:return'),
    ]
    methods = []
    for rid, expected, contract in audited:
        binary.checkpoint()
        row, metadata_offset = meta.row(6,rid)
        body = meta.rva(row[0],1)
        first = meta.reader.uint(body,1)
        if first & 3 == 2:
            header,size = 1,first >> 2
        elif first & 3 == 3:
            flags = meta.reader.uint(body,2)
            if flags & ~0xf013 or flags >> 12 != 3:
                raise PipelineError('Research dependency method header mismatch')
            header,size = 12,meta.reader.uint(body+4,4)
        else:
            raise PipelineError('Research dependency method header mismatch')
        if size > 65536:
            raise PipelineError('Research dependency method limit')
        code_offset = meta.rva(row[0]+header,size)
        code = meta.reader.take(code_offset,size)
        if _sha(code) != expected:
            raise PipelineError('Research dependency method audit mismatch')
        decoded = decode_il(code, StaticILLimits(method_instructions=binary.limits.instructions),binary.checkpoint)
        binary.total_instructions += len(decoded)
        if binary.total_instructions > binary.limits.instructions:
            raise PipelineError('Research total instruction limit')
        methods.append({'methodToken':f'0x{0x06000000|rid:08x}','metadataOffset':metadata_offset,
                        'codeOffset':code_offset,'codeBytes':size,'ilSha256':expected,
                        'auditedControlFlow':contract})
    return {'resource':resource,'methods':methods,
            'assurance':'fixed embedded dependency, manually audited reflection/LINQ contract; not generic reflection execution',
            'dictionaryRules':'default string comparer; duplicate names throw; reverse ToDictionary duplicate IDs throw'}


def extract_research_model(meta, types, *, platform='windows', culture='invariant', limits=ResearchLimits(), checkpoint=None):
    """Extract only the exact fixed profile; unsupported inputs produce no tables."""
    check = _research_checkpoint(limits, checkpoint)
    data = meta.reader.data
    if len(data) > limits.input_bytes:
        raise PipelineError('Research input byte limit')
    identity = _sha(data)
    result = {'schemaVersion':1,'family':'creative-research','executedInput':False,
              'publishable':False,'complete':False,'researchModelComplete':False,'culture':culture,'scope':'research base definitions and one-hop persistent ID overrides only',
              'profile':dict(PROFILE),'sourceReference':dict(REFERENCE),
              'input':{'sha256':identity,'bytes':len(data),'platform':platform},
              'unsupportedScope':['other binaries/platforms','runtime modifications','Item.SetDefaults',
                                  'creative menu display/filtering','research progress/player state','non-invariant runtime culture'],
              'assumptions':['fresh catalog; FillResearchItemOverrides applied to a fresh map',
                             'ASCII category casing equivalent to invariant/en-US; binary calls ToLower, not ToLowerInvariant; startup thread culture not established']}
    if platform != PROFILE['platform'] or identity != PROFILE['inputSha256'] or culture != 'invariant':
        result.update(status='UNSUPPORTED_PROFILE',reason='Only the fixed Windows server profile with invariant-culture model is audited')
        json_evidence_size(result, limits.evidence_bytes, check)
        return result
    binary = _Binary(meta,types,limits,check)
    try:
        dependency = _dependency_evidence(binary)
        names,name_evidence = _name_bindings(binary)
        initialize = _verify_initialize(binary)
        helper,helpers = _verify_helpers(binary)
        payload,resource = binary.resource(_RESOURCE)
        if resource['sha256'] != PROFILE['resourceSha256']:
            raise PipelineError('Research catalog resource profile mismatch')
        catalog = parse_research_catalog(payload,names,culture=culture,limits=limits,checkpoint=check)
        method = binary.method(_SAMPLES,'FillResearchItemOverrides',b'\x00\x00\x01')
        ins = method['instructions']
        arrays = {i.operand for i in ins if i.opcode == 0x8d}
        if len(arrays) != 1 or binary.core_type(next(iter(arrays))) != 'System.Int32':
            raise PipelineError('Research initializer array type mismatch')
        calls = {i.operand for i in ins if i.opcode == 0x28 and i.operand != helper}
        if len(calls) != 1:
            raise PipelineError('Research initializer unexpected call set')
        init_array = next(iter(calls))
        owner,name,signature = binary.member(init_array)
        if (owner,name) != ('System.Runtime.CompilerServices.RuntimeHelpers','InitializeArray'):
            raise PipelineError('Research InitializeArray owner mismatch')
        # Signature requires Array and RuntimeFieldHandle, each external core.
        if signature[:4] != b'\x00\x02\x01\x12':
            raise PipelineError('Research InitializeArray signature mismatch')
        array_type,pos = _compressed(signature,4)
        if pos >= len(signature) or signature[pos] != 0x11:
            raise PipelineError('Research InitializeArray handle signature mismatch')
        handle_type,end = _compressed(signature,pos+1)
        if end != len(signature) or array_type & 3 != 1 or handle_type & 3 != 1 or binary.core_type(0x01000000 | array_type >> 2) != 'System.Array' or binary.core_type(0x01000000 | handle_type >> 2) != 'System.RuntimeFieldHandle':
            raise PipelineError('Research InitializeArray core type mismatch')
        overrides = extract_override_initializer(ins,helper,int_type=next(iter(arrays)),initialize_array=init_array,
                     read_rva=lambda token,count:_rva_array(binary,token,count),limits=limits,checkpoint=check)
        result.update(catalog,**overrides,status='EXTRACTED_CONDITIONAL_MODEL',researchModelComplete=True,
                      resource=resource,nameBindingEvidence=name_evidence,dependencyEvidence=dependency,
                      methodEvidence=binary.evidence,controlFlowEvidence={'initialize':initialize,**helpers})
        result['statistics']['persistentOverrideCount'] = len(overrides['persistentIdOverrides'])
        result['statistics']['overrideCallCount'] = len(overrides['overrideCalls'])
        result['statistics']['lookupAvailableCount'] = sum(lookup_research_count(result,value)['available'] for value in names.values())
        result['statistics']['lookupDomainCount'] = len(names)
        result['tableDigests'] = {key:_sha(canonical_json(result[key])) for key in ('baseCounts','persistentIdOverrides')}
        result['completenessBoundary'] = 'research model complete under stated profile/culture/initial-state assumptions; runtime state, item defaults and display definitions not established'
        json_evidence_size(result,limits.evidence_bytes,check)
        return result
    except ILUnsupported as exc:
        raise PipelineError('Unsupported research IL: '+exc.code) from exc


def extract_research_semantics(input_path:Path, *, platform='windows', culture='invariant', limits=ResearchLimits(), checkpoint=None):
    """Read a bounded local PE as data; this API has no producer/export hook."""
    from .server_semantics import _Metadata,_types,SemanticLimits
    from .locale_mapping import _read_input
    check = _research_checkpoint(limits, checkpoint)
    # The shared reader rejects links/nonregular inputs, reads in bounded chunks,
    # checks cancellation, and detects mutation. The outer timer covers every phase.
    data = _read_input(Path(input_path), limits, check)
    check()
    meta = _Metadata(data, SemanticLimits(), check)
    check()
    types = _types(meta)
    check()
    return extract_research_model(meta, types, platform=platform, culture=culture,
                                  limits=limits, checkpoint=check)


def research_summary(model):
    """Explicit allowlist for sharing counts/hashes, without game names/values."""
    result = {key:model[key] for key in ('schemaVersion','family','status','executedInput','publishable',
              'complete','researchModelComplete','culture','assumptions','scope','unsupportedScope')}
    for key, names in (('profile',tuple(PROFILE)), ('sourceReference',tuple(REFERENCE)),
                       ('input',('sha256','bytes','platform'))):
        result[key] = {name:model[key][name] for name in names}
    for key in ('completenessBoundary','reason'):
        if key in model:
            result[key] = model[key]
    if 'statistics' in model:
        names = ('rows','comments','shortRows','unknownNames','recognizedRows','duplicateRecognizedRows',
                 'excludedRows','assignments','distinctRecognizedIds','baseDefinitionCount',
                 'persistentOverrideCount','overrideCallCount','lookupAvailableCount','lookupDomainCount')
        stats = {name:model['statistics'][name] for name in names if name in model['statistics']}
        if any(type(value) is not int or value < 0 for value in stats.values()):
            raise PipelineError('Invalid research summary statistics')
        result['statistics'] = stats
    if 'tableDigests' in model:
        digests = {key:model['tableDigests'][key] for key in ('baseCounts','persistentIdOverrides')}
        if any(type(value) is not str or re.fullmatch('[a-f0-9]{64}',value) is None for value in digests.values()):
            raise PipelineError('Invalid research summary hashes')
        result['tableDigests'] = digests
    if 'resource' in model:
        result['resource'] = {key:model['resource'][key] for key in ('bytes','sha256')}
    return result
