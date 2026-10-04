"""Original synthetic PE/CLI group initializer, queues and bool transforms.

All IDs, metadata and executable byte strings below are original test fixtures.
No game arrays or executable input bytes are stored or executed.
"""
import struct

from dispatch_fixture import assemble, ldc, tok
from resource_pipeline.item_assembler import GROUPS


VALUES = ((9, 1), (2,), (7, 4), (3, 6), (5,), (8, 2), (10, 4))


def group_body(values=VALUES, *, inline=False):
    code = tok(0x7e, 0x04000001) + tok(0x73, 0x06000003) + tok(0x80, 0x04000003)
    for index, ids in enumerate(values):
        code += tok(0x7e, 0x04000003) + ldc(len(ids)) + tok(0x8d, 0x01000002)
        if inline or index == 1:
            for at, value in enumerate(ids):
                code += b'\x25' + ldc(at) + ldc(value) + b'\x9e'
        elif ids:
            code += b'\x25' + tok(0xd0, 0x0400000e + index) + tok(0x28, 0x0a000002)
        code += tok(0x6f, 0x06000004) + tok(0x80, 0x04000004 + index)
    return code + b'\x2a'


def fixture(**options):
    strings, blobs = bytearray(b'\0'), bytearray(b'\0')
    def text(value):
        at = len(strings); strings.extend(value.encode() + b'\0'); return at
    def blob(value):
        assert len(value) < 128
        at = len(blobs); blobs.extend(bytes((len(value),)) + value); return at
    values = options.get('values', VALUES)
    assert len(values) == len(GROUPS)
    payloads = [b''.join(struct.pack('<i', value) for value in ids) for ids in values]
    count = options.get('count', 13)
    count_code = ldc(count) + tok(0x80, 0x04000001) + tok(0x28, 0x2b000001) + tok(0x80, 0x04000002) + b'\x2a'
    constructor = (b'\x02' + tok(0x73, 0x0a000007) + tok(0x7d, 0x0400000c)
                   + b'\x02' + tok(0x73, 0x0a000001) + tok(0x7d, 0x0400000d)
                   + b'\x02' + tok(0x28, 0x0a000001)
                   + b'\x02\x03' + tok(0x7d, 0x0400000b) + b'\x2a')
    wrapper = b'\x02\x16\x03' + tok(0x28, 0x06000005) + b'\x2a'
    fill = assemble([2,(0x28,0x06000006),0x0a,0x16,0x0b,(0x2b,'@14'),
        6,7,3,0x9c,7,0x17,0x58,0x0b,7,6,0x8e,0x69,(0x32,'@6'),
        0x16,0x0c,(0x2b,'@34'),6,4,8,0x94,3,0x16,0xfe01,0x9c,8,0x17,0x58,0x0c,
        8,4,0x8e,0x69,(0x32,'@22'),6,0x2a])
    getter, offsets = assemble([2,(0x7b,0x0400000d),0x0a,0x16,0x0b,6,(0x12,1),(0x28,0x0a000003),
        2,(0x7b,0x0400000c),(0x6f,0x0a000005),(0x2d,'@17'),2,(0x7b,0x0400000b),(0x8d,0x01000003),0x0c,(0xde,'@27'),
        2,(0x7b,0x0400000c),(0x6f,0x0a000006),0x0c,(0xde,'@27'),7,(0x2c,'@26'),6,(0x28,0x0a000004),0xdc,8,0x2a], offsets_result=True)
    eh = b'\x01\x10\x00\x00' + struct.pack('<HHBHBI', 2, offsets[5], offsets[22]-offsets[5], offsets[22], offsets[27]-offsets[22], 0)
    codes = [options.get('count_code', count_code), options.get('group_code', group_body(values, inline=options.get('inline', False))),
             options.get('constructor', lambda x:x)(constructor), options.get('wrapper', wrapper),
             options.get('fill', fill), options.get('getter', getter)]
    queue = options.get('queue_signature', b'\x15\x12\x21\x01\x1d\x02')
    field_specs = [('Count', b'\x06\x06', 0x36), ('Search', b'\x06\x12\x29', 0x36),
                   ('Factory', b'\x06\x12\x14', 0x16)]
    field_specs += [(name, b'\x06\x1d\x02', 0x16) for name in GROUPS]
    field_specs += [('_size', b'\x06\x08', 1), ('_boolBufferCache', b'\x06'+queue, 1), ('_queueLock', b'\x06\x1c', 1)]
    field_specs += [('OriginalBytes'+str(n), b'\x06\x11'+bytes(((7+n)<<2,)), 0x111) for n in range(7)]
    fields = [struct.pack('<HHH', options.get('field_flags', {}).get(i, flags),
                          text(options.get('field_names', {}).get(i, name)),
                          blob(options.get('field_signatures', {}).get(i, signature)))
              for i,(name,signature,flags) in enumerate(field_specs,1)]
    types = [('<Module>', '', 0, 0, 1, 1), ('ItemID', 'Terraria.ID', 1, 5, 1, 1),
             ('PrefixLegacy', 'Terraria.GameContent.Prefixes', 1, 5, 3, 2),
             ('ItemSets', '', 2, 5, 3, 2), ('SetFactory', 'Terraria.ID', 1, 5, 11, 3),
             ('OriginalPayloads', 'Fixture', 0x180, 5, 14, 7)]
    types += [('OriginalBlock'+str(n), 'Fixture', 0x110, 29, 21, 7) for n in range(7)]
    names = ['.cctor', '.cctor', '.ctor', 'CreateBoolSet', 'CreateBoolSet', 'GetBoolBuffer']
    signatures = [b'\x00\x00\x01', b'\x00\x00\x01', b'\x20\x01\x01\x08',
                  b'\x20\x01\x1d\x02\x1d\x08', b'\x20\x02\x1d\x02\x02\x1d\x08', b'\x20\x00\x1d\x02']
    methods = [[0,0,options.get('method_flags', {}).get(i, 0x1891 if i <= 2 else 0x1886 if i == 3 else 0x86),
                text(name),blob(options.get('signatures',{}).get(i,signatures[i-1])),1]
               for i,name in enumerate(names,1)]
    refs = ['Object', 'Int32', 'Boolean', 'Array', 'RuntimeFieldHandle', 'RuntimeHelpers', 'ValueType', 'Queue`1', 'Monitor']
    rows = {
        0:[struct.pack('<HHHHH',0,text('OriginalPrefixGroups.dll'),1,0,0)],
        1:[struct.pack('<HHH',6,text(options.get('ref_names',{}).get(i,name)),
                       text('System.Runtime.CompilerServices' if name == 'RuntimeHelpers' else 'System.Collections.Generic' if name == 'Queue`1' else 'System.Threading' if name == 'Monitor' else 'System'))
           for i,name in enumerate(refs,1)] + [struct.pack('<HHH',10,text('IdDictionary'),text('ReLogic.Reflection'))],
        2:[struct.pack('<IHHHHH',options.get('type_flags',{}).get(i,flags),text(name),text(namespace),base,
                       options.get('type_first_fields',{}).get(i,field),options.get('type_first_methods',{}).get(i,method))
           for i,(name,namespace,flags,base,field,method) in enumerate(types,1)],
        4:fields,6:[],
        10:[struct.pack('<HHH',9,text('.ctor'),blob(b'\x20\x00\x01')),
            struct.pack('<HHH',options.get('initialize_owner',49),text(options.get('initialize_name','InitializeArray')),blob(b'\x00\x02\x01\x12\x11\x11\x15')),
            struct.pack('<HHH',73,text('Enter'),blob(b'\x00\x02\x01\x1c\x10\x02')),
            struct.pack('<HHH',73,text('Exit'),blob(b'\x00\x01\x01\x1c')),
            struct.pack('<HHH',12,text('get_Count'),blob(b'\x20\x00\x08')),
            struct.pack('<HHH',12,text(options.get('dequeue_name','Dequeue')),blob(b'\x20\x00\x13\x00')),
            struct.pack('<HHH',12,text('.ctor'),blob(b'\x20\x00\x01')),
            struct.pack('<HHH',81,text('Create'),blob(b'\x10\x02\x00\x12\x29'))],
        15:[struct.pack('<HIH',options.get('layout_pack',{}).get(n,1),len(data)+options.get('layout_delta',{}).get(n,0),7+n) for n,data in enumerate(payloads)],
        17:[struct.pack('<H',blob(options.get('fill_locals',b'\x07\x03\x1d\x02\x08\x08'))),
            struct.pack('<H',blob(b'\x07\x03\x1c\x02\x1d\x02'))],
        27:[struct.pack('<H',blob(queue))],
        29:[struct.pack('<IH',0,14+n) for n in range(7)],
        32:[struct.pack('<IHHHHIHHH',0,1,0,0,0,0,0,text('OriginalPrefixGroups'),0)],
        35:[struct.pack('<HHHHIHHHH',4,0,0,0,0,blob(options.get('key',bytes.fromhex('b77a5c561934e089'))),text('mscorlib'),0,0),
            struct.pack('<HHHHIHHHH',1,0,0,0,0,0,text('ReLogic'),0,0)],
        41:[struct.pack('<HH',4,3)],
        43:[struct.pack('<HH',17,blob(b'\x0a\x02\x12\x08\x06'))],
    }
    if options.get('group_locals'):
        rows[17].append(struct.pack('<H',blob(b'\x07\x01\x08')))
    def metadata():
        rows[6] = [struct.pack('<IHHHHH',*row) for row in methods]
        tables = struct.pack('<IBBBBQQ',0,2,0,0,1,sum(1<<k for k in rows),0)
        tables += b''.join(struct.pack('<I',len(rows[k])) for k in sorted(rows))
        tables += b''.join(b''.join(rows[k]) for k in sorted(rows))
        streams={'#~':tables,'#Strings':bytes(strings),'#Blob':bytes(blobs),'#GUID':bytes(16)}
        root=bytearray(struct.pack('<IHHII',0x424a5342,1,1,0,12)+b'v4.0.30319\0\0'+struct.pack('<HH',0,4))
        cursor=len(root)+sum(8+((len(name)+4)&~3) for name in streams)
        for name,raw in streams.items():
            root.extend(struct.pack('<II',cursor,len(raw)));encoded=name.encode()+b'\0'
            root.extend(encoded+b'\0'*(-len(encoded)%4));cursor+=len(raw)
        for raw in streams.values():root.extend(raw)
        return bytes(root)
    meta=metadata();start=(0x300+len(meta)+3)&~3;bodies=bytearray()
    for i,code in enumerate(codes,1):
        methods[i-1][0]=0x2000+start+len(bodies)-0x200
        local=0x11000001 if i==5 else 0x11000002 if i==6 else 0x11000003 if i==2 and options.get('group_locals') else 0
        bodies.extend(struct.pack('<HHII',options.get('header_flags',{}).get(i,0x301b if i==6 else 0x3013),
                                  options.get('maxstack',{}).get(i,8),len(code),local)+code)
        bodies.extend(b'\0'*(-len(bodies)%4))
        if i==6:bodies.extend(options.get('getter_eh',lambda x:x)(eh))
    data_start=start+len(bodies);cursor=data_start
    for n,raw in enumerate(payloads):
        rows[29][n]=struct.pack('<IH',0x2000+cursor-0x200,14+n);cursor+=len(raw)
    meta=metadata();size=(cursor+511)//512*512;output=bytearray(size)
    def put(at,value):output[at:at+len(value)]=value
    put(0,b'MZ');put(0x3c,struct.pack('<I',0x80));put(0x80,b'PE\0\0')
    put(0x84,struct.pack('<HHIIIHH',0x14c,1,0,0,0,224,0x102));put(0x98,struct.pack('<H',0x10b))
    directory=0x98+96;put(directory-4,struct.pack('<I',16));put(directory+14*8,struct.pack('<II',0x2000,72))
    put(0x98+224,b'.text\0\0\0'+struct.pack('<IIII',size-0x200,0x2000,size-0x200,0x200))
    put(0x200,struct.pack('<IHHIIIIII',72,2,5,0x2100,len(meta),1,0,0,0))
    put(0x300,meta);put(start,bodies);put(data_start,b''.join(payloads))
    return bytes(output)
