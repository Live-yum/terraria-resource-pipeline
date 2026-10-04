"""Original small PE/CLI images for ID Count proofs; no game bytes or tables.

The byte writer is deliberately independent of the proof's IL grammar. Names,
IDs, metadata tokens and method bodies below belong to this synthetic model.
"""
import struct


def compressed(n):
    if n<128:return bytes((n,))
    if n<16384:return bytes((0x80|(n>>8),n&255))
    return bytes((0xc0|(n>>24),(n>>16)&255,(n>>8)&255,n&255))

def token(op,n):return (bytes((op,)) if op<256 else bytes((0xfe,op&255)))+struct.pack('<I',n)
def ldc(n):return token(0x20,n&0xffffffff)

def assemble(items):
    labels={};at=0;ops=[]
    for x in items:
        if isinstance(x,str):labels[x]=at;continue
        op,arg=x if isinstance(x,tuple) else (x,None)
        size=1 if op<256 else 2
        size+=1 if op in (0x0f,0x1f,0x2c,0x2d,0x2f) else 4 if arg is not None else 0
        ops.append((at,op,arg,size));at+=size
    out=b''
    for at,op,arg,size in ops:
        head=bytes((op,)) if op<256 else bytes((0xfe,op&255))
        if op in (0x2c,0x2d,0x2f):head+=struct.pack('<b',labels[arg]-at-size)
        elif op in (0x0f,0x1f):head+=bytes((arg&255,))
        elif arg is not None:head+=struct.pack('<I',arg&0xffffffff)
        out+=head
    return out


