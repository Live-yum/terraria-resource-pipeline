"""Original synthetic PE/CLI fixture; contains no game methods or game data."""
import struct


def token(op, value):
    return bytes((op,)) + struct.pack('<I', value)


def numeric_helper_pe(**options):
    strings = bytearray(b'\0')
    blobs = bytearray(b'\0')
    def text(value):
        index = len(strings)
        strings.extend(value.encode() + b'\0')
        return index
    def blob(value):
        index = len(blobs)
        assert len(value) < 128
        blobs.extend(bytes((len(value),)) + value)
        return index
    # Independently chosen values/operations. The external helper returns bool+7.
    codes = [
        b'\x02\x19\x1f\x0d' + token(0x28, 0x06000006) + b'\x2a',
        b'\x02\x17' + token(0x28, options.get('call_token', 0x06000007)) + token(0x7d, 0x04000001) + b'\x2a',
        b'\x2a', b'\x2a', b'\x2a',
        b'\x02\x03\x18\x58' + token(0x7d, 0x04000001) + b'\x02\x04' + token(0x7d, 0x04000002) + b'\x2a',
        options.get('helper_code', b'\x02\x1d\x58\x2a'),
    ]
    enum_coded = options.get('enum_coded', 16)
    enum_signature = options.get('enum_signature', bytes((0x20, 2, 1, 0x11, enum_coded, 8)))
    method_signatures = [b'\x20\x01\x01\x08'] * 5 + [enum_signature, options.get('helper_signature', b'\x00\x01\x08\x02')]
    field_rows = [
        struct.pack('<HHH', 6, text('OriginalFirst'), blob(b'\x06\x08')),
        struct.pack('<HHH', 6, text('OriginalSecond'), blob(b'\x06\x08')),
        struct.pack('<HHH', options.get('underlying_flags', 0x606), text(options.get('underlying_name', 'value__')),
                    blob(options.get('underlying_signature', b'\x06\x06'))),
        struct.pack('<HHH', options.get('literal_flags', 0x8056), text('OriginalChoice'), blob(options.get('literal_signature', b'\x06\x11\x10'))),
    ]
    if options.get('extra_instance'):
        field_rows.append(struct.pack('<HHH', 6, text('ExtraStorage'), blob(b'\x06\x08')))
    types = [
        struct.pack('<IHHHHH', 0, text('<Module>'), text(''), 0, 1, 1),
        struct.pack('<IHHHHH', 1, text('Item'), text('Terraria'), 0, 1, 1),
        struct.pack('<IHHHHH', options.get('owner_flags', 0x181), text('OriginalArithmetic'), text('Fixture'), 0, 3, 7),
        struct.pack('<IHHHHH', options.get('enum_flags', 0x101), text('OriginalEnum'), text('Fixture'),
                    options.get('extends', 5), 3, 8),
    ]
    methods = []
    for index, signature in enumerate(method_signatures):
        methods.append([0, options.get('impl_flags', 0) if index == 6 else 0,
                        options.get('helper_flags', 0x16) if index == 6 else 6,
                        text(options.get('helper_name', 'OriginalMap') if index == 6 else 'OriginalAssign' if index == 5 else 'SetDefaults' + str(index + 1)), blob(signature), 1])
    rows = {
        0: [struct.pack('<HHHHH', 0, text('Fixture.dll'), 1, 0, 0)],
        1: [struct.pack('<HHH', options.get('base_scope', 6), text(options.get('base_name', 'Enum')), text(options.get('base_namespace', 'System')))],
        2: types, 4: field_rows, 6: [],
        32: [struct.pack('<IHHHHIHHH', 0, 1, 0, 0, 0, 0, 0, text('OriginalFixture'), 0)],
        35: [struct.pack('<HHHHIHHHH', 4, 0, 0, 0, options.get('assembly_flags', 0),
                         blob(options.get('assembly_key', bytes.fromhex('b77a5c561934e089'))), text(options.get('assembly_name', 'mscorlib')), text(options.get('assembly_culture', '')), 0)],
    }
    if 'generic_owner' in options:
        rows[42] = [struct.pack('<HHHH', 0, 0, options['generic_owner'], text('T'))]
    if options.get('field_layout'):
        rows[16] = [struct.pack('<IH', 0, 3)]
    if options.get('class_layout'):
        rows[15] = [struct.pack('<HIH', 0, 4, 4)]
    if 'local_signature' in options:
        rows[17] = [struct.pack('<H', blob(options['local_signature']))]
    def metadata():
        rows[6] = [struct.pack('<IHHHHH', *row) for row in methods]
        tables = struct.pack('<IBBBBQQ', 0, 2, 0, 0, 1, sum(1 << key for key in rows), 0)
        tables += b''.join(struct.pack('<I', len(rows[key])) for key in sorted(rows))
        tables += b''.join(b''.join(rows[key]) for key in sorted(rows))
        streams = {'#~': tables, '#Strings': bytes(strings), '#Blob': bytes(blobs), '#GUID': bytes(16)}
        root = bytearray(struct.pack('<IHHII', 0x424a5342, 1, 1, 0, 12) + b'v4.0.30319\0\0' + struct.pack('<HH', 0, 4))
        cursor = len(root) + sum(8 + ((len(name) + 4) & ~3) for name in streams)
        for name, data in streams.items():
            root.extend(struct.pack('<II', cursor, len(data)))
            encoded = name.encode() + b'\0'
            root.extend(encoded + b'\0' * ((-len(encoded)) % 4))
            cursor += len(data)
        for data in streams.values():
            root.extend(data)
        return bytes(root)
    meta = metadata()
    method_start = (0x300 + len(meta) + 3) & ~3
    bodies = bytearray()
    for index, (row, code) in enumerate(zip(methods, codes)):
        row[0] = 0x2000 + method_start + len(bodies) - 0x200
        local_token = 0x11000001 if index == 6 and 'local_signature' in options else 0
        flags = options.get('header_flags', 0x3013) if index == 6 else 0x3013
        bodies.extend(struct.pack('<HHII', flags, 8, len(code), local_token) + code)
        bodies.extend(b'\0' * ((-len(bodies)) % 4))
    if options.get('no_body'):
        methods[6][0] = 0
    meta = metadata()
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
    put(0x98 + 224, b'.text\0\0\0' + struct.pack('<IIII', size - 0x200, 0x2000, size - 0x200, 0x200))
    put(0x200, struct.pack('<IHHIIIIII', 72, 2, 5, 0x2100, len(meta), 1, 0, 0, 0))
    put(0x300, meta)
    put(method_start, bodies)
    return bytes(output)
