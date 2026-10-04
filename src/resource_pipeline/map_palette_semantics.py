"""Bounded, alias-tracking abstract map palette proof; no game/CLR execution.

Tile/wall option cells must be concrete at the pre-legend call boundary. Unknown
arithmetic may exist only as typed scalars/colors; structural uses and selected
output cells reject unknown values. Every array alias is preserved in the heap.
"""
from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass
import struct
import math
from .item_texture_aliases import _Program, _Budget, ItemTextureAliasLimits, _CheckpointCancelled, _constant, _require, _shape, _compressed_bytes, _hex
from .id_count_semantics import prove_id_count_program
from .xna_color_semantics import ColorContract, prove_color_intrinsics, VENDOR_SHA256, rgba_from_ints, multiply_rgba
from .server_semantics import SemanticLimits, read_assembly_bytes
from .security import sha256
from .static_il import ILUnsupported, _compressed, json_evidence_size

@dataclass(frozen=True)
class Color:
    packed: int | None

@dataclass(frozen=True)
class Float:
    value: float | None

@dataclass(frozen=True)
class UnknownInt:
    why: str = 'unmodeled gradient arithmetic'

@dataclass(frozen=True)
class Ref:
    slot: int

@dataclass
class Array:
    kind: str
    values: list
    identity: int

def _need(condition, *_):
    _require(condition, 'MAP_TYPE_OR_STATE_BOUNDARY')