class Image:
    def __init__(self,name='ReLogic',options=None):
        self.options=options or {};self.strings=bytearray(b'\0');self.blobs=bytearray(b'\0');self.us=bytearray(b'\0')
        self.rows={};self.types=[];self.fields=[];self.methods=[];self.refs={};self.members={};self.specs={};self.assemblies={}
        self.name=name
    def text(self,v):
        at=len(self.strings);self.strings.extend(v.encode()+b'\0');return at
    def blob(self,v):
        at=len(self.blobs);self.blobs.extend(compressed(len(v))+v);return at
    def string(self,v):
        at=len(self.us);raw=v.encode('utf-16le')+b'\0';self.us.extend(compressed(len(raw))+raw);return 0x70000000|at
    def assembly(self,name):
        if name in self.assemblies:return self.assemblies[name]
        version=(1,0,0,0) if name=='ReLogic' else (4,0,0,0)
        opts=self.options;key=b'' if name=='ReLogic' else bytes.fromhex('b77a5c561934e089')
        key=opts.get('assembly_keys',{}).get(name,key)
        r=struct.pack('<HHHHIHHHH',*version,opts.get('assembly_flags',{}).get(name,0),self.blob(key),
                      self.text(opts.get('assembly_names',{}).get(name,name)),0,0)
        self.rows.setdefault(35,[]).append(r);n=len(self.rows[35]);self.assemblies[name]=n;return n
    def ref(self,name,assembly='mscorlib'):
        namespace,_,short=name.rpartition('.');n=len(self.refs)+1
        short=self.options.get('ref_names',{}).get(name,short)
        self.rows.setdefault(1,[]).append(struct.pack('<HHH',self.assembly(assembly)*4+2,self.text(short),self.text(namespace)))
        self.refs[name]=n;return n
    def typeref(self,name,value=False):return bytes((0x11 if value else 0x12,))+compressed(self.refs[name]*4+1)
    def local(self,n):return b'\x12'+compressed(n*4)
    def gen(self,name,*args,value=False):return b'\x15'+self.typeref(name,value)+compressed(len(args))+b''.join(args)
    def typespec(self,sig):
        self.rows.setdefault(27,[]).append(struct.pack('<H',self.blob(sig)));return len(self.rows[27])
    def type(self,name,namespace='',flags=0x100001,base=5):
        n=len(self.types)+1;opts=self.options
        self.types.append((opts.get('type_flags',{}).get(n,flags),self.text(name),self.text(namespace),
                           opts.get('type_base',{}).get(n,base),len(self.fields)+1,len(self.methods)+1));return n
    def field(self,key,name,sig,flags):
        n=len(self.fields)+1;opts=self.options
        self.fields.append((opts.get('field_flags',{}).get(key,flags),self.text(opts.get('field_names',{}).get(key,name)),
                           self.blob(opts.get('field_signatures',{}).get(key,sig))))
        return 0x04000000|n
    def method(self,key,name,sig,flags,code=b'',locals=b''):
        opts=self.options;n=len(self.methods)+1
        loc=0
        if locals:
            self.rows.setdefault(17,[]).append(struct.pack('<H',self.blob(opts.get('locals',{}).get(key,locals))));loc=0x11000000|len(self.rows[17])
        self.methods.append({'key':key,'row':[0,opts.get('impl_flags',{}).get(key,0),opts.get('method_flags',{}).get(key,flags),
                    self.text(name),self.blob(opts.get('method_signatures',{}).get(key,sig)),1],
                    'code':code,'local':loc})
        return 0x06000000|n
    def member(self,key,owner,name,sig,*,generic_owner=False):
        opts=self.options;parent=(owner<<3)|(4 if generic_owner else 1)
        self.rows.setdefault(10,[]).append(struct.pack('<HHH',opts.get('member_owners',{}).get(key,parent),
             self.text(opts.get('member_names',{}).get(key,name)),self.blob(opts.get('member_signatures',{}).get(key,sig))))
        token_=0x0a000000|len(self.rows[10]);self.members[key]=token_;return token_
    def spec(self,key,target,args):
        sig=b'\x0a'+compressed(len(args))+b''.join(args)
        self.rows.setdefault(43,[]).append(struct.pack('<HH',((target&0xffffff)<<1)|(target>>24==10),
                       self.blob(self.options.get('spec_signatures',{}).get(key,sig))))
        tok=0x2b000000|len(self.rows[43]);self.specs[key]=tok;return tok
    def build(self):
        self.rows[0]=[struct.pack('<HHHHH',0,self.text(self.name+'.dll'),1,0,0)]
        self.rows[2]=[struct.pack('<IHHHHH',*r) for r in self.types]
        self.rows[4]=[struct.pack('<HHH',*r) for r in self.fields]
        self.rows[32]=[struct.pack('<IHHHHIHHH',0,1,0,0,0,0,0,self.text(self.name),0)]
        def meta():
            self.rows[6]=[struct.pack('<IHHHHH',*m['row']) for m in self.methods]
            tables=struct.pack('<IBBBBQQ',0,2,0,0,1,sum(1<<k for k in self.rows),0)
            tables+=b''.join(struct.pack('<I',len(self.rows[k])) for k in sorted(self.rows))
            tables+=b''.join(b''.join(self.rows[k]) for k in sorted(self.rows))
            streams={'#~':tables,'#Strings':bytes(self.strings),'#Blob':bytes(self.blobs),'#GUID':bytes(16),'#US':bytes(self.us)}
            root=bytearray(struct.pack('<IHHII',0x424a5342,1,1,0,12)+b'v4.0.30319\0\0'+struct.pack('<HH',0,len(streams)))
            cursor=len(root)+sum(8+((len(k)+4)&~3) for k in streams)
            for name,raw in streams.items():
                root.extend(struct.pack('<II',cursor,len(raw)));n=name.encode()+b'\0';root.extend(n+b'\0'*(-len(n)%4));cursor+=len(raw)
            for raw in streams.values():root.extend(raw)
            return bytes(root)
        metadata=meta();start=(0x300+len(metadata)+3)&~3;bodies=bytearray()
        for method in self.methods:
            key=method['key'];code=self.options.get('method_code',{}).get(key,method['code'])
            method['row'][0]=0x2000+start+len(bodies)-0x200
            bodies.extend(struct.pack('<HHII',self.options.get('header_flags',{}).get(key,0x3013),
                    self.options.get('maxstack',{}).get(key,8),len(code),self.options.get('local_tokens',{}).get(key,method['local'])))
            bodies.extend(code);bodies.extend(b'\0'*(-len(bodies)%4))
        metadata=meta();size=(start+len(bodies)+511)//512*512;out=bytearray(size)
        def put(at,v):out[at:at+len(v)]=v
        put(0,b'MZ');put(0x3c,struct.pack('<I',0x80));put(0x80,b'PE\0\0')
        put(0x84,struct.pack('<HHIIIHH',0x14c,1,0,0,0,224,0x102));put(0x98,struct.pack('<H',0x10b))
        directory=0x98+96;put(directory-4,struct.pack('<I',16));put(directory+14*8,struct.pack('<II',0x2000,72))
        put(0x98+224,b'.text\0\0\0'+struct.pack('<IIII',size-0x200,0x2000,size-0x200,0x200))
        put(0x200,struct.pack('<IHHIIIIII',72,2,5,0x2100,len(metadata),1,0,0,0))
        put(0x300,metadata);put(start,bodies);return bytes(out)


