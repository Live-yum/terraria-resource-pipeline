"""Original non-executable PE/CLI fixture with synthetic paint input colors.

The fixture is hand-built test data, with no imported game code or color table.
Its intentionally incomplete PE headers make it suitable for static parsing only.
"""
import struct


WHITE_TOKEN = 0x0a000001
SETTER_TOKENS = {channel: 0x0a000002 + index
                 for index, channel in enumerate('RGBA')}


def token(op, value):
    return bytes((op,)) + struct.pack('<I', value)


def call(value=WHITE_TOKEN):
    return token(0x28, value)


def ldc(value):
    return b'\x20' + struct.pack('<i', value)


def setter(channel, value, local=0):
    """Load a Color local address, then invoke one synthetic byte setter."""
    target = SETTER_TOKENS[channel] if isinstance(channel, str) else channel
    return bytes((0x12, local)) + ldc(value) + call(target)


class BranchEmitter:
    """Small IL emitter with named labels and checked short branch fixups."""

    def __init__(self):
        self.code = bytearray()
        self.labels = {}
        self.fixups = []

    def emit(self, *chunks):
        for chunk in chunks:
            self.code.extend(chunk)
        return self

    def label(self, name):
        if name in self.labels:
            raise ValueError('duplicate branch label: ' + name)
        self.labels[name] = len(self.code)
        return self

    def branch(self, opcode, label):
        if isinstance(opcode, str):
            opcode = {'br.s': 0x2b, 'beq.s': 0x2e,
                      'bne.un.s': 0x33}[opcode]
        self.emit(bytes((opcode, 0)))
        self.fixups.append((len(self.code) - 1, label))
        return self

    def finish(self):
        result = bytearray(self.code)
        for position, label in self.fixups:
            displacement = self.labels[label] - position - 1
            if not -128 <= displacement <= 127:
                raise ValueError('short branch is out of range: ' + label)
            result[position] = displacement & 0xff
        return bytes(result)


def paint_input_code():
    """Return White by default, one RGB pair, and one explicit RGBA case."""
    il = BranchEmitter()
    il.emit(call(), b'\x0a', b'\x02\x0b')  # White -> local 0; argument -> local 1.
    il.emit(b'\x07', ldc(1)).branch('beq.s', 'pair')
    il.emit(b'\x07', ldc(13)).branch('bne.un.s', 'check_30')
    il.label('pair')
    for channel, value in zip('RGB', (11, 23, 47)):
        il.emit(setter(channel, value))
    il.label('check_30').emit(b'\x07', ldc(30))
    il.branch('bne.un.s', 'return')
    for channel, value in zip('RGBA', (89, 101, 113, 127)):
        il.emit(setter(channel, value))
    il.label('return').emit(b'\x06\x2a')
    return il.finish()