def _run_model(p, counts, m, contract, binding):
    il = {i.offset: i for i in m['instructions']}
    stack = []
    locals_ = binding.defaults()
    fields = {}
    pc = 0
    steps = 0
    allocations = []
    maxstack = 0
    tail = False

    def integer(v):
        _need(type(v) is int)
        _need(-2 ** 31 <= v < 2 ** 31)
        return v

    def array(v):
        _need(isinstance(v, Array))
        return v

    def index(a, n):
        n = integer(n)
        _need(0 <= n < len(a.values))
        return n

    def resolve(token):
        return binding.member(token)

    def typekind(token):
        return binding.kind(token)

    def color(v):
        _require(type(v) is Color, 'MAP_COLOR_BY_VALUE_REQUIRED')
        return v

    def address(v):
        _require(type(v) is Ref and 0 <= v.slot < len(locals_) and binding.locals[v.slot] == 'color',
                 'MAP_COLOR_MANAGED_ADDRESS_REQUIRED')
        return v

    def color_receiver(v):
        return color(locals_[address(v).slot])
    while True:
        steps += 1
        p.budget.check()
        _need(steps <= 500000)
        ins = il[pc]
        op = ins.opcode
        a = ins.operand
        next_ = ins.next_offset
        try:
            if 21 <= op <= 32:
                stack.append(_constant(ins))
            elif op == 34:
                stack.append(Float(a))
            elif 6 <= op <= 9:
                stack.append(locals_[op - 6])
            elif 10 <= op <= 13:
                binding.store(locals_, op - 10, stack.pop())
            elif op == 17:
                stack.append(locals_[a])
            elif op == 18:
                _need(binding.locals[a] == 'color')
                stack.append(Ref(a))
            elif op == 19:
                binding.store(locals_, a, stack.pop())
            elif op == 38:
                stack.pop()
            elif op == 126:
                f = resolve(a)
                owner = f['owner']
                name = f['name']
                if owner in ('Terraria.ID.TileID', 'Terraria.ID.WallID') and name == 'Count':
                    stack.append(counts[owner.rsplit('.', 1)[1]]['count'])
                else:
                    _need(owner == 'Terraria.Map.MapHelper' and name in fields)
                    stack.append(fields[name])
            elif op == 128:
                f = resolve(a)
                _need(f['owner'] == 'Terraria.Map.MapHelper')
                value_ = stack.pop()
                binding.store_field(f['name'], value_)
                fields[f['name']] = value_
            elif op == 141:
                n = integer(stack.pop())
                _need(0 <= n <= 65536)
                kind = typekind(a)
                _need(len(allocations) < 4096 and sum((len(x.values) for x in allocations)) + n <= 131072)
                default = Color(0) if kind == 'color' else None if kind == 'color-array' else 0
                obj = Array(kind, [default] * n, len(allocations))
                allocations.append(obj)
                stack.append(obj)
            elif op == 142:
                stack.append(len(array(stack.pop()).values))
            elif op in (154, 163, 148, 147):
                n = stack.pop()
                arr = array(stack.pop())
                v = arr.values[index(arr, n)]
                _need(arr.kind == {154: 'color-array', 163: 'color', 148: 'i4', 147: 'u2'}[op])
                if op == 163:
                    _need(typekind(a) == 'color')
                stack.append(v)
            elif op in (162, 164, 158, 157):
                v = stack.pop()
                n = stack.pop()
                arr = array(stack.pop())
                n = index(arr, n)
                _need(arr.kind == {162: 'color-array', 164: 'color', 158: 'i4', 157: 'u2'}[op])
                if op == 162:
                    _need(isinstance(v, Array) and v.kind == 'color')
                elif op == 164:
                    _need(typekind(a) == 'color' and isinstance(v, Color))
                else:
                    v = integer(v)
                    if op == 157:
                        v &= 65535
                arr.values[n] = v
            elif op in (88, 89, 90, 91):
                b = stack.pop()
                v = stack.pop()
                _require(not (op == 91 and type(b) is int and b == 0), 'MAP_INTEGER_DIVIDE_BY_ZERO')
                if isinstance(v, Float) or isinstance(b, Float):
                    _need(isinstance(v, Float) and isinstance(b, Float))
                    out = Float(None)
                elif isinstance(v, UnknownInt) or isinstance(b, UnknownInt):
                    _need(all(type(x) is int or isinstance(x, UnknownInt) for x in (v, b)))
                    out = UnknownInt()
                else:
                    v = integer(v)
                    b = integer(b)
                    if op == 88:
                        out = v + b
                    elif op == 89:
                        out = v - b
                    elif op == 90:
                        out = v * b
                    else:
                        _need(b)
                        out = abs(v) // abs(b) * (1 if (v >= 0) == (b >= 0) else -1)
                    integer(out)
                stack.append(out)
            elif op in (105, 107, 209, 210):
                v = stack.pop()
                if op == 107:
                    _need(type(v) is int or isinstance(v, (Float, UnknownInt)))
                    stack.append(Float(None))
                elif isinstance(v, (Float, UnknownInt)):
                    stack.append(UnknownInt())
                else:
                    v = integer(v)
                    stack.append(v if op == 105 else v & (65535 if op == 209 else 255))
            elif op == 43:
                next_ = a
            elif op == 45:
                if integer(stack.pop()):
                    next_ = a
            elif op in (50, 49):
                b = integer(stack.pop())
                v = integer(stack.pop())
                if v < b if op == 50 else v <= b:
                    next_ = a
            elif op in (40, 115):
                target = resolve(a)
                owner = target['owner']
                name = target['name']
                if owner == 'Terraria.Lang' and name == 'BuildMapAtlas':
                    _need(not stack and ins.next_offset in il and (il[ins.next_offset].opcode == 42))
                    tail = True
                    break
                if owner == 'Terraria.Map.MapHelper' and name == 'MultiplyMapColor':
                    scale = stack.pop()
                    c = color(stack.pop())
                    _need(isinstance(scale, Float) and scale.value is not None)
                    stack.append(Color(None if c.packed is None else c.packed & 4278190080 | multiply_rgba(c.packed, scale.value) & 16777215))
                else:
                    _need(isinstance(owner, dict) and owner['name'] == 'Microsoft.Xna.Framework.Color')
                    if name == '.ctor':
                        z, y, x = (stack.pop(), stack.pop(), stack.pop())
                        _need(all(type(v) is int or isinstance(v, UnknownInt) for v in (x, y, z)))
                        if any((isinstance(v, UnknownInt) for v in (x, y, z))):
                            c = Color(None)
                        else:
                            c = Color(rgba_from_ints(integer(x), integer(y), integer(z)))
                        if op == 115:
                            stack.append(c)
                        else:
                            ref = address(stack.pop())
                            binding.store(locals_, ref.slot, c)
                    elif name in ('get_Black', 'get_Gray', 'get_LightGray', 'get_Transparent'):
                        stack.append(Color(contract.named[name[4:]]))
                    elif name in ('get_R', 'get_G', 'get_B'):
                        c = color_receiver(stack.pop())
                        stack.append(UnknownInt() if c.packed is None else c.packed >> 8 * 'RGB'.index(name[-1]) & 255)
                    elif name == 'op_Equality':
                        b = color(stack.pop())
                        v = color(stack.pop())
                        _need(b.packed is not None and v.packed is not None)
                        stack.append(int(v == b))
                    else:
                        raise ILUnsupported('MAP_UNSUPPORTED_CALL',offset=pc)
            else:
                raise ILUnsupported('MAP_UNSUPPORTED_INSTRUCTION',offset=pc)
            maxstack = max(maxstack, len(stack))
            _need(maxstack <= binding.maxstack)
            pc = next_
        except (IndexError, KeyError, TypeError, ValueError) as e:
            raise ILUnsupported('MAP_STATE_REJECTED', offset=pc) from e
    _need(tail)
    result = {}
    _require(all(n in fields for n in ('colorLookup','tileLookup','wallLookup','tileOptionCounts','wallOptionCounts')),
             'MAP_REQUIRED_OUTPUT_FIELD_MISSING')
    palette = array(fields['colorLookup'])
    for family, prefix, key in [('tiles', 'tile', 'TileID'), ('walls', 'wall', 'WallID')]:
        lookup = array(fields[prefix + 'Lookup'])
        options = array(fields[prefix + 'OptionCounts'])
        count = counts[key]['count']
        _need(len(lookup.values) == len(options.values) == count)
        rows = []
        for id_ in range(count):
            start = integer(lookup.values[id_])
            n = integer(options.values[id_])
            colors = []
            _need(0 <= start < len(palette.values) and 0 <= n <= 16)
            for j in range(n):
                c = palette.values[index(palette, start + j)]
                _need(isinstance(c, Color) and c.packed is not None)
                colors.append(c.packed)
            rows.append({'id': id_, 'lookup': start, 'optionCount': n, 'packedRgba': colors})
        result[family] = rows
    return {'status': 'PROVEN_PRE_LEGEND_PALETTE_MODEL', 'executedInput': False, 'complete': False, 'factScope': 'PRE_LEGEND_BOUNDARY', 'targetPaletteModelComplete': True, 'wholeInitializerProven': False, 'legendTailNonmutationProven': False, 'publishable': False, 'runtimeSnapshotUsable': False, 'runtimeDependencyBindingVerified': False, 'steps': steps, 'maxStackObserved': maxstack, 'arraysAllocated': len(allocations), 'paletteSlots': len(palette.values), 'unprovenPaletteSlots': sum((isinstance(v, Color) and v.packed is None for v in palette.values)), 'rows': result}


