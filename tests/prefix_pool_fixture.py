"""Original synthetic PE/CLI prefix-pool fixture; no game bytes or tables."""
import struct

from resource_pipeline.item_assembler import POOLS


VALUES = ((9, 1), (2,), (7, 4), (3, 6), (5,), (8, 2), (10, 4), (11,))


def token(opcode, value):
    return bytes((opcode,)) + struct.pack('<I', value)


def ldc(value):
    return b'\x20' + struct.pack('<i', value)


def pool_body(values=VALUES, *, inline=False):
    result = b''
    for index, values in enumerate(values):
        result += ldc(len(values)) + token(0x8d, 0x01000002)
        if inline:
            for at, value in enumerate(values):
                result += b'\x25' + ldc(at) + ldc(value) + b'\x9e'
        elif values:
            result += b'\x25' + token(0xd0, 0x0400000a + index) + token(0x28, 0x0a000001)
        result += token(0x80, 0x04000002 + index)
    return result + b'\x2a'


def fixture(**options):
    strings, blobs = bytearray(b'\0'), bytearray(b'\0')
    def text(value):
        at = len(strings); strings.extend(value.encode() + b'\0'); return at
    def blob(value):
        assert len(value) < 128
        at = len(blobs); blobs.extend(bytes((len(value),)) + value); return at
    values = options.get('values', VALUES)
    assert len(values) == len(POOLS)
    payloads = [b''.join(struct.pack('<i', n) for n in ids) for ids in values]
    codes = [options.get('count_code', ldc(options.get('count', 13)) + token(0x80, 0x04000001) + b'\x2a'),
             options.get('pool_code', pool_body(values, inline=options.get('inline', False)))]
    specs = [('Count', b'\x06\x08', 0x36)]
    specs += [(name, b'\x06\x1d\x08', 0x16) for name in POOLS]
    specs += [('OriginalData' + str(n), b'\x06\x11' + bytes(((6 + n) << 2,)), 0x111) for n in range(8)]
    fields = [struct.pack('<HHH', options.get('field_flags', {}).get(i, flags),
                          text(options.get('field_names', {}).get(i, name)),
                          blob(options.get('field_signatures', {}).get(i, signature)))
              for i, (name, signature, flags) in enumerate(specs, 1)]
    type_specs = [('<Module>', '', 0, 0, 1, 1), ('PrefixID', 'Terraria.ID', 1, 5, 1, 1),
                  ('PrefixLegacy', 'Terraria.GameContent.Prefixes', 1, 5, 2, 2),
                  ('Prefixes', '', 2, 5, 2, 2), ('OriginalPayloads', 'Fixture', 0x180, 5, 10, 3)]
    type_specs += [('OriginalBlock' + str(n), 'Fixture', 0x110, 25, 18, 3) for n in range(8)]
    methods = [[0, 0, options.get('method_flags', {}).get(i, 0x1891), text('.cctor'),
                blob(options.get('signatures', {}).get(i, b'\x00\x00\x01')), 1] for i in (1, 2)]
    refs = ['Object', 'Int32', 'Array', 'RuntimeFieldHandle', 'RuntimeHelpers', 'ValueType']
    rows = {
        0: [struct.pack('<HHHHH', 0, text('OriginalPrefixPools.dll'), 1, 0, 0)],
        1: [struct.pack('<HHH', 6, text(options.get('ref_names', {}).get(i, name)),
                        text('System.Runtime.CompilerServices' if name == 'RuntimeHelpers' else 'System'))
            for i, name in enumerate(refs, 1)],
        2: [struct.pack('<IHHHHH', options.get('type_flags', {}).get(i, flags),
                        text(name), text(namespace), base,
                        options.get('type_first_fields', {}).get(i, first_field), first_method)
            for i, (name, namespace, flags, base, first_field, first_method) in enumerate(type_specs, 1)],
        4: fields, 6: [],
        10: [struct.pack('<HHH', options.get('initialize_owner', 41),
                         text(options.get('initialize_name', 'InitializeArray')),
                         blob(options.get('initialize_signature', b'\x00\x02\x01\x12\x0d\x11\x11')))],
        15: [struct.pack('<HIH', options.get('layout_pack', {}).get(n, 1),
                         len(raw) + options.get('layout_delta', {}).get(n, 0), 6 + n)
             for n, raw in enumerate(payloads)],
        29: [struct.pack('<IH', 0, 10 + n) for n in range(8)],
        32: [struct.pack('<IHHHHIHHH', 0, 1, 0, 0, 0, 0, 0, text('OriginalPrefixPools'), 0)],
        35: [struct.pack('<HHHHIHHHH', 4, 0, 0, 0, options.get('assembly_flags', 0),
                         blob(options.get('key', bytes.fromhex('b77a5c561934e089'))),
                         text(options.get('assembly_name', 'mscorlib')), 0, 0)],
        41: [struct.pack('<HH', 4, 3)],
    }
    if options.get('local'):
        rows[17] = [struct.pack('<H', blob(b'\x07\x01\x08'))]
    def metadata():
        rows[6] = [struct.pack('<IHHHHH', *row) for row in methods]
        tables = struct.pack('<IBBBBQQ', 0, 2, 0, 0, 1, sum(1 << k for k in rows), 0)
        tables += b''.join(struct.pack('<I', len(rows[k])) for k in sorted(rows))
        tables += b''.join(b''.join(rows[k]) for k in sorted(rows))
        streams = {'#~': tables, '#Strings': bytes(strings), '#Blob': bytes(blobs), '#GUID': bytes(16)}
        root = bytearray(struct.pack('<IHHII', 0x424a5342, 1, 1, 0, 12) + b'v4.0.30319\0\0' + struct.pack('<HH', 0, 4))
        cursor = len(root) + sum(8 + ((len(name) + 4) & ~3) for name in streams)
        for name, raw in streams.items():
            root.extend(struct.pack('<II', cursor, len(raw))); encoded = name.encode() + b'\0'
            root.extend(encoded + b'\0' * (-len(encoded) % 4)); cursor += len(raw)
        for raw in streams.values(): root.extend(raw)
        return bytes(root)
    meta = metadata(); start = (0x300 + len(meta) + 3) & ~3; bodies = bytearray()
    for i, code in enumerate(codes, 1):
        methods[i - 1][0] = 0x2000 + start + len(bodies) - 0x200
        local = 0x11000001 if options.get('local') == i else 0
        bodies.extend(struct.pack('<HHII', options.get('header_flags', {}).get(i, 0x3013),
                                  options.get('maxstack', {}).get(i, 8), len(code), local) + code)
        bodies.extend(b'\0' * (-len(bodies) % 4))
    data_start = start + len(bodies); cursor = data_start
    for n, raw in enumerate(payloads):
        rows[29][n] = struct.pack('<IH', 0x2000 + cursor - 0x200, 10 + n); cursor += len(raw)
    meta = metadata(); size = (cursor + 511) // 512 * 512; output = bytearray(size)
    def put(at, value): output[at:at + len(value)] = value
    put(0, b'MZ'); put(0x3c, struct.pack('<I', 0x80)); put(0x80, b'PE\0\0')
    put(0x84, struct.pack('<HHIIIHH', 0x14c, 1, 0, 0, 0, 224, 0x102)); put(0x98, struct.pack('<H', 0x10b))
    directory = 0x98 + 96; put(directory - 4, struct.pack('<I', 16)); put(directory + 14 * 8, struct.pack('<II', 0x2000, 72))
    put(0x98 + 224, b'.text\0\0\0' + struct.pack('<IIII', size - 0x200, 0x2000, size - 0x200, 0x200))
    put(0x200, struct.pack('<IHHIIIIII', 72, 2, 5, 0x2100, len(meta), 1, 0, 0, 0))
    put(0x300, meta); put(start, bodies); put(data_start, b''.join(payloads))
    return bytes(output)
