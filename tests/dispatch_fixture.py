"""Original synthetic PE metadata and generic bool-array loops; no game bytes."""
import struct


def tok(op, value):
    return bytes((op,)) + struct.pack('<I', value)


def ldc(value):
    return b'\x20' + struct.pack('<i', value)


def assemble(pattern, *, offsets_result=False):
    sizes = []
    for value in pattern:
        if isinstance(value, int):
            sizes.append(2 if value > 255 else 1)
        else:
            op, arg = value
            sizes.append(2 if isinstance(arg, str) or op == 0x12 else 5)
    offsets = []; offset = 0
    for size in sizes:
        offsets.append(offset); offset += size
    result = bytearray()
    for n, value in enumerate(pattern):
        if isinstance(value, int):
            result.extend(bytes((0xfe, value & 255)) if value > 255 else bytes((value,)))
        else:
            op, arg = value
            if isinstance(arg, str):
                result.extend(bytes((op,)) + struct.pack('<b', offsets[int(arg[1:])] - offsets[n] - sizes[n]))
            elif op == 0x12:
                result.extend(bytes((op, arg)))
            else:
                result.extend(tok(op, arg))
    return (bytes(result), offsets) if offsets_result else bytes(result)


def dispatch_pe(**options):
    if options.get('custom_code') is not None and options.get('reject_zero'):
        raise ValueError('Custom fixture does not support the guarded constructor')
    strings = bytearray(b'\0'); blobs = bytearray(b'\0')
    def text(value):
        n = len(strings); strings.extend(value.encode() + b'\0'); return n
    def blob(value):
        n = len(blobs); assert len(value) < 128
        blobs.extend(bytes((len(value),)) + value); return n
    values = options.get('ids', ((1, 4, 4), (3, 8), (2, 11), (6,)))
    payloads = [b''.join(struct.pack('<i', n) for n in ids) for ids in values]
    pooled = options.get('pooled', False)
    shift = 2 if pooled else 0
    count = options.get('count', 13)
    count_code = ldc(count) + tok(0x80, 0x04000001) + options.get('count_tail', b'') + b'\x2a'
    sets = tok(0x7e, 0x04000001) + tok(0x73, 0x06000003) + tok(0x80, 0x04000002)
    for n, ids in enumerate(values):
        sets += tok(0x7e, 0x04000002)
        explicit = options.get('explicit', {}).get(n)
        if explicit is not None: sets += ldc(explicit)
        sets += ldc(len(ids)) + tok(0x8d, options.get('element_type', 0x01000002))
        sets += b'\x25' + tok(0xd0, 0x04000009 + shift + n) + tok(0x28, 0x0a000002)
        sets += tok(0x6f, 0x06000005 if explicit is not None else 0x06000004) + tok(0x80, 0x04000003 + n)
    sets += options.get('sets_tail', b'') + b'\x2a'
    constructor = b'\x02' + tok(0x28, 0x0a000001) + b'\x02\x03' + tok(0x7d, 0x04000007) + b'\x2a'
    if pooled and options.get('fresh_constructor'):
        constructor = (b'\x02' + tok(0x73, 0x0a000007) + tok(0x7d, 0x04000008)
                       + b'\x02' + tok(0x73, 0x0a000001) + tok(0x7d, 0x04000009) + constructor)
    if options.get('reject_zero'):
        constructor = constructor[:-8] + b'\x03\x2d\x0b' + tok(0x72, 0x70000001) + tok(0x73, 0x0a000008 if pooled else 0x0a000003) + b'\x7a' + constructor[-8:]
    constructor = options.get('constructor', lambda code: code)(constructor)
    wrapper = b'\x02\x16\x03' + tok(0x28, 0x06000005) + b'\x2a'
    bool_code = assemble([2,(0x28,0x06000006),0x0a,0x16,0x0b,(0x2b,'@14'),
        6,7,3,0x9c,7,0x17,0x58,0x0b,7,6,0x8e,0x69,(0x32,'@6'),
        0x16,0x0c,(0x2b,'@34'),6,4,8,0x94,3,0x16,0xfe01,0x9c,8,0x17,0x58,0x0c,
        8,4,0x8e,0x69,(0x32,'@22'),6,0x2a])
    getter = b'\x02' + tok(0x7b, 0x04000007) + tok(0x8d, 0x01000003) + b'\x2a'
    getter_eh = b''
    if pooled:
        getter, offsets = assemble([2,(0x7b,0x04000009),0x0a,0x16,0x0b,6,(0x12,1),(0x28,0x0a000003),
            2,(0x7b,0x04000008),(0x6f,0x0a000005),(0x2d,'@17'),2,(0x7b,0x04000007),(0x8d,0x01000003),0x0c,(0xde,'@27'),
            2,(0x7b,0x04000008),(0x6f,0x0a000006),0x0c,(0xde,'@27'),7,(0x2c,'@26'),6,(0x28,0x0a000004),0xdc,8,0x2a], offsets_result=True)
        getter_eh = b'\x01\x10\x00\x00' + struct.pack('<HHBHBI', 2, offsets[5], offsets[22]-offsets[5], offsets[22], offsets[27]-offsets[22], 0)
        getter_eh = options.get('getter_eh', lambda x:x)(getter_eh)
    consumer = b''
    for n in range(4):
        consumer += tok(0x7e, 0x04000003 + n) + b'\x02' + tok(0x7b, 0x04000008 + shift) + b'\x91\x2c\x01\x00'
    consumer += b'\x2a'
    codes = [count_code, options.get('sets_code', sets), constructor, options.get('wrapper', wrapper),
             options.get('factory', bool_code), options.get('getter', getter), options.get('consumer', consumer)]
    if options.get('custom_code') is not None:
        codes.insert(6, options['custom_code'])
    type_specs = [('<Module>', '', 0, 0, 1, 1), ('ItemID', 'Terraria.ID', 1, 5, 1, 1),
                  ('Sets', '', 2, 5, 2, 2), ('SetFactory', 'Terraria.ID', 1, 5, 7, 3),
                  ('Item', 'Terraria', 1, 5, 8 + shift, 7), ('OriginalPayloads', 'Fixture', 0x180, 5, 9 + shift, 8)]
    type_specs += [('OriginalBlock' + str(n), 'Fixture', 0x110, 29, 13 + shift, 8) for n in range(4)]
    if options.get('custom_code') is not None:
        type_specs = [(name, ns, flags, base, field, method + (method >= 7))
                      for name, ns, flags, base, field, method in type_specs]
    if options.get('custom_struct'):
        type_specs.append(('OriginalDefault', 'Fixture', options.get('struct_flags', 0x109), 29, 13, 9))
    fields = []
    specs = [('Count', b'\x06\x08', 0x16), ('Factory', b'\x06\x12\x10', 0x16)]
    specs += [(name, b'\x06\x1d\x02', 0x16) for name in ('IsFood', 'Deprecated', 'IsDrill', 'IsChainsaw')]
    specs += [('_size', b'\x06\x08', 1)]
    queue = options.get('queue_signature', b'\x15\x12\x21\x01\x1d' + bytes((options.get('queue_element', 2),)))
    if pooled: specs += [('_boolBufferCache', b'\x06'+queue, 1), ('_queueLock', b'\x06\x1c', 1)]
    specs += [('type', b'\x06\x08', 6)]
    specs += [('OriginalBytes' + str(n), b'\x06\x11' + bytes(((7 + n) << 2,)), 0x111) for n in range(4)]
    if options.get('custom_struct'):
        specs += [('First', b'\x06\x08', 6), ('Second', b'\x06\x06', 6)]
    for n, (name, sig, flags) in enumerate(specs, 1):
        fields.append(struct.pack('<HHH', options.get('field_flags', {}).get(n, flags), text(options.get('field_names', {}).get(n, name)),
                                  blob(options.get('field_signatures', {}).get(n, sig))))
    names = ['.cctor', '.cctor', '.ctor', 'CreateBoolSet', 'CreateBoolSet', 'GetBoolBuffer', 'SetDefaults']
    signatures = [b'\x00\x00\x01',b'\x00\x00\x01',b'\x20\x01\x01\x08',b'\x20\x01\x1d\x02\x1d\x08',
                  b'\x20\x02\x1d\x02\x02\x1d\x08',b'\x20\x00\x1d\x02',b'\x20\x01\x01\x08']
    if options.get('custom_code') is not None:
        names.insert(6, 'CreateCustomSet')
        signatures.insert(6, b'\x30\x01\x02\x1d\x1e\x00\x1e\x00\x1d\x1c')
    methods = [[0, 0, 0x1891 if n < 2 else 0x1886 if n == 2 else 0x86,
                text(name), blob(options.get('signatures', {}).get(n + 1, signatures[n])), 1] for n, name in enumerate(names)]
    refs = ['Object', 'Int32', 'Boolean', 'Array', 'RuntimeFieldHandle', 'RuntimeHelpers', 'ValueType']
    if pooled: refs += ['Queue`1', 'Monitor']
    if options.get('reject_zero'): refs += ['ArgumentOutOfRangeException']
    rows = {0: [struct.pack('<HHHHH', 0, text('OriginalDispatch.dll'), 1, 0, 0)],
        1: [struct.pack('<HHH',6,text(name),text('System.Runtime.CompilerServices' if name == 'RuntimeHelpers' else 'System.Collections.Generic' if name == 'Queue`1' else 'System.Threading' if name == 'Monitor' else 'System')) for name in refs],
        2: [struct.pack('<IHHHHH',options.get('type_flags',{}).get(i,flags),text(name),text(ns),base,field,method)
            for i,(name,ns,flags,base,field,method) in enumerate(type_specs,1)],
        4: fields, 6: [],
        10: [struct.pack('<HHH',9,text('.ctor'),blob(b'\x20\x00\x01')),
             struct.pack('<HHH',49,text(options.get('initialize_name','InitializeArray')),blob(b'\x00\x02\x01\x12\x11\x11\x15'))],
        15: [struct.pack('<HIH',1,len(data) + options.get('layout_delta', {}).get(n, 0),7+n) for n,data in enumerate(payloads)],
        17: [struct.pack('<H',blob(options.get('factory_locals', b'\x07\x03\x1d\x02\x08\x08')))],
        29: [struct.pack('<IH', 0, 9 + shift + n) for n in range(4)],
        32: [struct.pack('<IHHHHIHHH',0,1,0,0,0,0,0,text('OriginalDispatch'),0)],
        35: [struct.pack('<HHHHIHHHH',4,0,0,0,0,blob(options.get('key',bytes.fromhex('b77a5c561934e089'))),text('mscorlib'),0,0)],
        41: [struct.pack('<HH',3,2)]}
    if pooled:
        rows[17].append(struct.pack('<H', blob(b'\x07\x03\x1c\x02\x1d\x02')))
        rows[27] = [struct.pack('<H', blob(queue))]
        rows[10] += [struct.pack('<HHH', 73, text('Enter'), blob(b'\x00\x02\x01\x1c\x10\x02')),
                     struct.pack('<HHH', 73, text('Exit'), blob(b'\x00\x01\x01\x1c')),
                     struct.pack('<HHH', 12, text('get_Count'), blob(b'\x20\x00\x08')),
                     struct.pack('<HHH', 12, text(options.get('dequeue_name', 'Dequeue')), blob(b'\x20\x00\x13\x00')),
                     struct.pack('<HHH', 12, text('.ctor'), blob(b'\x20\x00\x01'))]
    if options.get('custom_code') is not None:
        custom_local = len(rows[17]) + 1
        rows[17].append(struct.pack('<H', blob(options.get('custom_locals', b'\x07\x04\x1d\x1e\x00\x08\x08\x1e\x00'))))
        rows.setdefault(27, []).append(struct.pack('<H', blob(options.get('custom_element', b'\x1e\x00'))))
        refs.append('Exception')
        rows[1].append(struct.pack('<HHH', 6, text('Exception'), text('System')))
        rows[10].append(struct.pack('<HHH', len(refs) << 3 | 1, text('.ctor'), blob(b'\x20\x01\x01\x0e')))
        rows[42] = [struct.pack('<HHHH', *options.get('custom_generic', (0, 0, 7 << 1 | 1)), text('T'))]
        rows[43] = [struct.pack('<HH', options.get('custom_spec_method', 7 << 1), blob(options.get('custom_spec', b'\x0a\x01\x08')))]
    if options.get('custom_struct'):
        sets_local = len(rows[17]) + 1
        rows[17].append(struct.pack('<H', blob(b'\x07\x01\x11\x2c')))
    if options.get('custom_struct_attribute'):
        rows[12] = [struct.pack('<HHH', 11 << 5 | 3, 1 << 3 | 3, blob(b'\x01\x00\x00\x00'))]
    if options.get('field_layout'):
        rows[16] = [struct.pack('<IH', offset, field) for offset, field in options['field_layout']]
    if options.get('reject_zero'):
        rows[10].append(struct.pack('<HHH', len(refs) << 3 | 1, text('.ctor'), blob(b'\x20\x01\x01\x0e')))
    if options.get('lifecycle_list'):
        list_ref = len(rows[1]) + 1
        rows[1].append(struct.pack('<HHH', 6, text(options.get('list_type_name', 'List`1')), text('System.Collections.Generic')))
        from resource_pipeline.item_texture_aliases import _compressed_bytes
        list_sig = b'\x15\x12' + _compressed_bytes(list_ref << 2 | 1) + bytes((1, options.get('list_element', 8)))
        rows.setdefault(27, []).append(struct.pack('<H', blob(list_sig)))
        list_parent = len(rows[27]) << 3 | 4
        rows[10] += [struct.pack('<HHH', list_parent, text('.ctor'), blob(b'\x20\x00\x01')),
                     struct.pack('<HHH', list_parent, text(options.get('list_add_name', 'Add')), blob(b'\x20\x01\x01\x13\x00'))]
        rows[4][2] = struct.pack('<HHH', 0x16, text('IsFood'), blob(b'\x06' + list_sig))
    for name in options.get('extra_type_refs', ()):
        rows[1].append(struct.pack('<HHH', 6, text(name), text('System')))
    for parent, name, sig in options.get('extra_members', ()):
        rows[10].append(struct.pack('<HHH', parent, text(name), blob(sig)))
    for sig in options.get('extra_type_specs', ()):
        rows.setdefault(27, []).append(struct.pack('<H', blob(sig)))
    for target, sig in options.get('extra_method_specs', ()):
        rows.setdefault(43, []).append(struct.pack('<HH', target, blob(sig)))
    if options.get('sets_locals'):
        sets_local = len(rows[17]) + 1
        rows[17].append(struct.pack('<H', blob(options['sets_locals'])))
    def metadata():
        rows[6] = [struct.pack('<IHHHHH',*row) for row in methods]
        tables = struct.pack('<IBBBBQQ',0,2,0,0,1,sum(1<<key for key in rows),0)
        tables += b''.join(struct.pack('<I',len(rows[k])) for k in sorted(rows))
        tables += b''.join(b''.join(rows[k]) for k in sorted(rows))
        streams = {'#~':tables,'#Strings':bytes(strings),'#Blob':bytes(blobs),'#GUID':bytes(16)}
        if options.get('reject_zero') or options.get('custom_code') is not None: streams['#US'] = b'\0\x03x\0\0'
        root = bytearray(struct.pack('<IHHII',0x424a5342,1,1,0,12)+b'v4.0.30319\0\0'+struct.pack('<HH',0,len(streams)))
        offset = len(root)+sum(8+((len(name)+4)&~3) for name in streams)
        for name,data in streams.items():
            root.extend(struct.pack('<II',offset,len(data)));raw=name.encode()+b'\0'
            root.extend(raw+b'\0'*((-len(raw))%4));offset+=len(data)
        for data in streams.values():root.extend(data)
        return bytes(root)
    meta = metadata(); start=(0x300+len(meta)+3)&~3; bodies=bytearray()
    for n,code in enumerate(codes):
        methods[n][0] = 0x2000+start+len(bodies)-0x200
        bodies.extend(struct.pack('<HHII', options.get('header_flags', {}).get(n+1,0x301b if pooled and n==5 else 0x3013),16,len(code),0x11000000 | sets_local if n == 1 and (options.get('custom_struct') or options.get('sets_locals')) else 0x11000000 | custom_local if n == 6 and options.get('custom_code') is not None else 0x11000001 if n==4 else 0x11000002 if pooled and n==5 else 0)+code)
        bodies.extend(b'\0'*((-len(bodies))%4))
        if pooled and n==5: bodies.extend(getter_eh)
    data_start=start+len(bodies);cursor=data_start
    for n,data in enumerate(payloads):
        rows[29][n]=struct.pack('<IH',0x2000+cursor-0x200,9+shift+n);cursor+=len(data)
    meta=metadata();size=((cursor+511)//512)*512;output=bytearray(size)
    def put(at,value):output[at:at+len(value)]=value
    put(0,b'MZ');put(0x3c,struct.pack('<I',0x80));put(0x80,b'PE\0\0')
    put(0x84,struct.pack('<HHIIIHH',0x14c,1,0,0,0,224,0x102));put(0x98,struct.pack('<H',0x10b))
    directory=0x98+96;put(directory-4,struct.pack('<I',16));put(directory+14*8,struct.pack('<II',0x2000,72))
    put(0x98+224,b'.text\0\0\0'+struct.pack('<IIII',size-0x200,0x2000,size-0x200,0x200))
    put(0x200,struct.pack('<IHHIIIIII',72,2,5,0x2100,len(meta),1,0,0,0))
    put(0x300,meta);put(start,bodies);put(data_start,b''.join(payloads))
    return bytes(output)