_MAP = 'Terraria.Map.MapHelper'
_FIELDS = {
    'tileOptionCounts': ('i4-array',0x16), 'wallOptionCounts': ('i4-array',0x16),
    'tileLookup': ('u2-array',0x16), 'wallLookup': ('u2-array',0x16),
    'colorLookup': ('color-array',0x11), 'snowTypes': ('u2-array',0x11),
    **{n:('u2',0x11) for n in ('tilePosition','wallPosition','liquidPosition','skyPosition',
       'dirtPosition','rockPosition','hellPosition','wallRangeStart','wallRangeEnd')},
}
_OPS = set(range(6,14)) | set(range(0x15,0x21)) | {0x11,0x12,0x13,0x22,0x26,0x7e,0x80,0x8d,0x8e,
    0x9a,0xa3,0x94,0x93,0xa2,0xa4,0x9e,0x9d,0x58,0x59,0x5a,0x5b,0x69,0x6b,0xd1,0xd2,
    0x2b,0x2d,0x32,0x31,0x28,0x73,0x2a}
_PROFILES = {
 '960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3': ('client',0x06001fd4,
  '23e8aa2bcbeb1e92f7128db73d39ec134d5028ac6a2d5ed68a820990dec689fd'),
 'd87e3faf08637f6be8882c63e7f11fb7e792b0230006309618473ece0f863e1e': ('server',0x06001ef2,
  '51ed178f5ac9a9576c47ebbea837d474ef56633ac179f25b1f204917c6020f2a'),
}


