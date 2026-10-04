"""Original miniature PE/CLI and IL models. No copied game rows or bodies."""
import struct

from id_count_fixture import Image, compressed


def assemble(items):
    labels, operations, at = {}, [], 0
    short_branches = {0x2b, 0x2e, 0x31, 0x32, 0x33}
    for item in items:
        if isinstance(item, str): labels[item] = at; continue
        opcode, operand = item if isinstance(item, tuple) else (item, None)
        size = 1 if opcode < 256 else 2
        size += 1 if opcode in short_branches | {0x0e, 0x1f} else 4 if operand is not None else 0
        operations.append((at, opcode, operand, size)); at += size
    raw = bytearray()
    for at, opcode, operand, size in operations:
        raw.extend(bytes((opcode,)) if opcode < 256 else bytes((0xfe, opcode & 255)))
        if opcode in short_branches: raw.extend(struct.pack('<b', labels[operand] - at - size))
        elif opcode in (0x39, 0x3b, 0x3d): raw.extend(struct.pack('<i', labels[operand] - at - size))
        elif opcode in (0x0e, 0x1f): raw.extend(bytes((operand & 255,)))
        elif operand is not None: raw.extend(struct.pack('<I', operand & 0xffffffff))
    return bytes(raw), labels


def fixture(*, clothes=(2, 0, 1), malformed_array_size=False, duplicate_constant=False):
    b = Image(name='OriginalPlayerFactFixture')
    for name in ('System.Object', 'System.Int32', 'System.Array', 'System.RuntimeFieldHandle',
                 'System.Runtime.CompilerServices.RuntimeHelpers'): b.ref(name)
    b.type('<Module>', flags=0, base=0)
    b.type('HeadState', 'Original')
    head = b.field('head', 'Head', b'\x06\x08', 6)
    hair = b.field('hair', 'Hair', b'\x06\x08', 6)
    init = []
    for argument in (1, 2, 3, 5):
        init += [argument + 2 if argument <= 3 else (0x0e, argument), 0x16, 0x52]
    code, labels = assemble(init + [2, (0x7b, head), 0x18, (0x3b, 'full'),
        2, (0x7b, head), 0x19, (0x3b, 'hat'), (0x2b, 'end'),
        'full', 3, 0x17, 0x52, (0x2b, 'end'), 'hat', 4, 0x17, 0x52, 'end', 0x2a])
    hm = b.method('head', 'HeadFlags', b'\x20\x00\x01', 6, code)
    back = [2, (0x7b, hair), 0x0a, (0x0e, 4), 6, (0x1f, 10), (0x31, 'false')]
    ranges = [[20, 22], [30, 32], [40, 42]]
    for n, (lower, upper) in enumerate(ranges):
        back += [6, (0x1f, lower), (0x32, 'range' + str(n)), 6, (0x1f, upper), (0x31, 'false'), 'range' + str(n)]
    for identity in (50, 52, 54, 56): back += [6, (0x1f, identity), (0x2e, 'false')]
    back += [6, (0x1f, 70), 0xfe04, (0x2b, 'store'), 'false', 0x16, 'store', 0x52]
    for identity in (4, 7, 8, 72): back += [6, (0x1f, identity), (0x2e, 'true')]
    back += [6, (0x1f, 90), (0x33, 'return'), 'true', (0x0e, 4), 0x17, 0x52, 'return', 0x2a]
    bm = b.method('back', 'BackFlags', b'\x20\x00\x01', 6, assemble(back)[0])
    b.type('ClothesBuilder', 'Original')
    cloth = b.field('clothes', 'Order', b'\x06\x1d\x08', 1)
    cm = b.method('clothes', '.ctor', b'\x20\x00\x01', 0x1886)
    backing_type = b.type('OriginalStorage', 'Original', flags=0x100111)
    backing = b.field('storage', 'OriginalBytes', b'\x06\x11' + compressed(backing_type * 4), 0x113)
    b.rows[15] = [struct.pack('<HIH', 1, len(clothes) * 4 + int(malformed_array_size), backing_type)]
    b.rows[29] = [struct.pack('<IH', 0, backing & 0xffffff)]
    b.type('Versions', 'Original')
    release = b.field('release', 'Release', b'\x06\x08', 0x8056)
    version = b.field('version', 'Version', b'\x06\x0e', 0x8056)
    display = b.field('display', 'Display', b'\x06\x0e', 0x8056)
    b.rows[11] = [struct.pack('<HHH', kind, (token & 0xffffff) * 4, b.blob(value)) for token, kind, value in (
        (release, 8, struct.pack('<i', 38)), (version, 14, '0.0.1'.encode('utf-16-le')),
        (display, 14, 'v0.0.1'.encode('utf-16-le')))]
    if duplicate_constant: b.rows[11].append(b.rows[11][0])
    initialize = b.member('initialize', b.refs['System.Runtime.CompilerServices.RuntimeHelpers'], 'InitializeArray',
        b'\x00\x02\x01' + b.typeref('System.Array') + b.typeref('System.RuntimeFieldHandle', True))
    b.methods[(cm & 0xffffff) - 1]['code'] = assemble([2, (0x1f, len(clothes)),
        (0x8d, 0x01000000 | b.refs['System.Int32']), 0x25, (0xd0, backing), (0x28, initialize), (0x7d, cloth), 0x2a])[0]
    raw = bytearray(b.build()); at = len(raw)
    from resource_pipeline.server_semantics import _Metadata, SemanticLimits
    meta = _Metadata(bytes(raw), SemanticLimits())
    _, row_offset = meta.row(29, 1)
    struct.pack_into('<I', raw, row_offset, 0x2000 + at - 0x200)
    raw.extend(struct.pack('<' + 'i' * len(clothes), *clothes))
    section = 0x98 + 224
    struct.pack_into('<I', raw, section + 8, len(raw) - 0x200)
    struct.pack_into('<I', raw, section + 16, len(raw) - 0x200)
    return bytes(raw), {'head': head, 'hair': hair, 'headMethod': hm, 'headEnd': labels['end'],
        'backMethod': bm, 'clothesMethod': cm, 'clothes': cloth, 'release': release, 'version': version, 'display': display}