def paint_input_pe(*, code=None, assembly_name='OriginalPaintFixture',
                   assembly_version=(1, 0, 0, 0), assembly_flags=0,
                   assembly_public_key=b'', assembly_culture='',
                   assembly_hash_algorithm=0x8004,
                   owner_name='WorldGen', owner_ns='Terraria',
                   owner_flags=0x100001, owner_extends=0,
                   type_name='Color', type_ns='Microsoft.Xna.Framework',
                   scope=6, assembly_ref_name='Microsoft.Xna.Framework',
                   assembly_ref_version=(4, 0, 0, 0),
                   assembly_ref_token=bytes.fromhex('842cf8be1de50553'),
                   assembly_ref_culture='', assembly_ref_flags=0,
                   assembly_ref_hash=b'', method_name='paintColor',
                   method_signature=b'\x00\x01\x11\x05\x08',
                   method_flags=0x96, impl_flags=0,
                   local_signature=b'\x07\x02\x11\x05\x08',
                   header_flags=0x3013, max_stack=2,
                   local_token=0x11000001,
                   member_names=None, member_signatures=None,
                   member_parents=None, duplicate_owner=False,
                   duplicate_method=False, no_body=False,
                   with_offsets=False):
    """Build a bounded fixture, optionally returning (bytes, mutation offsets).

    Member sequences contain White, R, G, B, and A in that order. Shorter or
    longer sequences deliberately create malformed/extra-member test metadata.
    Passing local_signature=None omits the StandAloneSig table. All offsets in
    the optional mapping are absolute file offsets, except explicitly named
    heap indexes and RVA values.
    """
    strings = bytearray(b'\0')
    blobs = bytearray(b'\0')

    def text(value):
        if not value:
            return 0
        index = len(strings)
        strings.extend(value.encode() + b'\0')
        return index

    def blob(value):
        if not value:
            return 0
        index = len(blobs)
        if len(value) >= 128:
            raise ValueError('fixture blobs must fit a one-byte length')
        blobs.extend(bytes((len(value),)) + value)
        return index

    if code is None:
        code = paint_input_code()
    code = bytes(code)
    if len(code) > 65536:
        raise ValueError('fixture code exceeds its test-data bound')
    if member_names is None:
        member_names = ('get_White', 'set_R', 'set_G', 'set_B', 'set_A')
    if member_signatures is None:
        member_signatures = (b'\x00\x00\x11\x05',) + (b'\x20\x01\x01\x05',) * 4
    if member_parents is None:
        member_parents = (9,) * 5  # MemberRefParent: TypeRef row 1.
    if not len(member_names) == len(member_signatures) == len(member_parents):
        raise ValueError('member metadata sequences must have equal lengths')
    if len(member_names) > 64:
        raise ValueError('too many fixture members')

    method_signature_index = blob(method_signature)
    local_signature_index = None if local_signature is None else blob(local_signature)
    methods = [[0, impl_flags, method_flags, text(method_name),
                method_signature_index, 1]]
    if duplicate_method:
        methods.append(list(methods[0]))
    owner_name_index, owner_namespace_index = text(owner_name), text(owner_ns)
    types = [
        struct.pack('<IHHHHH', 0, text('<Module>'), 0, 0, 1, 1),
        struct.pack('<IHHHHH', owner_flags, owner_name_index,
                    owner_namespace_index, owner_extends, 1, 1),
    ]
    if duplicate_owner:
        types.append(struct.pack('<IHHHHH', owner_flags, owner_name_index,
                                 owner_namespace_index, owner_extends,
                                 1, len(methods) + 1))
    member_signature_indexes = []
    members = []
    for name, signature, parent in zip(member_names, member_signatures, member_parents):
        index = blob(signature)
        member_signature_indexes.append(index)
        members.append(struct.pack('<HHH', parent, text(name), index))
    rows = {
        0: [struct.pack('<HHHHH', 0, text('OriginalPaintFixture.dll'), 1, 0, 0)],
        1: [struct.pack('<HHH', scope, text(type_name), text(type_ns))],
        2: types,
        6: [],
        10: members,
        32: [struct.pack('<IHHHHIHHH', assembly_hash_algorithm,
                         *assembly_version, assembly_flags,
                         blob(assembly_public_key), text(assembly_name),
                         text(assembly_culture))],
        35: [struct.pack('<HHHHIHHHH', *assembly_ref_version,
                         assembly_ref_flags, blob(assembly_ref_token),
                         text(assembly_ref_name), text(assembly_ref_culture),
                         blob(assembly_ref_hash))],
    }
    if local_signature is not None:
        rows[17] = [struct.pack('<H', local_signature_index)]
    if len(strings) >= 65536 or len(blobs) >= 65536:
        raise ValueError('fixture heaps must use two-byte indexes')

    def metadata():
        rows[6] = [struct.pack('<IHHHHH', *row) for row in methods]
        tables = bytearray(struct.pack('<IBBBBQQ', 0, 2, 0, 0, 1,
                                       sum(1 << key for key in rows), 0))
        tables.extend(b''.join(struct.pack('<I', len(rows[key])) for key in sorted(rows)))
        row_offsets = {}
        for key in sorted(rows):
            row_offsets[key] = []
            for row in rows[key]:
                row_offsets[key].append(len(tables))
                tables.extend(row)
        streams = {'#~': bytes(tables), '#Strings': bytes(strings),
                   '#Blob': bytes(blobs), '#GUID': bytes(16)}
        root = bytearray(struct.pack('<IHHII', 0x424a5342, 1, 1, 0, 12)
                         + b'v4.0.30319\0\0' + struct.pack('<HH', 0, 4))
        cursor = len(root) + sum(8 + ((len(name) + 4) & ~3) for name in streams)
        stream_offsets = {}
        for name, data in streams.items():
            stream_offsets[name] = cursor
            root.extend(struct.pack('<II', cursor, len(data)))
            encoded = name.encode() + b'\0'
            root.extend(encoded + b'\0' * ((-len(encoded)) % 4))
            cursor += len(data)
        for data in streams.values():
            root.extend(data)
        return bytes(root), stream_offsets, row_offsets

    meta, _, _ = metadata()
    method_start = (0x300 + len(meta) + 3) & ~3
    bodies = bytearray()
    body_offsets = []
    for row in methods:
        body_offsets.append(method_start + len(bodies))
        row[0] = 0 if no_body else 0x2000 + body_offsets[-1] - 0x200
        bodies.extend(struct.pack('<HHII', header_flags, max_stack,
                                  len(code), local_token) + code)
        bodies.extend(b'\0' * ((-len(bodies)) % 4))
    meta, stream_offsets, row_offsets = metadata()
    size = ((method_start + len(bodies) + 511) // 512) * 512
    output = bytearray(size)

    def put(offset, value):
        output[offset:offset + len(value)] = value

    put(0, b'MZ')
    put(0x3c, struct.pack('<I', 0x80))
    put(0x80, b'PE\0\0')
    put(0x84, struct.pack('<HHIIIHH', 0x14c, 1, 0, 0, 0, 224, 0x102))
    put(0x98, struct.pack('<H', 0x10b))
    directory = 0x98 + 96
    put(directory - 4, struct.pack('<I', 16))
    put(directory + 14 * 8, struct.pack('<II', 0x2000, 72))
    put(0x98 + 224, b'.text\0\0\0' + struct.pack('<IIII',
        size - 0x200, 0x2000, size - 0x200, 0x200))
    put(0x200, struct.pack('<IHHIIIIII', 72, 2, 5, 0x2100, len(meta), 1, 0, 0, 0))
    put(0x300, meta)
    put(method_start, bodies)
    if not with_offsets:
        return bytes(output)
    absolute_streams = {name: 0x300 + offset for name, offset in stream_offsets.items()}
    table_start = absolute_streams['#~']
    blob_start = absolute_streams['#Blob']
    offsets = {
        'metadata': 0x300, 'streams': absolute_streams,
        'rows': {key: [table_start + offset for offset in values]
                 for key, values in row_offsets.items()},
        'method_header': body_offsets[0], 'method_headers': body_offsets,
        'code': body_offsets[0] + 12,
        'codes': [offset + 12 for offset in body_offsets],
        'method_rva': methods[0][0],
        'method_signature': blob_start + method_signature_index + 1,
        'local_signature': None if local_signature_index is None else
                           blob_start + local_signature_index + 1,
        'member_signatures': [blob_start + index + 1
                              for index in member_signature_indexes],
    }
    return bytes(output), offsets