def dependency_fixture(**options):
    b=Image(options=options)
    refs=['System.Object','System.Type','System.Reflection.FieldInfo','System.Reflection.MemberInfo','System.Convert',
          'System.String','System.Exception','System.RuntimeTypeHandle','System.Reflection.BindingFlags',
          'System.Collections.Generic.Dictionary`2','System.Collections.Generic.KeyValuePair`2','System.Func`2',
          'System.Action`1','System.Collections.Generic.IEnumerable`1','System.Collections.Generic.List`1']
    for n in refs:b.ref(n)
    b.ref('System.Linq.Enumerable','System.Core')
    typ=b.typeref('System.Type');field=b.typeref('System.Reflection.FieldInfo');pair=b.gen(refs[10],b'\x0e',b'\x08',value=True)
    dictionary=b.gen(refs[9],b'\x0e',b'\x08');reverse=b.gen(refs[9],b'\x08',b'\x0e')
    func=lambda a,c:b.gen('System.Func`2',a,c)
    enum=lambda a:b.gen(refs[13],a)
    list_=lambda a:b.gen(refs[14],a)
    action=lambda a:b.gen('System.Action`1',a)
    m=lambda ret,*args,this=False,generic=0:bytes(((0x20 if this else 0)|(0x10 if generic else 0),))+ (compressed(generic) if generic else b'')+compressed(len(args))+ret+b''.join(args)
    b.type('<Module>',flags=0,base=0)
    b.type('IdDictionary','ReLogic.Reflection')
    f={}
    for k,n,s,flags in [('names','_nameToId',dictionary,0x21),('reverse','_idToName',reverse,1),('count','Count',b'\x08',0x26)]:
        f[k]=b.field(k,n,b'\x06'+s,flags)
    mt={}
    mt['ctor']=b.method('ctor','.ctor',m(b'\x01',b'\x08',this=True),0x1881)
    mt['create']=b.method('create','Create',m(b.local(2),typ,typ),0x96,locals=b'\x07\x03'+b.local(3)+b'\x08'+field)
    mt['generic']=b.method('generic','Create',m(b.local(2),generic=2),0x96)
    b.type('<>c__DisplayClass15_0',flags=0x100103)
    f['type']=b.field('type','idType',b'\x06'+typ,6);f['dictionary']=b.field('dictionary','dictionary',b'\x06'+b.local(2),6)
    mt['closure']=b.method('closure','.ctor',m(b'\x01',this=True),0x1886)
    mt['predicate']=b.method('predicate','<Create>b__1',m(b'\x02',field,this=True),0x83)
    mt['action']=b.method('action','<Create>b__2',m(b'\x01',field,this=True),0x83,locals=b'\x07\x01\x08')
    b.type('<>c',flags=0x102103)
    f['singleton']=b.field('singleton','<>9',b'\x06'+b.local(4),0x36)
    for k,n,s in [('predicate','<>9__15_0',func(field,b'\x02')),('value','<>9__15_3',func(pair,b'\x08')),('key','<>9__15_4',func(pair,b'\x0e'))]:
        f[k]=b.field(k,n,b'\x06'+s,0x16)
    mt['init']=b.method('init','.cctor',m(b'\x01'),0x1891)
    mt['singleton']=b.method('singleton','.ctor',m(b'\x01',this=True),0x1886)
    mt['named']=b.method('named','<Create>b__15_0',m(b'\x02',field,this=True),0x83)
    mt['value']=b.method('value','<Create>b__15_3',m(b'\x08',pair,this=True),0x83)
    mt['key']=b.method('key','<Create>b__15_4',m(b'\x0e',pair,this=True),0x83)
    if 'extra_code' in options:
        b.type('OriginalExtra','Fixture');b.method('extra','OriginalExtra',m(b'\x01'),0x96,options['extra_code'])
    if options.get('extra_initializer'):
        b.type('OriginalExtra','Fixture');b.method('extra_init','.cctor',m(b'\x01'),0x1891,b'\x2a')
    b.rows[41]=[struct.pack('<HH',3,2),struct.pack('<HH',4,2)]
    b.rows[42]=[struct.pack('<HHHH',n,0,7,b.text('OriginalT'+str(n))) for n in range(2)]
    # Trusted intrinsics and exact generic owners are all declared separately.
    ref=lambda k,owner,name,sig:b.member(k,b.refs[owner],name,sig)
    gen=lambda k,owner,name,sig:b.member(k,b.typespec(owner),name,sig,generic_owner=True)
    ref('object','System.Object','.ctor',m(b'\x01',this=True));gen('dict',dictionary,'.ctor',m(b'\x01',this=True))
    ref('handle','System.Type','GetTypeFromHandle',m(typ,b.typeref('System.RuntimeTypeHandle',True)))
    ref('fieldType',refs[2],'get_FieldType',m(typ,this=True));ref('typeEquals','System.Type','op_Equality',m(b'\x02',typ,typ))
    ref('name',refs[3],'get_Name',m(b'\x0e',this=True));ref('stringEquals','System.String','op_Equality',m(b'\x02',b'\x0e',b'\x0e'))
    ref('read',refs[2],'GetValue',m(b'\x1c',b'\x1c',this=True));ref('convert','System.Convert','ToInt32',m(b'\x08',b'\x1c'))
    gen('add',dictionary,'Add',m(b'\x01',b'\x13\x00',b'\x13\x01',this=True))
    gen('pairValue',pair,'get_Value',m(b'\x13\x01',this=True));gen('pairKey',pair,'get_Key',m(b'\x13\x00',this=True))
    ref('getFields','System.Type','GetFields',m(b'\x1d'+field,this=True))
    ref('staticFields','System.Type','GetFields',m(b'\x1d'+field,b.typeref(refs[8],True),this=True))
    ref('notnull',refs[2],'op_Inequality',m(b'\x02',field,field));ref('exception','System.Exception','.ctor',m(b'\x01',b'\x0e',this=True))
    for k,owner in [('predicateCtor',func(field,b'\x02')),('actionCtor',action(field)),('valueCtor',func(pair,b'\x08')),('keyCtor',func(pair,b'\x0e'))]:
        gen(k,owner,'.ctor',m(b'\x01',b'\x1c',b'\x18',this=True))
    gen('each',list_(field),'ForEach',m(b'\x01',action(b'\x13\x00'),this=True))
    t0,t1,t2=b'\x1e\x00',b'\x1e\x01',b'\x1e\x02'
    for k,name,sig,args in [('first','FirstOrDefault',m(t0,enum(t0),func(t0,b'\x02'),generic=1),(field,)),
       ('where','Where',m(enum(t0),enum(t0),func(t0,b'\x02'),generic=1),(field,)),
       ('list','ToList',m(list_(t0),enum(t0),generic=1),(field,)),
       ('reverse','ToDictionary',m(b.gen(refs[9],t1,t2),enum(t0),func(t0,t1),func(t0,t2),generic=3),(pair,b'\x08',b'\x0e'))]:
        target=ref(k,'System.Linq.Enumerable',name,sig);b.spec(k,target,args)
    mr=b.members;sp=b.specs
    codes={
      'ctor':[2,(0x73,mr['dict']),(0x7d,f['names']),2,(0x28,mr['object']),2,3,(0x7d,f['count']),0x2a],
      'generic':[(0xd0,0x1b000000|b.typespec(b'\x1e\x00')),(0x28,mr['handle']),
                 (0xd0,0x1b000000|b.typespec(b'\x1e\x01')),(0x28,mr['handle']),(0x28,mt['create']),0x2a],
      'closure':[2,(0x28,mr['object']),0x2a],'singleton':[2,(0x28,mr['object']),0x2a],
      'predicate':[3,(0x6f,mr['fieldType']),2,(0x7b,f['type']),(0x28,mr['typeEquals']),0x2a],
      'named':[3,(0x6f,mr['name']),(0x72,b.string('Count')),(0x28,mr['stringEquals']),0x2a],
      'value':[(0x0f,1),(0x28,mr['pairValue']),0x2a],'key':[(0x0f,1),(0x28,mr['pairKey']),0x2a],
      'init':[(0x73,mt['singleton']),(0x80,f['singleton']),0x2a],
      'action':[3,0x14,(0x6f,mr['read']),(0x28,mr['convert']),0x0a,6,2,(0x7b,f['dictionary']),
        (0x7b,f['count']),(0x2f,'return'),2,(0x7b,f['dictionary']),(0x7b,f['names']),3,(0x6f,mr['name']),6,(0x6f,mr['add']),'return',0x2a],
    }
    codes['create']=[(0x73,mt['closure']),0x0a,6,3,(0x7d,f['type']),(0x20,2147483647),0x0b,2,(0x6f,mr['getFields']),
      (0x7e,f['predicate']),0x25,(0x2d,'first'),0x26,(0x7e,f['singleton']),(0xfe06,mt['named']),
      (0x73,mr['predicateCtor']),0x25,(0x80,f['predicate']),'first',(0x28,sp['first']),0x0c,8,0x14,(0x28,mr['notnull']),
      (0x2c,'allocate'),8,0x14,(0x6f,mr['read']),(0x28,mr['convert']),0x0b,7,(0x2d,'allocate'),
      (0x72,b.string('Synthetic positive Count required')),(0x73,mr['exception']),0x7a,'allocate',6,7,(0x73,mt['ctor']),
      (0x7d,f['dictionary']),2,(0x1f,24),(0x6f,mr['staticFields']),6,(0xfe06,mt['predicate']),(0x73,mr['predicateCtor']),
      (0x28,sp['where']),(0x28,sp['list']),6,(0xfe06,mt['action']),(0x73,mr['actionCtor']),(0x6f,mr['each']),
      6,(0x7b,f['dictionary']),6,(0x7b,f['dictionary']),(0x7b,f['names']),(0x7e,f['value']),0x25,(0x2d,'key'),0x26,
      (0x7e,f['singleton']),(0xfe06,mt['value']),(0x73,mr['valueCtor']),0x25,(0x80,f['value']),'key',
      (0x7e,f['key']),0x25,(0x2d,'reverse'),0x26,(0x7e,f['singleton']),(0xfe06,mt['key']),(0x73,mr['keyCtor']),
      0x25,(0x80,f['key']),'reverse',(0x28,sp['reverse']),(0x7d,f['reverse']),6,(0x7b,f['dictionary']),0x2a]
    for method in b.methods:
        if method['key'] in codes:method['code']=assemble(codes[method['key']])
    if options.get('alias_cache'):
        # MemberRef same-owner alias must be resolved by the all-method scan.
        b.rows.setdefault(10,[]).append(struct.pack('<HHH',4<<3,b.text('<>9__15_0'),b.blob(b'\x06'+func(field,b'\x02'))))
        alias=0x0a000000|len(b.rows[10])
        b.type('OriginalAlias','Fixture')
        b.method('alias_extra','OriginalAliasWriter',m(b'\x01'),0x96,b'\x14'+token(0x80,alias)+b'\x2a')
    if options.get('spoof_core_type'):
        b.type('Dictionary`2','System.Collections.Generic')
        # Same generic arguments, but a local impostor owns the constructor.
        row=b.rows[27][0];blob_index=struct.unpack('<H',row)[0]
        assert b.blobs[blob_index+1:blob_index+3]==b'\x15\x12'
        b.blobs[blob_index+3]=5<<2
    return b.build()


