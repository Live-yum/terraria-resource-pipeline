"""Original synthetic field-add PE/CLI fixture; no executable or game table copied."""
import struct


FIELDS = [('statDefense', 8), ('statManaMax2', 8), ('meleeCrit', 8), ('rangedCrit', 8), ('magicCrit', 8),
          ('meleeDamage', 12), ('rangedDamage', 12), ('magicDamage', 12), ('minionDamage', 12),
          ('moveSpeed', 12), ('meleeSpeed', 12)]


def token(opcode, value): return bytes([opcode]) + struct.pack('<I', value)


def body(cases=None):
    if cases is None: cases = [(3, [('statDefense', 2)]), (4, [('moveSpeed', 0.03)])]
    fields = {name: (0x04000002 + index, kind) for index, (name, kind) in enumerate(FIELDS)}
    result = bytearray()
    for prefix, updates in cases:
        result += b'\x03' + token(0x7b, 0x04000001) + b'\x20' + struct.pack('<i', prefix)
        branch = len(result); result += b'\x40\0\0\0\0'
        for name, value in updates:
            identity, kind = fields[name]
            result += b'\x02\x02' + token(0x7b, identity)
            result += b'\x22' + struct.pack('<f', value) if kind == 12 else b'\x20' + struct.pack('<i', value)
            result += b'\x58' + token(0x7d, identity)
        struct.pack_into('<i', result, branch + 1, len(result) - branch - 5)
    return bytes(result) + b'\x2a'


def fixture(code=None, *, explicit_layout=False, field_names=None, field_flags=6, signature=b'\x20\x01\x01\x12\x08'):
    code = body() if code is None else code
    strings, blobs = bytearray(b'\0'), bytearray(b'\0')
    def s(text):
        at = len(strings); strings.extend(text.encode() + b'\0'); return at
    def b(data):
        at = len(blobs); blobs.extend(bytes([len(data)]) + data); return at
    fields = [struct.pack('<HHH', 6, s('prefix'), b(b'\x06\x05'))]
    for index, (name, kind) in enumerate(FIELDS):
        fields.append(struct.pack('<HHH', field_flags, s(field_names.get(index, name) if field_names else name), b(bytes((6, kind)))))
    rows = {
        0: [struct.pack('<HHHHH', 0, s('Original.dll'), 1, 0, 0)],
        2: [struct.pack('<IHHHHH', 0, s('<Module>'), s(''), 0, 1, 1),
            struct.pack('<IHHHHH', 0, s('Item'), s('Terraria'), 0, 1, 1),
            struct.pack('<IHHHHH', 0x10 if explicit_layout else 0, s('Player'), s('Terraria'), 0, 2, 1)],
        4: fields, 6: [],
        32: [struct.pack('<IHHHHIHHH', 0, 2, 3, 4, 5, 0, 0, s('OriginalPrefixFixture'), 0)]}
    method = [0, 0, 0x86, s('GrantPrefixBenefits'), b(signature), 1]
    def metadata():
        rows[6] = [struct.pack('<IHHHHH', *method)]
        tables = struct.pack('<IBBBBQQ', 0, 2, 0, 0, 1, sum(1 << k for k in rows), 0)
        tables += b''.join(struct.pack('<I', len(rows[k])) for k in sorted(rows))
        tables += b''.join(b''.join(rows[k]) for k in sorted(rows))
        streams = {'#~': tables, '#Strings': bytes(strings), '#Blob': bytes(blobs), '#GUID': bytes(16)}
        root = bytearray(struct.pack('<IHHII', 0x424a5342, 1, 1, 0, 12) + b'v4.0.30319\0\0' + struct.pack('<HH', 0, 4))
        cursor = len(root) + sum(8 + ((len(n) + 4) & ~3) for n in streams)
        for name, raw in streams.items():
            root.extend(struct.pack('<II', cursor, len(raw))); value = name.encode() + b'\0'
            root.extend(value + b'\0' * (-len(value) % 4)); cursor += len(raw)
        for raw in streams.values(): root.extend(raw)
        return bytes(root)
    meta = metadata(); start = (0x300 + len(meta) + 3) & ~3
    method[0] = 0x2000 + start - 0x200
    meta = metadata(); raw_body = struct.pack('<HHII', 0x3013, 8, len(code), 0) + code
    size = (start + len(raw_body) + 511) // 512 * 512; out = bytearray(size)
    def put(offset, data): out[offset:offset + len(data)] = data
    put(0, b'MZ'); put(0x3c, struct.pack('<I', 0x80)); put(0x80, b'PE\0\0')
    put(0x84, struct.pack('<HHIIIHH', 0x14c, 1, 0, 0, 0, 224, 0x102)); put(0x98, struct.pack('<H', 0x10b))
    directory = 0x98 + 96; put(directory - 4, struct.pack('<I', 16)); put(directory + 14 * 8, struct.pack('<II', 0x2000, 72))
    put(0x98 + 224, b'.text\0\0\0' + struct.pack('<IIII', size - 0x200, 0x2000, size - 0x200, 0x200))
    put(0x200, struct.pack('<IHHIIIIII', 72, 2, 5, 0x2100, len(meta), 1, 0, 0, 0))
    put(0x300, meta); put(start, raw_body)
    return bytes(out)
