"""Original finite coefficient IL, independently assembled for proof rejection tests."""
import struct
from resource_pipeline.prefix_coefficient_semantics import SIGNATURE


def tok(op, value): return bytes((op,)) + struct.pack('<I', value)
def arg(n): return bytes((0x0e, n))
def integer(n): return b'\x20' + struct.pack('<i', n)
def single(n): return b'\x22' + struct.pack('<f', n)


def code(*, extra_tail=b'', change=1.25, missing=None):
    prefix = b''.join(arg(n) + (single(1) + b'\x56' if n < 8 else integer(0) + b'\x54') for n in range(2, 11) if n != missing)
    case = arg(2) + single(change) + b'\x56'
    prefix += b'\x03' + integer(2) + b'\x40' + struct.pack('<i', len(case)) + case
    tail = arg(11) + single(1) + b'\x56' + extra_tail
    # Independent tail field read + trusted non-mutating Round call. Both return
    # branches must be checked even though the item state is not interpreted.
    tail += b'\x02' + tok(0x7b, 0x04000002) + b'\x6c' + tok(0x28, 0x0a000001) + single(0)
    tail += b'\x2e\x02\x16\x2a\x17\x2a'
    return prefix + tail


def fixture(body=None, *, maxstack=8, signature=SIGNATURE, core_key=b'\xb7\x7a\x5c\x56\x19\x34\xe0\x89', round_name='Round'):
    code_bytes = code() if body is None else body
    strings, blobs = bytearray(b'\0'), bytearray(b'\0')
    def s(value):
        at = len(strings); strings.extend(value.encode() + b'\0'); return at
    def b(value):
        at = len(blobs); blobs.extend(bytes((len(value),)) + value); return at
    rows = {0: [struct.pack('<HHHHH', 0, s('Original.dll'), 1, 0, 0)],
            1: [struct.pack('<HHH', 6, s('Math'), s('System'))],
            2: [struct.pack('<IHHHHH', 0, s('<Module>'), s(''), 0, 1, 1),
                struct.pack('<IHHHHH', 0, s('PrefixID'), s('Terraria.ID'), 0, 1, 1),
                struct.pack('<IHHHHH', 0, s('Item'), s('Terraria'), 0, 2, 2)],
            4: [struct.pack('<HHH', 0x36, s('Count'), b(b'\x06\x08')),
                struct.pack('<HHH', 6, s('damage'), b(b'\x06\x08'))],
            6: [], 10: [struct.pack('<HHH', 9, s(round_name), b(b'\x00\x01\x0d\x0d'))],
            32: [struct.pack('<IHHHHIHHH', 0, 2, 3, 4, 5, 0, 0, s('OriginalCoefficients'), 0)],
            35: [struct.pack('<HHHHIHHHH', 4, 0, 0, 0, 0, b(core_key), s('mscorlib'), 0, 0)]}
    methods = [[0, 0, 0x1891, s('.cctor'), b(b'\x00\x00\x01'), 1],
               [0, 0, 0x86, s('TryGetPrefixStatMultipliersForItem'), b(signature), 1]]
    bodies = [integer(4) + tok(0x80, 0x04000001) + b'\x2a', code_bytes]
    def metadata():
        rows[6] = [struct.pack('<IHHHHH', *row) for row in methods]
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
    meta = metadata(); start = (0x300 + len(meta) + 3) & ~3; packed = bytearray()
    for method, body in zip(methods, bodies):
        method[0] = 0x2000 + start + len(packed) - 0x200
        packed.extend(struct.pack('<HHII', 0x3013, maxstack, len(body), 0) + body)
        packed.extend(bytes(-len(packed) % 4))
    meta = metadata(); size = (start + len(packed) + 511) // 512 * 512; result = bytearray(size)
    def put(at, value): result[at:at + len(value)] = value
    put(0, b'MZ'); put(0x3c, struct.pack('<I', 0x80)); put(0x80, b'PE\0\0')
    put(0x84, struct.pack('<HHIIIHH', 0x14c, 1, 0, 0, 0, 224, 0x102)); put(0x98, struct.pack('<H', 0x10b))
    directory = 0x98 + 96; put(directory - 4, struct.pack('<I', 16)); put(directory + 14 * 8, struct.pack('<II', 0x2000, 72))
    put(0x98 + 224, b'.text\0\0\0' + struct.pack('<IIII', size - 0x200, 0x2000, size - 0x200, 0x200))
    put(0x200, struct.pack('<IHHIIIIII', 72, 2, 5, 0x2100, len(meta), 1, 0, 0, 0))
    put(0x300, meta); put(start, packed)
    return bytes(result)