def game_fixture(**options):
    b=Image('OriginalCounts',options)
    b.ref('System.Object');b.ref('ReLogic.Reflection.IdDictionary','ReLogic')
    b.type('<Module>',flags=0,base=0)
    for index,name in enumerate(('ItemID','TileID','WallID')):
        rid=b.type(name,'Terraria.ID');element=6 if name=='ItemID' else 7
        count=b.field(name+'.Count','Count',bytes((6,element)),0x36)
        search=b.field(name+'.Search','Search',b'\x06'+b.typeref('ReLogic.Reflection.IdDictionary'),0x36)
        literal=b.field(name+'.Literal','OriginalLiteral'+str(index),bytes((6,element)),0x8056)
        b.rows.setdefault(11,[]).append(struct.pack('<HHH',options.get('constant_type',element),(literal&0xffffff)<<2,
                                  b.blob(options.get('constant_blob',struct.pack('<H',index)))))
        member=b.member(name+'.Create',2,'Create',b'\x10\x02\x00'+b.typeref('ReLogic.Reflection.IdDictionary'))
        spec=b.spec(name+'.Create',member,(b.local(rid),bytes((element,))))
        code=ldc(options.get('counts',{}).get(name,13+index))+token(0x80,count)+token(0x28,spec)+token(0x80,search)+b'\x2a'
        b.method(name+'.cctor','.cctor',b'\x00\x00\x01',0x1891,code)
    return b.build()
