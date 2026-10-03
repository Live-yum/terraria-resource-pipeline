"""Original non-executable PE/CLI fixture for numeric stage analysis."""
import struct


def item_stage_pe(*,explicit_layout=False,explicit_base=False):
    strings=bytearray(b'\0');blobs=bytearray(b'\0')
    def s(text):
        index=len(strings);strings.extend(text.encode()+b'\0');return index
    def b(data):
        index=len(blobs);assert len(data)<128;blobs.extend(bytes((len(data),))+data);return index
    def tok(op,value):return bytes((op,))+struct.pack('<I',value)
    def ldc(value):return b'\x20'+struct.pack('<i',value)
    field=0x04000003
    method_codes=[
        b'\x02'+ldc(17)+tok(0x7d,field)+b'\x2a',
        b'\x2a',
        b'\x02\x03'+tok(0x28,0x06000006)+tok(0x7d,field)+b'\x2a',
        b'\x2b\xfe',
        b'\x02\x14'+tok(0x7d,field+1)+b'\x02'+ldc(9)+tok(0x7d,field)+b'\x2a',
        b'\x02'+ldc(100)+b'\x5a\x2a',
    ]
    if explicit_layout or explicit_base:
        # Original overlapping-storage regression: both int32 fields live at
        # byte zero, so independent field facts would contradict the layout.
        method_codes[0]=b'\x02'+ldc(17)+tok(0x7d,field)+b'\x02'+ldc(99)+tok(0x7d,field+1)+b'\x2a'
    field_rows=[struct.pack('<HHH',0x8056,s('One'),b(b'\x06\x08')),
                struct.pack('<HHH',0x8056,s('Two'),b(b'\x06\x08')),
                struct.pack('<HHH',0x0006,s('FixtureValue'),b(b'\x06\x08')),
                struct.pack('<HHH',0x0006,s('FixtureReference'),b(b'\x06\x08' if explicit_layout or explicit_base else b'\x06\x0e'))]
    type_rows=[struct.pack('<IHHHHH',0,s('<Module>'),s(''),0,1,1),
               struct.pack('<IHHHHH',0,s('ItemID'),s('Terraria.ID'),0,1,1),
               struct.pack('<IHHHHH',0x10 if explicit_layout else 0,s('Item'),s('Terraria'),0,3,1)]
    if explicit_base:
        type_rows[2]=struct.pack('<IHHHHH',0x10,s('FixtureBase'),s('Terraria'),0,3,1)
        type_rows.append(struct.pack('<IHHHHH',0,s('Item'),s('Terraria'),3<<2,5,1))
    methods=[]
    for n in range(6):
        signature=b'\x20\x01\x01\x08' if n<5 else b'\x00\x01\x08\x08'
        methods.append([0,0,6 if n<5 else 0x16,s('SetDefaults'+str(n+1) if n<5 else 'FixtureHelper'),b(signature),1])
    rows={0:[struct.pack('<HHHHH',0,s('Fixture.dll'),1,0,0)],2:type_rows,4:field_rows,
          6:[],11:[struct.pack('<HHH',8,4,b(struct.pack('<i',1))),struct.pack('<HHH',8,8,b(struct.pack('<i',2)))],
          32:[struct.pack('<IHHHHIHHH',0,2,3,4,5,0,0,s('OriginalFixture'),0)]}
    if explicit_layout or explicit_base:
        rows[15]=[struct.pack('<HIH',0,4,3)]
        rows[16]=[struct.pack('<IH',0,3),struct.pack('<IH',0,4)]
    def metadata():
        rows[6]=[struct.pack('<IHHHHH',*row) for row in methods]
        tables=struct.pack('<IBBBBQQ',0,2,0,0,1,sum(1<<key for key in rows),0)
        tables+=b''.join(struct.pack('<I',len(rows[key])) for key in sorted(rows))
        tables+=b''.join(b''.join(rows[key]) for key in sorted(rows))
        streams={'#~':tables,'#Strings':bytes(strings),'#Blob':bytes(blobs),'#GUID':bytes(16)}
        root=bytearray(struct.pack('<IHHII',0x424a5342,1,1,0,12)+b'v4.0.30319\0\0'+struct.pack('<HH',0,4))
        cursor=len(root)+sum(8+((len(name)+4)&~3) for name in streams)
        for name,data in streams.items():
            root.extend(struct.pack('<II',cursor,len(data)));encoded=name.encode()+b'\0';root.extend(encoded+b'\0'*((-len(encoded))%4));cursor+=len(data)
        for data in streams.values():root.extend(data)
        return bytes(root)
    meta=metadata();method_start=(0x300+len(meta)+3)&~3;bodies=bytearray()
    for row,code in zip(methods,method_codes):
        row[0]=0x2000+method_start+len(bodies)-0x200
        bodies.extend(struct.pack('<HHII',0x3013,8,len(code),0)+code)
        bodies.extend(b'\0'*((-len(bodies))%4))
    meta=metadata();size=((method_start+len(bodies)+511)//512)*512
    out=bytearray(size)
    def put(offset,data):out[offset:offset+len(data)]=data
    put(0,b'MZ');put(0x3c,struct.pack('<I',0x80));put(0x80,b'PE\0\0')
    put(0x84,struct.pack('<HHIIIHH',0x14c,1,0,0,0,224,0x102));put(0x98,struct.pack('<H',0x10b))
    directory=0x98+96;put(directory-4,struct.pack('<I',16));put(directory+14*8,struct.pack('<II',0x2000,72))
    put(0x98+224,b'.text\0\0\0'+struct.pack('<IIII',size-0x200,0x2000,size-0x200,0x200))
    put(0x200,struct.pack('<IHHIIIIII',72,2,5,0x2100,len(meta),1,0,0,0))
    put(0x300,meta);put(method_start,bodies)
    return bytes(out)