class _Bindings:
    def __init__(self,p,domains):
        from .xna_color_semantics import _core
        self.p=p;self.domains=domains;self.members={};self.field_kinds={}
        _require(type(domains) is dict and all(type(domains.get(n)) is dict for n in ('TileID','WallID')),
                 'MAP_DOMAIN_RECORDS_REQUIRED')
        _,owner=p.owner(_MAP);owner_row,_=p.row(2,p.by_name[_MAP][0])
        _require(owner_row[0]&0x18==0 and not owner_row[0]&0x20 and owner_row[3]&3==1,
                 'MAP_OWNER_LAYOUT_OR_BASE')
        _require(0<owner_row[3]>>2<=min(p.meta.rows[1],0xffffff),'MAP_OWNER_BASE_RID')
        _core(p,0x01000000|owner_row[3]>>2,'System.Object')
        # The array field supplies the exact nominal Color TypeRef signature.
        color_field=p.field(_MAP,'colorLookup');sig=p.fields[color_field]
        _require(sig[:3]==b'\x06\x1d\x11','MAP_COLOR_FIELD_TYPE')
        coded,end=_compressed(sig,3)
        _require(end==len(sig) and coded&3==1 and 0<coded>>2<=min(p.meta.rows[1],0xffffff),
                 'MAP_COLOR_FIELD_RID')
        self.color_rid=coded>>2;self.color_sig=b'\x11'+_compressed_bytes(coded)
        self._xna_type(self.color_rid)
        for name,(kind,flags) in _FIELDS.items():
            token=p.field(_MAP,name);row,_=p.row(4,token&0xffffff)
            _require(row[0]==flags and self.parse(p.fields[token][1:])==kind,'MAP_FIELD_TYPE_OR_FLAGS')
            _require(not any(r[1]==token&0xffffff for r in p.table(16)),'MAP_FIELD_LAYOUT_ALIAS')
            self.members[token]={'owner':_MAP,'name':name};self.field_kinds[name]=kind
        for name in ('TileID','WallID'):
            d=domains[name]
            _require({'owner','fieldToken','wholeInitializerProven','externalTailCountNonmutationProven',
                      'factScope','count','countMethodEvidence'} <= set(d) and type(d['countMethodEvidence']) is dict,
                     'MAP_DOMAIN_RECORDS_REQUIRED')
            owner='Terraria.ID.'+name;token=p.field(owner,'Count',b'\x06\x07')
            m=p.method(owner,'.cctor');row,_=p.row(4,token&0xffffff)
            _require(row[0]==0x36 and d['owner']==owner and d['fieldToken']==_hex(token)
                     and d['wholeInitializerProven'] is True and d['externalTailCountNonmutationProven'] is True
                     and d['factScope']=='INITIALIZER_BOUNDARY' and type(d['count']) is int
                     and 0<d['count']<=65535 and d['count']==_constant(m['instructions'][0])
                     and all(d['countMethodEvidence'].get(k)==m['evidence'][k]
                             for k in ('methodToken','ilSha256','signatureSha256')),'MAP_INDEPENDENT_COUNT_BINDING')
            self.members[token]={'owner':owner,'name':'Count'}
        self.method=p.method(_MAP,'Initialize');m=self.method;row,_=p.row(6,m['token']&0xffffff)
        _require(row[2]==0x96 and m['signature']==b'\x00\x00\x01' and not m['eh'] and m['header']==12,
                 'MAP_METHOD_CONTRACT')
        header=p.meta.reader.uint(m['evidence']['bodyOffset'],2)
        _require(header==0x3013,'MAP_INITIALIZED_LOCALS_REQUIRED')
        self.maxstack=p.meta.reader.uint(m['evidence']['bodyOffset']+2,2)
        _require(0<self.maxstack<=256,'MAP_MAXSTACK')
        blob=m['local'];_require(blob[:1]==b'\x07','MAP_LOCAL_SIGNATURE');n,pos=_compressed(blob,1)
        _require(n<=128,'MAP_LOCAL_COUNT');self.locals=[]
        for _ in range(n):
            kind,pos=self._parse(blob,pos);self.locals.append(kind)
        _require(pos==len(blob),'MAP_LOCAL_SIGNATURE_TRAILING')
        self.mul=p.method(_MAP,'MultiplyMapColor');self.tail=p.method('Terraria.Lang','BuildMapAtlas')
        c=self.color_sig;mul=self.mul;row,_=p.row(6,mul['token']&0xffffff)
        _require(row[2]==0x96 and mul['signature']==b'\x00\x02'+c+c+b'\x0c'
                 and mul['local']==b'\x07\x01\x05' and not mul['eh'],'MAP_MULTIPLY_CONTRACT')
        cap=_shape(mul['instructions'],[(0x0f,0),(0x28,'alpha'),0x0a,2,3,(0x28,'multiply'),(0x10,0),
                                      (0x0f,0),6,(0x28,'setalpha'),2,0x2a])
        for token,name in [(cap['alpha'],'get_A'),(cap['multiply'],'op_Multiply'),(cap['setalpha'],'set_A')]:
            _require(self._xna_member(token)['name']==name,'MAP_MULTIPLY_CALLEE')
        _require((8 if mul['header']==1 else p.meta.reader.uint(mul['evidence']['bodyOffset']+2,2))>=2,
                 'MAP_MULTIPLY_MAXSTACK')
        self.members[mul['token']]={'owner':_MAP,'name':'MultiplyMapColor'}
        row,_=p.row(6,self.tail['token']&0xffffff)
        _require(row[2]==0x96 and self.tail['signature']==b'\x00\x00\x01','MAP_LEGEND_SIGNATURE')
        self.members[self.tail['token']]={'owner':'Terraria.Lang','name':'BuildMapAtlas'}
        il=m['instructions'];_require(len(il)>=2 and il[-1].opcode==0x2a
              and il[-2].opcode==0x28 and il[-2].operand==self.tail['token'],'MAP_TAIL_BOUNDARY')
        for index,i in enumerate(il):
            p.budget.check();_require(i.opcode in _OPS,'MAP_UNSUPPORTED_OPCODE')
            if i.opcode in (0x7e,0x80):
                f=self.member(i.operand)
                _require(i.operand>>24==4 and (i.opcode!=0x80 or f['owner']==_MAP),'MAP_FIELD_EFFECT')
            elif i.opcode in (0x28,0x73):
                target=self.member(i.operand)
                if i.operand==self.tail['token']:_require(index==len(il)-2,'MAP_EARLY_LEGEND_EFFECT')
                _require(i.opcode!=0x73 or target['name']=='.ctor','MAP_NEWOBJ_TARGET')
                _require(target['name'] not in ('get_A','op_Multiply','set_A'),'MAP_UNMODELED_DIRECT_INTRINSIC')
            elif i.opcode in (0x8d,0xa3,0xa4):self.kind(i.operand)
            elif i.opcode in (0x11,0x12,0x13):
                _require(0<=i.operand<len(self.locals),'MAP_LOCAL_INDEX')
                if i.opcode==0x12:_require(self.locals[i.operand]=='color','MAP_LOCAL_ADDRESS_TYPE')
            elif i.opcode in range(6,14):_require((i.opcode-6)%4<len(self.locals),'MAP_SHORT_LOCAL_INDEX')
            elif i.opcode==0x22:_require(math.isfinite(i.operand),'MAP_NONFINITE_LITERAL')
            elif i.opcode==0x2a:_require(index==len(il)-1,'MAP_EARLY_RETURN')
        self.count_fields={k:domains[k]['count'] for k in ('TileID','WallID')}

    def _xna_type(self,rid):
        _require(0<rid<=min(self.p.meta.rows[1],0xffffff),'MAP_XNA_RID')
        row,_=self.p.row(1,rid)
        _require(self.p.meta.string(row[1])=='Color' and self.p.meta.string(row[2])=='Microsoft.Xna.Framework'
                 and row[0]&3==2 and 0<row[0]>>2<=min(self.p.meta.rows[35],0xffffff),'MAP_XNA_TYPE')
        a,_=self.p.row(35,row[0]>>2)
        _require(tuple(a[:4])==(4,0,0,0) and a[4]==0 and self.p.meta.string(a[6])=='Microsoft.Xna.Framework'
                 and not self.p.meta.string(a[7]) and self.p.meta.blob(a[5])[0]==bytes.fromhex('842cf8be1de50553'),
                 'MAP_XNA_ASSEMBLY_IDENTITY')

    def _xna_member(self,token):
        _require(token>>24==10,'MAP_INTRINSIC_MEMBER_TOKEN');r,_=self.p.row(10,token&0xffffff)
        _require(r[0]&7==1 and r[0]>>3==self.color_rid,'MAP_INTRINSIC_OWNER')
        self._xna_type(r[0]>>3);name=self.p.meta.string(r[1]);c=self.color_sig
        sigs={'.ctor':b'\x20\x03\x01\x08\x08\x08',
              **{'get_'+n:b'\x00\x00'+c for n in ('Transparent','Black','Gray','LightGray')},
              **{'get_'+n:b'\x20\x00\x05' for n in 'RGBA'},
              'set_A':b'\x20\x01\x01\x05','op_Multiply':b'\x00\x02'+c+c+b'\x0c',
              'op_Equality':b'\x00\x02\x02'+c+c}
        _require(name in sigs and self.p.meta.blob(r[2])[0]==sigs[name],'MAP_INTRINSIC_SIGNATURE')
        value={'owner':{'name':'Microsoft.Xna.Framework.Color'},'name':name};self.members[token]=value
        return value

    def member(self,token):
        if token not in self.members:return self._xna_member(token)
        return self.members[token]

    def _parse(self,blob,pos=0,depth=0):
        _require(depth<=3 and pos<len(blob),'MAP_TYPE_SIGNATURE_LIMIT');op=blob[pos];pos+=1
        if op in (7,8,12):return {7:'u2',8:'i4',12:'f4'}[op],pos
        if op==0x11:
            coded,pos=_compressed(blob,pos)
            _require(b'\x11'+_compressed_bytes(coded)==self.color_sig,'MAP_VALUE_TYPE_IDENTITY')
            return 'color',pos
        if op==0x1d:
            kind,pos=self._parse(blob,pos,depth+1)
            _require(kind in ('color','color-array','i4','u2'),'MAP_ARRAY_ELEMENT_TYPE')
            return kind+'-array',pos
        raise ILUnsupported('MAP_UNSUPPORTED_TYPE_SIGNATURE')

    def parse(self,blob):
        kind,end=self._parse(blob);_require(end==len(blob),'MAP_TYPE_SIGNATURE_TRAILING');return kind

    def kind(self,token):
        from .xna_color_semantics import _core
        if token>>24==1:
            _require(0<token&0xffffff<=self.p.meta.rows[1],'MAP_TYPE_TOKEN_RID')
            if token&0xffffff==self.color_rid:return 'color'
            row,_=self.p.row(1,token&0xffffff);name=self.p.meta.string(row[2])+'.'+self.p.meta.string(row[1])
            _require(name in ('System.Int32','System.UInt16'),'MAP_ARRAY_CORE_TYPE')
            _core(self.p,token,name);return 'i4' if name=='System.Int32' else 'u2'
        _require(token>>24==27,'MAP_ARRAY_TYPE_TOKEN');r,_=self.p.row(27,token&0xffffff)
        kind=self.parse(self.p.meta.blob(r[0])[0]);_require(kind=='color-array','MAP_NESTED_ARRAY_TYPE');return kind

    def defaults(self):
        return [None if k.endswith('-array') else Color(0) if k=='color' else Float(0.) if k=='f4' else 0
                for k in self.locals]

    def typed(self,kind,value):
        if kind.endswith('-array'):
            _require(value is None or isinstance(value,Array) and value.kind+'-array'==kind,'MAP_ARRAY_VALUE_TYPE')
        elif kind=='color':_require(isinstance(value,Color),'MAP_COLOR_VALUE_TYPE')
        elif kind=='f4':_require(isinstance(value,Float),'MAP_FLOAT_VALUE_TYPE')
        else:
            _require(isinstance(value,UnknownInt) or type(value) is int and
                     (0<=value<=65535 if kind=='u2' else -2**31<=value<2**31),'MAP_INTEGER_VALUE_TYPE')

    def store(self,locals_,slot,value):
        _require(0<=slot<len(self.locals),'MAP_LOCAL_INDEX');self.typed(self.locals[slot],value);locals_[slot]=value

    def store_field(self,name,value):self.typed(self.field_kinds[name],value)


