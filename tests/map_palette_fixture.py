"""Original map palette PE: tiny independent counts, colors and alias scenarios."""
import struct
from id_count_fixture import Image,token,ldc


def fixture(**options):
    options={**options,'assembly_keys':{'Microsoft.Xna.Framework':bytes.fromhex('842cf8be1de50553'),**options.get('assembly_keys',{})}}
    b=Image('OriginalMap',options);obj=b.ref('System.Object');ids=b.ref('ReLogic.Reflection.IdDictionary','ReLogic')
    color=b.ref('Microsoft.Xna.Framework.Color','Microsoft.Xna.Framework');i4=b.ref('System.Int32');u2=b.ref('System.UInt16')
    c=b.typeref('Microsoft.Xna.Framework.Color',value=True);b.type('<Module>',flags=0,base=0)
    count_fields={}
    for index,name in enumerate(('TileID','WallID')):
        rid=b.type(name,'Terraria.ID');count=b.field(name+'.Count','Count',b'\x06\x07',0x36)
        b.field(name+'.Search','Search',b'\x06'+b.typeref('ReLogic.Reflection.IdDictionary'),0x36)
        literal=b.field(name+'.Literal','Example'+str(index),b'\x06\x07',0x8056)
        b.rows.setdefault(11,[]).append(struct.pack('<HHH',7,(literal&0xffffff)<<2,b.blob(struct.pack('<H',0))))
        member=b.member(name+'.Create',ids,'Create',b'\x10\x02\x00'+b.typeref('ReLogic.Reflection.IdDictionary'))
        spec=b.spec(name+'.Create',member,(b.local(rid),b'\x07'))
        count_fields[name]=count;count_value=options.get('counts',{}).get(name,2-index)
        b.method(name+'.cctor','.cctor',b'\x00\x00\x01',0x1891,
                 ldc(count_value)+token(0x80,count)+token(0x28,spec)+token(0x80,count+1)+b'\x2a')
    b.type('MapHelper','Terraria.Map',flags=0x100181);f={}
    definitions=[('tileOptionCounts',b'\x1d\x08',0x16),('wallOptionCounts',b'\x1d\x08',0x16),
        ('tileLookup',b'\x1d\x07',0x16),('wallLookup',b'\x1d\x07',0x16),
        ('colorLookup',b'\x1d'+c,0x11),('snowTypes',b'\x1d\x07',0x11)]
    definitions += [(n,b'\x07',0x11) for n in ('tilePosition','wallPosition','liquidPosition','skyPosition','dirtPosition','rockPosition','hellPosition','wallRangeStart','wallRangeEnd')]
    for name,sig,flags in definitions:f[name]=b.field(name,name,b'\x06'+sig,flags)
    init=b.method('initialize','Initialize',b'\x00\x00\x01',0x96,locals=b'\x07\x02'+c+b'\x1d'+c)
    mul=b.method('multiply','MultiplyMapColor',b'\x00\x02'+c+c+b'\x0c',0x96,locals=b'\x07\x01\x05')
    b.type('Lang','Terraria',flags=0x100181);tail=b.method('legend','BuildMapAtlas',b'\x00\x00\x01',0x96,b'\x2a')
    members={}
    for name,sig in [('.ctor',b'\x20\x03\x01\x08\x08\x08'),('get_A',b'\x20\x00\x05'),
                     ('set_A',b'\x20\x01\x01\x05'),('op_Multiply',b'\x00\x02'+c+c+b'\x0c'),
                     ('get_Black',b'\x00\x00'+c),('get_R',b'\x20\x00\x05'),('op_Equality',b'\x00\x02\x02'+c+c)]:
        members[name]=b.member(name,color,name,sig)
    for method in b.methods:
        if method['key']=='multiply':method['code']=b'\x0f\x00'+token(0x28,members['get_A'])+b'\x0a\x02\x03'+token(0x28,members['op_Multiply'])+b'\x10\x00\x0f\x00\x06'+token(0x28,members['set_A'])+b'\x02\x2a'
    code=b''
    for name,size,element in [('tileOptionCounts',2,i4),('wallOptionCounts',1,i4),('tileLookup',2,u2),('wallLookup',1,u2)]:
        code+=ldc(size)+token(0x8d,0x01000000|element)+token(0x80,f[name])
        for index in range(size):
            value=1 if 'Option' in name else index+1 if name=='tileLookup' else 3
            code+=token(0x7e,f[name])+ldc(index)+ldc(value)+bytes((0x9e if 'Option' in name else 0x9d,))
    code+=ldc(5)+token(0x8d,0x01000000|color)+token(0x80,f['colorLookup'])
    for index,channels in enumerate(((10,20,30),(40,50,60),(70,80,90)),1):
        code+=token(0x7e,f['colorLookup'])+ldc(index)+b''.join(ldc(x) for x in channels)+token(0x73,members['.ctor'])+token(0xa4,0x01000000|color)
    if options.get('named'):
        code+=token(0x7e,f['colorLookup'])+ldc(1)+token(0x28,members['get_Black'])+token(0xa4,0x01000000|color)
    if 'scale' in options:
        code+=token(0x7e,f['colorLookup'])+ldc(1)+token(0x7e,f['colorLookup'])+ldc(1)+token(0xa3,0x01000000|color)+b'\x22'+struct.pack('<f',options['scale'])+token(0x28,mul)+token(0xa4,0x01000000|color)
    if options.get('unknown'):
        # Real alias, not a copied array; selected-cell mutation must be rejected.
        code+=token(0x7e,f['colorLookup'])+b'\x0b\x07'+ldc(options.get('unknown_index',4))
        code+=b'\x22'+struct.pack('<f',0.25)+b'\x69'+ldc(77)+ldc(88)+token(0x73,members['.ctor'])+token(0xa4,0x01000000|color)
    if options.get('opaque_pointer') or options.get('opaque_arithmetic'):
        code+=token(0x7e,f['colorLookup'])+ldc(4)+b'\x22'+struct.pack('<f',0.25)+b'\x69'
        code+=token(0x7e,f['colorLookup'])
        if options.get('opaque_arithmetic'):code+=b'\x58'+ldc(77)
        code+=ldc(88)+token(0x73,members['.ctor'])+token(0xa4,0x01000000|color)
    if options.get('unknown_index_use'):
        code+=token(0x7e,f['colorLookup'])+b'\x22'+struct.pack('<f',0.25)+b'\x69'+token(0xa3,0x01000000|color)+b'\x26'
    if options.get('unknown_control'):
        code+=b'\x22'+struct.pack('<f',0.25)+b'\x69\x2d\x00'
    if options.get('wrong_local_type'):code+=token(0x7e,f['tileLookup'])+b'\x0b'
    if options.get('huge_array'):code+=ldc(131073)+token(0x8d,0x01000000|color)+b'\x26'
    if options.get('invalid_bounds'):code+=token(0x7e,f['tileLookup'])+ldc(0)+ldc(-1)+b'\x9d'
    if options.get('ref_multiply'):
        code+=token(0x7e,f['colorLookup'])+ldc(1)+b'\x12\x00\x22'+struct.pack('<f',1.0)+token(0x28,mul)+token(0xa4,0x01000000|color)
    if options.get('ref_equality'):code+=b'\x12\x00\x12\x00'+token(0x28,members['op_Equality'])+b'\x26'
    if options.get('value_getter'):code+=token(0x7e,f['colorLookup'])+ldc(1)+token(0xa3,0x01000000|color)+token(0x28,members['get_R'])+b'\x26'
    if options.get('address_getter'):code+=b'\x12\x00'+token(0x28,members['get_R'])+b'\x26'
    if options.get('value_ctor'):code+=token(0x7e,f['colorLookup'])+ldc(1)+token(0xa3,0x01000000|color)+ldc(1)+ldc(2)+ldc(3)+token(0x28,members['.ctor'])
    if options.get('opaque_zero_division'):code+=b'\x22'+struct.pack('<f',0.25)+b'\x69\x16\x5b\x26'
    if options.get('unknown_effect'):code+=b'\x00'
    if options.get('empty_outputs'):code=b''
    code+=token(0x28,tail)+b'\x2a'
    for method in b.methods:
        if method['key']=='initialize':method['code']=code
    return b.build()
