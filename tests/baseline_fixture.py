"""Original synthetic metadata/IL; no game code, values, assets or CLR execution."""
import struct


def token(op, value):
    prefix = bytes((op,)) if op < 256 else bytes((0xfe, op & 255))
    return prefix + struct.pack('<I', value)


def ldc(value):
    return b'\x20' + struct.pack('<i', value)


def put_field(field, value):
    return b'\x02' + value + token(0x7d, 0x04000000 | field)


def baseline_pe(**options):
    strings, blobs = bytearray(b'\0'), bytearray(b'\0')
    def text(value):
        index = len(strings); strings.extend(value.encode() + b'\0'); return index
    def blob(value):
        index = len(blobs); assert len(value) < 128
        blobs.extend(bytes((len(value),)) + value); return index
    nullable = b'\x15\x11\x09\x01\x08'
    ctor = b''.join(put_field(12 + n, ldc(n + 2) + ldc(7) + token(0x28, 0x06000003)) for n in range(7))
    ctor += put_field(4, ldc(4)) + put_field(6, b'\x22' + struct.pack('<f', 1.25))
    ctor += put_field(11, token(0x7e, 0x04000014))
    ctor += b'\x02' + token(0x28, options.get('base_call', 0x0a000001)) + b'\x2a'
    reset = put_field(4, b'\x03') + put_field(5, b'\x16') + put_field(7, b'\x14')
    reset += b'\x02' + token(0x7c, 0x04000008) + token(0xfe15, 0x1b000001)
    reset += b'\x02' + token(0x7c, 0x04000009) + token(0xfe15, 0x02000004)
    reset += b'\x02\x14' + token(0x28, 0x06000004)
    reset += put_field(10, token(0x7e, 0x04000013)) + put_field(11, token(0x7e, 0x04000014)) + b'\x2a'
    codes = [options.get('ctor_code', ctor), options.get('reset_code', reset),
             options.get('helper_code', b'\x02' + ldc(11) + b'\x5a\x03\x58\x2a'),
             options.get('setter_code', b'\x02\x03' + token(0x7d, 0x04000007) + b'\x2a'),
             options.get('item_cctor', ldc(83) + token(0x80, 0x04000013) + b'\x2a'),
             options.get('typed_ctor', b'\x2a'),
             options.get('scalar_cctor', ldc(37) + token(0x80, 0x04000014) + b'\x2a')]
    field_specs = [('width', b'\x08'), ('height', b'\x08'), ('stringColor', b'\x08'),
                   ('OriginalCounter', b'\x08'), ('OriginalFlag', b'\x02'), ('OriginalRatio', b'\x0c'),
                   ('OriginalReference', b'\x12\x05'), ('OriginalOptional', nullable),
                   ('OriginalOpaque', b'\x11\x10'), ('OriginalMaximum', b'\x08'), ('OriginalCategory', b'\x08')]
    field_specs += [('OriginalCost' + str(n), b'\x08') for n in range(7)]
    field_specs += [('OriginalStaticMaximum', b'\x08'), ('OriginalStaticCategory', b'\x08'), ('OriginalFactory', b'\x12\x05')]
    fields = []
    for number, (name, signature) in enumerate(field_specs, 1):
        flags = 0x16 if number >= 19 else 0x26 if 12 <= number <= 18 else 6
        signature = options.get('field_signatures', {}).get(number, b'\x06' + signature)
        flags = options.get('field_flags', {}).get(number, flags)
        fields.append(struct.pack('<HHH', flags, text(name), blob(signature)))
    types = [
        struct.pack('<IHHHHH', 0, text('<Module>'), text(''), 0, 1, 1),
        struct.pack('<IHHHHH', options.get('item_flags', 1), text('Item'), text('Terraria'), options.get('base_coded', 5), 1, 1),
        struct.pack('<IHHHHH', 0x181, text('OriginalScalars'), text('Fixture'), 5, 20, options.get('scalar_first_method', 7)),
        struct.pack('<IHHHHH', 0x109, text('OriginalOpaque'), text('Fixture'), 13, 22, 8),
    ]
    names = ['.ctor', 'ResetStats', 'OriginalPrice', 'OriginalSetReference', '.cctor', '.ctor', '.cctor']
    signatures = [b'\x20\x00\x01', b'\x20\x01\x01\x08', b'\x00\x02\x08\x08\x08',
                  b'\x20\x01\x01\x12\x05', b'\x00\x00\x01', b'\x20\x01\x01\x08', b'\x00\x00\x01']
    flags = [0x1886, 0x86, 0x96, 0x881, 0x1891, 0x1886, 0x1891]
    methods = []
    for index in range(len(codes)):
        methods.append([0, options.get('method_impl', {}).get(index + 1, 0),
                        options.get('method_flags', {}).get(index + 1, flags[index]),
                        text(options.get('method_names', {}).get(index + 1, names[index])),
                        blob(options.get('method_signatures', {}).get(index + 1, signatures[index])), 1])
    rows = {
        0: [struct.pack('<HHHHH', 0, text('OriginalBaseline.dll'), 1, 0, 0)],
        1: [struct.pack('<HHH', options.get('base_scope', 6), text(options.get('base_name', 'Object')), text(options.get('base_namespace', 'System'))),
            struct.pack('<HHH', 6, text('Nullable`1'), text('System')),
            struct.pack('<HHH', 6, text('ValueType'), text('System'))],
        2: types, 4: fields, 6: [],
        10: [struct.pack('<HHH', options.get('member_parent', 9), text(options.get('member_name', '.ctor')),
                         blob(options.get('member_signature', b'\x20\x00\x01'))),
             struct.pack('<HHH', 9, text('OriginalUnknownEffect'), blob(b'\x00\x00\x01'))],
        27: [struct.pack('<H', blob(options.get('typespec', nullable)))],
        32: [struct.pack('<IHHHHIHHH', 0, 1, 0, 0, 0, 0, 0, text('OriginalBaseline'), 0)],
        35: [struct.pack('<HHHHIHHHH', 4, 0, 0, 0, options.get('assembly_flags', 0),
                         blob(options.get('assembly_key', bytes.fromhex('b77a5c561934e089'))),
                         text(options.get('assembly_name', 'mscorlib')), text(options.get('assembly_culture', '')), 0)],
    }
    if 'generic_owner' in options:
        rows[42] = [struct.pack('<HHHH', 0, 0, options['generic_owner'], text('T'))]
    if options.get('field_layout'):
        rows[16] = [struct.pack('<IH', 0, 1)]
    if options.get('class_layout'):
        rows[15] = [struct.pack('<HIH', 0, 4, 2)]
    if 'locals' in options:
        rows[17] = [struct.pack('<H', blob(options['locals']))]
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
            encoded = name.encode() + b'\0'; root.extend(encoded + b'\0' * ((-len(encoded)) % 4)); cursor += len(data)
        for data in streams.values():
            root.extend(data)
        return bytes(root)
    meta = metadata(); method_start = (0x300 + len(meta) + 3) & ~3; bodies = bytearray()
    for index, (row, code) in enumerate(zip(methods, codes), 1):
        row[0] = 0x2000 + method_start + len(bodies) - 0x200
        local_token = 0x11000001 if index == 2 and 'locals' in options else 0
        header_flags = options.get('header_flags', {}).get(index, 0x3013)
        max_stack = options.get('max_stack', {}).get(index, 16)
        bodies.extend(struct.pack('<HHII', header_flags, max_stack, len(code), local_token) + code)
        bodies.extend(b'\0' * ((-len(bodies)) % 4))
    meta = metadata(); size = ((method_start + len(bodies) + 511) // 512) * 512; output = bytearray(size)
    def put(offset, value):
        output[offset:offset + len(value)] = value
    put(0, b'MZ'); put(0x3c, struct.pack('<I', 0x80)); put(0x80, b'PE\0\0')
    put(0x84, struct.pack('<HHIIIHH', 0x14c, 1, 0, 0, 0, 224, 0x102)); put(0x98, struct.pack('<H', 0x10b))
    directory = 0x98 + 96; put(directory - 4, struct.pack('<I', 16)); put(directory + 14 * 8, struct.pack('<II', 0x2000, 72))
    put(0x98 + 224, b'.text\0\0\0' + struct.pack('<IIII', size - 0x200, 0x2000, size - 0x200, 0x200))
    put(0x200, struct.pack('<IHHIIIIII', 72, 2, 5, 0x2100, len(meta), 1, 0, 0, 0))
    put(0x300, meta); put(method_start, bodies)
    return bytes(output)