def prove_map_palette(p,domains,contract):
    _require(type(contract) is ColorContract and type(contract.evidence) is dict and contract.evidence.get('wholeBodiesProven') is True
             and type(contract.named) is dict and all(type(contract.named.get(n)) is int and 0<=contract.named[n]<=0xffffffff
                 for n in ('Transparent','Black','Gray','LightGray')),
             'MAP_COLOR_CONTRACT_TYPE')
    binding=_Bindings(p,domains)
    result=_run_model(p,domains,binding.method,contract,binding)
    result.update(schemaVersion=1,methodEvidence=binding.method['evidence'],multiplyMethodEvidence=binding.mul['evidence'],
                  unmodeledTailEvidence=binding.tail['evidence'],countEvidence={k:domains[k] for k in ('TileID','WallID')},
                  colorIntrinsicEvidence=contract.evidence,
                  conditional=True,normalReturnGuaranteed=False,materialBaseReady=False,
                  preconditions=['declared Count model and audited XNA Color bodies with modeled dependency binding',
                   'MapHelper/CLR/core/native initialization has completed normally before the modeled body',
                   'no concurrent/external/reflection/unsafe/native interference with fields or fresh arrays'],
                  unsupported=['legend call and subsequent mutation','runtime dependency resolution or successful startup',
                   'sky/terrain gradient numeric values','localized names, map-option selection, paint, shape/variant policy',
                   'full material source production or publication'])
    json_evidence_size(result,p.budget.limits.evidence_bytes,p.budget.check)
    return result


