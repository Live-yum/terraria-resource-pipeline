"""Original PE model of packed-color arithmetic; no vendor or game byte fixture."""
import struct
from id_count_fixture import Image


def assemble(rows):
    labels={};ops=[];position=0
    short={0x0f,0x10,0x11,0x12,0x13,0x1f};branches={0x2b,0x2c,0x2f,0x31,0x34,0x36}
    for row in rows:
        if isinstance(row,str):labels[row]=position;continue
        op,arg=row if isinstance(row,tuple) else (row,None)
        width=(2 if op>255 else 1)+(1 if op in short|branches else 4 if arg is not None else 0)
        ops.append((position,op,arg,width));position+=width
    output=bytearray()
    for offset,op,arg,width in ops:
        output.extend(bytes((op,)) if op<=255 else bytes((0xfe,op&255)))
        if op in branches:output.extend(struct.pack('<b',labels[arg]-offset-width))
        elif op in short:output.append(arg&255)
        elif op==0x22:output.extend(struct.pack('<f',arg))
        elif arg is not None:output.extend(struct.pack('<I',arg&0xffffffff))
    return bytes(output)


def fixture(**options):
    b=Image('OriginalColors',options)
    value=b.ref('System.ValueType');uint=b.ref('System.UInt32')
    b.type('<Module>',flags=0,base=0)
    b.type('Color','Microsoft.Xna.Framework',flags=0x100109,base=value*4+1)
    f=b.field('packed','packedValue',b'\x06\x09',1);c=b'\x11\x08';m={}
    def method(key,name,sig,flags,local=b''):
        m[key]=b.method(key,name,sig,flags,locals=local)
    method('packed','.ctor',b'\x20\x01\x01\x09',0x1881)
    method('clamp','ClampToByte64',b'\x00\x01\x08\x0a',0x91)
    method('rgb','.ctor',b'\x20\x03\x01\x08\x08\x08',0x1886)
    for n in 'RGBA':method(n,'get_'+n,b'\x20\x00\x05',0x886)
    method('alpha','set_A',b'\x20\x01\x01\x05',0x886)
    method('equal','Equals',b'\x20\x01\x02'+c,0x1e6)
    method('eq','op_Equality',b'\x00\x02\x02'+c+c,0x896)
    method('multiply','op_Multiply',b'\x00\x02'+c+c+b'\x0c',0x896,b'\x07\x07'+b'\x09'*6+c)
    for n in ('Transparent','Black','Gray','LightGray'):method(n,'get_'+n,b'\x00\x00'+c,0x896)
    equals=b.member('uint-equals',uint,'Equals',b'\x20\x01\x02\x09')
    bodies={
      'packed':[2,3,(0x7d,f),0x2a],
      'clamp':[2,0x16,0x6a,(0x2f,'not-negative'),0x16,0x2a,'not-negative',2,(0x20,255),0x6a,
               (0x31,'not-large'),(0x20,255),0x2a,'not-large',2,0x69,0x2a],
      'rgb':[3,4,0x60,5,0x60,(0x20,-256),0x5f,(0x2c,'pack'),3,0x6a,(0x28,m['clamp']),(0x10,1),
             4,0x6a,(0x28,m['clamp']),(0x10,2),5,0x6a,(0x28,m['clamp']),(0x10,3),
             'pack',4,0x1e,0x62,(0x10,2),5,(0x1f,16),0x62,(0x10,3),2,3,4,0x60,5,0x60,
             (0x20,-16777216),0x60,(0x7d,f),0x2a],
      'R':[2,(0x7b,f),0xd2,0x2a],
      'G':[2,(0x7b,f),0x1e,0x64,0xd2,0x2a],
      'B':[2,(0x7b,f),(0x1f,16),0x64,0xd2,0x2a],
      'A':[2,(0x7b,f),(0x1f,24),0x64,0xd2,0x2a],
      'alpha':[2,2,(0x7b,f),(0x20,16777215),0x5f,3,(0x1f,24),0x62,0x60,(0x7d,f),0x2a],
      'equal':[2,(0x7c,f),(0x0f,1),(0x7b,f),(0x28,equals),0x2a],
      'eq':[(0x0f,0),3,(0x28,m['equal']),0x2a],
    }
    # Separate original instruction authoring, with named branch labels.
    q=[(0x0f,0),(0x7b,f),(0x13,5),(0x11,5),0xd2,(0x13,4),
       (0x11,5),0x1e,0x64,0xd2,0x0d,(0x11,5),(0x1f,16),0x64,0xd2,0x0c,
       (0x11,5),(0x1f,24),0x64,0xd2,0x0b,3,(0x22,65536.0),0x5a,(0x10,1),
       3,(0x22,0.0),(0x34,'positive'),0x16,0x0a,(0x2b,'scaled'),'positive',
       3,(0x22,16777215.0),(0x36,'convert'),(0x20,16777215),0x0a,(0x2b,'scaled'),
       'convert',3,0x6d,0x0a,'scaled']
    q.extend([(0x11,4),6,0x5a,(0x1f,16),0x64,(0x13,4),9,6,0x5a,(0x1f,16),0x64,0x0d,
              8,6,0x5a,(0x1f,16),0x64,0x0c,7,6,0x5a,(0x1f,16),0x64,0x0b])
    q.extend([(0x11,4),(0x20,255),(0x36,'red'),(0x20,255),(0x13,4),'red',
              9,(0x20,255),(0x36,'green'),(0x20,255),0x0d,'green',
              8,(0x20,255),(0x36,'blue'),(0x20,255),0x0c,'blue',
              7,(0x20,255),(0x36,'alpha'),(0x20,255),0x0b,'alpha',
              (0x12,6),(0x11,4),9,0x1e,0x62,0x60,8,(0x1f,16),0x62,0x60,
              7,(0x1f,24),0x62,0x60,(0x7d,f),(0x11,6),0x2a])
    bodies['multiply']=q
    values=options.get('named_values',{'Transparent':0,'Black':0x87654321,'Gray':0x12345678,'LightGray':0xabcdef01})
    for n,v in values.items():bodies[n]=[0x16 if v==0 else (0x20,v),(0x73,m['packed']),0x2a]
    for entry in b.methods:entry['code']=assemble(bodies[entry['key']])
    if options.get('extra_field'):b.field('extra','other',b'\x06\x09',1)
    if options.get('cctor'):b.method('cctor','.cctor',b'\x00\x00\x01',0x1891,b'\x2a')
    return b.build()