def extract_map_palette_semantics(input_path:Path,*,xna_path:Path,checkpoint=None):
    check=checkpoint or (lambda:None);check()
    limits=ItemTextureAliasLimits(instructions=1000000,steps=20000000,total_method_bytes=8*1024*1024,
                                  evidence_bytes=4*1024*1024,wall_seconds=120)
    raw=read_assembly_bytes(input_path,SemanticLimits(),check);digest=sha256(raw)
    _require(digest in _PROFILES,'MAP_UNSUPPORTED_SOURCE_PROFILE')
    vendor=read_assembly_bytes(xna_path,SemanticLimits(input_bytes=2*1024*1024),check)
    _require(sha256(vendor)==VENDOR_SHA256,'MAP_UNSUPPORTED_XNA_PROFILE')
    try:
        budget=_Budget(limits,check);p=_Program(raw,budget);v=_Program(vendor,budget)
        # Full vendor hash binding is separate from runtime loader verification.
        contract=prove_color_intrinsics(v);independent=prove_id_count_program(p);domains=independent['domains']
        m=p.method(_MAP,'Initialize');role,token,il_hash=_PROFILES[digest]
        _require(m['token']==token and m['evidence']['ilSha256']==il_hash,'MAP_METHOD_PROFILE')
        result=prove_map_palette(p,domains,contract)
        result.update(inputSha256=digest,sourceRole=role,gameVersion='1.4.5.8',modeledXnaDependencySha256=VENDOR_SHA256,
                      modeledXnaDependency={'assemblyName':'Microsoft.Xna.Framework','version':'4.0.0.0','sha256':VENDOR_SHA256},
                      modeledReLogicDependency=independent['modeledReLogicDependency'],
                      countDependencyEvidence=independent['dependencyEvidence'],
                      countDependencyPreconditions=independent['preconditions'])
        json_evidence_size(result,limits.evidence_bytes,budget.check)
        return result
    except _CheckpointCancelled as exc:raise exc.original
