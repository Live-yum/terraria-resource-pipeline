"""Bounded abstract evaluation of numeric Item stage IL, never CLR execution.

Only a closed opcode/value subset is modeled. No eval, imports of input code,
assembly loading, JIT, subprocess, native call, or uploaded method invocation.
Facts describe SetDefaultsN stages, not complete Item.SetDefaults results.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import struct
import time

from .security import PipelineError, canonical_json


@dataclass(frozen=True)
class StaticILLimits:
    ids: int = 256
    sample_ids: int = 32
    method_bytes: int = 1024 * 1024
    method_instructions: int = 250_000
    total_decoded_instructions: int = 1_000_000
    total_method_bytes: int = 8 * 1024 * 1024
    method_steps: int = 20_000
    total_steps: int = 2_000_000
    stack: int = 256
    locals: int = 256
    call_depth: int = 8
    methods: int = 256
    method_index: int = 4096
    fields: int = 1024
    metadata_name_bytes: int = 1024 * 1024
    evidence_bytes: int = 16 * 1024 * 1024
    wall_seconds: float = 30

    def __post_init__(self):
        for name,value in vars(self).items():
            if name=='wall_seconds':
                if type(value) not in (int,float) or not 0<value<=120:raise ValueError('Invalid static IL wall-time budget')
            elif type(value) is not int or value<=0:raise ValueError('Invalid static IL integer budget')
        if self.sample_ids<3 or self.sample_ids>self.ids:raise ValueError('Invalid static IL sample budget')
        if self.evidence_bytes<16*1024:raise ValueError('Static IL evidence budget needs a bounded diagnostic envelope')


class ILUnsupported(Exception):
    def __init__(self, code, *, offset=None, token=None):
        self.code, self.offset, self.token = code, offset, token
        super().__init__(code)


class EvidenceSizeLimit(PipelineError):
    pass


def json_evidence_size(value, maximum, checkpoint=None):
    """Exact canonical UTF-8 size, without materializing the complete JSON.

    Strings are escaped/encoded in at most 4096-character chunks; even a large
    scalar cannot allocate an unbounded iterencode chunk before its size check.
    The closed evidence schema has string keys, finite scalars and depth <=128.
    """
    if type(maximum) is not int or maximum<0:raise ValueError('Invalid evidence byte budget')
    checkpoint=checkpoint or (lambda:None)
    total=0;ancestors=set();short_strings={}
    def add(size):
        nonlocal total
        total+=size
        if total>maximum:raise EvidenceSizeLimit('EVIDENCE_JSON_BYTE_LIMIT')
    def visit(item,depth):
        checkpoint()
        if depth>128:raise PipelineError('Evidence JSON depth limit')
        if isinstance(item,str):
            # Immutable short values repeat heavily in evidence keys. Bound the
            # per-call cache by both entries and scalar length; no container or
            # caller-owned result is cached across validation calls.
            cached=short_strings.get(item) if len(item)<=128 else None
            if cached is not None:
                add(cached)
            else:
                size=2
                add(2)
                for start in range(0,len(item),4096):
                    checkpoint()
                    # Standard JSON encoder, exact ensure_ascii=False escaping.
                    part=len(json.encoder.encode_basestring(item[start:start+4096]).encode('utf-8'))-2
                    add(part);size+=part
                if len(item)<=128 and len(short_strings)<4096:short_strings[item]=size
        elif item is None:
            add(4)
        elif type(item) is bool:
            add(4 if item else 5)
        elif type(item) is int:
            add(len(str(item)))
        elif type(item) is float:
            add(len(json.dumps(item,allow_nan=False).encode('utf-8')))
        elif type(item) in (dict,list,tuple):
            identity=id(item)
            if identity in ancestors:raise PipelineError('Circular evidence JSON')
            ancestors.add(identity)
            add(2+max(0,len(item)-1))
            if type(item) is dict:
                for key,child in item.items():
                    if not isinstance(key,str):raise PipelineError('Evidence JSON keys must be strings')
                    visit(key,depth+1);add(1);visit(child,depth+1)
            else:
                for child in item:visit(child,depth+1)
            ancestors.remove(identity)
        else:raise PipelineError('Unsupported evidence JSON value')
    try:visit(value,0)
    except (UnicodeError,ValueError,RecursionError) as exc:
        if isinstance(exc,PipelineError):raise
        raise PipelineError('Invalid canonical evidence JSON') from exc
    return total


def bounded_evidence_json(value, maximum, checkpoint=None):
    """Preflight before canonical_json creates its complete string/byte copies."""
    json_evidence_size(value,maximum,checkpoint)
    return canonical_json(value)


class EvidenceBudget:
    """Conservative cumulative construction charge, never refunded on overwrite.

    Every retained field/method/name/dependency/static-read entry is charged.
    Record envelopes and a fixed final-summary reserve cover diagnostics and
    counters even when the next evidence entry cannot fit. The completed result
    also receives an exact streaming size check before it can leave the API.
    """
    def __init__(self,maximum,checkpoint=None):
        self.maximum=maximum;self.used=8192;self.checkpoint=checkpoint
    def charge(self,value,extra=1):
        try:size=json_evidence_size(value,max(0,self.maximum-self.used-extra),self.checkpoint)
        except EvidenceSizeLimit:raise ILUnsupported('TOTAL_EVIDENCE_BYTE_LIMIT') from None
        if self.used+size+extra>self.maximum:raise ILUnsupported('TOTAL_EVIDENCE_BYTE_LIMIT')
        self.used+=size+extra


# ECMA-335 III opcode operand encodings. Unsupported semantics are still decoded
# accurately so an opcode in an unvisited switch arm cannot shift boundaries.
_OPERANDS = {}
for start, end in ((0x00,0x0d),(0x14,0x1e),(0x25,0x26),(0x2a,0x2a),
                   (0x46,0x6e),(0x76,0x76),(0x7a,0x7a),(0x82,0x8b),
                   (0x8e,0x8e),(0x90,0xa2),(0xb3,0xba),(0xc3,0xc3),
                   (0xd1,0xdc),(0xdf,0xe0)):
    _OPERANDS.update({n:'none' for n in range(start,end+1)})
for n in range(0x0e,0x14): _OPERANDS[n]='u1'
_OPERANDS.update({0x1f:'i1',0x20:'i4',0x21:'i8',0x22:'r4',0x23:'r8'})
for n in (0x27,0x28,0x29,0x6f,0x70,0x71,0x72,0x73,0x74,0x75,0x79,
          0x7b,0x7c,0x7d,0x7e,0x7f,0x80,0x81,0x8c,0x8d,0x8f,0xa3,0xa4,
          0xa5,0xc2,0xc6,0xd0): _OPERANDS[n]='token'
for n in range(0x2b,0x38): _OPERANDS[n]='branch1'
for n in range(0x38,0x45): _OPERANDS[n]='branch4'
_OPERANDS.update({0x45:'switch',0xdd:'branch4',0xde:'branch1'})
for n in (0,1,2,3,4,5,15,17,19,20,23,24,26,29,30): _OPERANDS[0xfe00+n]='none'
for n in (6,7,21,22,28): _OPERANDS[0xfe00+n]='token'
for n in range(9,15): _OPERANDS[0xfe00+n]='u2'
_OPERANDS.update({0xfe12:'u1',0xfe19:'u1'})
_FORMATS={'u1':'B','i1':'b','u2':'H','i4':'i','i8':'q','r4':'f','r8':'d','token':'I','branch1':'b','branch4':'i'}


@dataclass(frozen=True)
class Instruction:
    offset: int
    opcode: int
    operand: object
    next_offset: int


def decode_il(code: bytes, limits=StaticILLimits(), checkpoint=None) -> dict[int, Instruction]:
    checkpoint = checkpoint or (lambda: None)
    if len(code)>limits.method_bytes:
        raise ILUnsupported('METHOD_BYTE_LIMIT')
    result, position = {}, 0
    def unpack(fmt):
        nonlocal position
        size=struct.calcsize(fmt)
        if position>len(code)-size:
            raise ILUnsupported('TRUNCATED_IL_OPERAND',offset=position)
        value=struct.unpack_from('<'+fmt,code,position)[0]
        position+=size
        return value
    while position<len(code):
        checkpoint()
        if len(result)>=limits.method_instructions:
            raise ILUnsupported('INSTRUCTION_LIMIT',offset=position)
        offset=position
        opcode=unpack('B')
        if opcode==0xfe: opcode=0xfe00+unpack('B')
        kind=_OPERANDS.get(opcode)
        if kind is None: raise ILUnsupported('UNKNOWN_IL_OPCODE',offset=offset)
        operand=None
        if kind=='switch':
            count=unpack('I')
            if count>limits.method_instructions or count>(len(code)-position)//4:
                raise ILUnsupported('INVALID_SWITCH_TABLE',offset=offset)
            deltas=[unpack('i') for _ in range(count)]
            operand=tuple(position+delta for delta in deltas)
        elif kind!='none':
            operand=unpack(_FORMATS[kind])
            if kind.startswith('branch'): operand+=position
        result[offset]=Instruction(offset,opcode,operand,position)
    for ins in result.values():
        targets=ins.operand if ins.opcode==0x45 else (ins.operand,) if _OPERANDS[ins.opcode].startswith('branch') else ()
        if any(target not in result for target in targets):
            raise ILUnsupported('BRANCH_TARGET_NOT_INSTRUCTION',offset=ins.offset)
    return result


_KINDS={1:'void',2:'bool',3:'char',4:'i1',5:'u1',6:'i2',7:'u2',8:'i4',9:'u4',10:'i8',11:'u8',12:'r4',13:'r8',14:'other',24:'other',25:'other',28:'other'}
_INTEGRAL_KINDS=frozenset(('bool','char','i1','u1','i2','u2','i4','u4','i8','u8'))
_ENUM_KINDS=_INTEGRAL_KINDS-{'bool','char'}
# Foreign owners are admitted only for direct, call-free, integer-only bodies.
# Inspect every decoded instruction, including unreachable branches, before
# using the existing path evaluator. No field/string/object access or calls.
_PURE_INTEGER_OPS=frozenset((0,2,3,4,5,6,7,8,9,10,11,12,13,14,17,19,
    *range(0x15,0x22),0x25,0x26,0x2a,*range(0x2b,0x46),
    *range(0x58,0x6b),0x6d,0x6e,0xd1,0xd2,
    0xfe01,0xfe02,0xfe03,0xfe04,0xfe05,0xfe09,0xfe0c,0xfe0e))


def _compressed(blob, position):
    if position>=len(blob): raise ILUnsupported('TRUNCATED_SIGNATURE')
    first=blob[position];position+=1
    if first<0x80: return first,position
    size=2 if first&0xc0==0x80 else 4 if first&0xe0==0xc0 else 0
    if not size or position+size-1>len(blob): raise ILUnsupported('INVALID_SIGNATURE_INTEGER')
    value=first & (0x3f if size==2 else 0x1f)
    for byte in blob[position:position+size-1]: value=(value<<8)|byte
    if value<(0x80 if size==2 else 0x4000):raise ILUnsupported('INVALID_SIGNATURE_INTEGER')
    return value,position+size-1


def _type(blob, position, depth=0, enum_resolver=None):
    if depth>16 or position>=len(blob): raise ILUnsupported('UNSUPPORTED_SIGNATURE_TYPE')
    element=blob[position];position+=1
    if element in _KINDS:return _KINDS[element],position
    if element in (0x0f,0x10,0x1d,0x45):
        _,position=_type(blob,position,depth+1);return 'other',position
    if element in (0x11,0x12,0x13,0x1e):
        coded,position=_compressed(blob,position)
        return enum_resolver(coded) if element==0x11 and enum_resolver else 'other',position
    if element in (0x1f,0x20):
        _,position=_compressed(blob,position);return _type(blob,position,depth+1)
    if element==0x15:
        if position>=len(blob) or blob[position] not in (0x11,0x12):raise ILUnsupported('UNSUPPORTED_SIGNATURE_TYPE')
        _,position=_compressed(blob,position+1);count,position=_compressed(blob,position)
        if count>64:raise ILUnsupported('SIGNATURE_ARGUMENT_LIMIT')
        for _ in range(count):_,position=_type(blob,position,depth+1)
        return 'other',position
    if element==0x14:
        _,position=_type(blob,position,depth+1)
        _,position=_compressed(blob,position)
        for _ in range(2):
            count,position=_compressed(blob,position)
            if count>64:raise ILUnsupported('SIGNATURE_ARGUMENT_LIMIT')
            for _ in range(count):_,position=_compressed(blob,position)
        return 'other',position
    raise ILUnsupported('UNSUPPORTED_SIGNATURE_TYPE')


def method_signature(blob, enum_resolver=None):
    if not blob or blob[0] not in (0,0x20):
        raise ILUnsupported('UNSUPPORTED_METHOD_SIGNATURE')
    count,pos=_compressed(blob,1)
    if count>64:raise ILUnsupported('SIGNATURE_ARGUMENT_LIMIT')
    returned,pos=_type(blob,pos,enum_resolver=enum_resolver)
    args=[]
    for _ in range(count):
        kind,pos=_type(blob,pos,enum_resolver=enum_resolver);args.append(kind)
    if pos!=len(blob):raise ILUnsupported('SIGNATURE_TRAILING_BYTES')
    return bool(blob[0]&0x20),returned,tuple(args)


@dataclass(frozen=True)
class Value:
    kind: str
    value: object = None


THIS=Value('this')
UNKNOWN=Value('unknown')


def _i(value,bits=32):
    value=int(value)&((1<<bits)-1)
    return value-(1<<bits) if value&(1<<(bits-1)) else value


def _storage(value,kind):
    if value.kind=='unknown':return None
    if kind in ('r4','r8'):
        if value.kind not in ('r4','r8'):raise ILUnsupported('INVALID_NUMERIC_STACK_TYPE')
        number=float(value.value)
        if kind=='r4':
            try:number=struct.unpack('<f',struct.pack('<f',number))[0]
            except OverflowError:raise ILUnsupported('NONFINITE_FLOAT')
        if not math.isfinite(number):raise ILUnsupported('NONFINITE_FLOAT')
        return number
    if value.kind not in ('i4','i8'):raise ILUnsupported('INVALID_NUMERIC_STACK_TYPE')
    n=int(value.value)
    if kind=='bool':
        if n not in (0,1):raise ILUnsupported('UNSUPPORTED_BOOLEAN_ENCODING')
        return bool(n)
    bits={'i1':8,'u1':8,'i2':16,'u2':16,'char':16,'i4':32,'u4':32,'i8':64,'u8':64}.get(kind)
    if bits is None:raise ILUnsupported('NON_NUMERIC_FIELD')
    return _i(n,bits) if kind.startswith('i') else n&((1<<bits)-1)


def _typed_value(value,kind):
    if value.kind=='unknown':return UNKNOWN
    if kind=='other':return UNKNOWN
    if kind in ('i8','u8') and value.kind!='i8':raise ILUnsupported('INVALID_NUMERIC_STACK_TYPE')
    if kind not in ('i8','u8','r4','r8') and value.kind!='i4':raise ILUnsupported('INVALID_NUMERIC_STACK_TYPE')
    stored=_storage(value,kind)
    if kind in ('r4','r8'):return Value(kind,stored)
    return Value('i8' if kind in ('i8','u8') else 'i4',_i(stored,64 if kind in ('i8','u8') else 32))


@dataclass
class Method:
    token: int
    name: str
    instance: bool
    returned: str
    args: tuple[str,...]
    instructions: dict[int,Instruction]
    evidence: dict
    locals: tuple[str,...] = ()
    init_locals: bool = False
    max_stack: int = 256


class MetadataProgram:
    """Resolve only data from this assembly; imported/generic calls stay closed."""
    def __init__(self,meta,types,limits=StaticILLimits(),checkpoint=None,evidence_budget=None):
        self.meta,self.types,self.limits=meta,types,limits
        self.checkpoint=checkpoint or meta.checkpoint
        self.evidence_budget=evidence_budget or EvidenceBudget(limits.evidence_bytes,self.checkpoint)
        self.name_bytes=0
        self.enum_cache={};self.enum_fields=0
        self.metadata_scans=0;self.scanned_tables={};self.method_owners=None
        self.foreign_owners={}
        self.checkpoint()
        found=[(rid,row) for rid,row in types.items() if row['fullName']=='Terraria.Item']
        if len(found)!=1:raise ILUnsupported('ITEM_TYPE_NOT_FOUND_OR_AMBIGUOUS')
        self.type_rid,self.item=found[0]
        owners={self.type_rid}
        cursor=self.type_rid
        for _ in range(64):
            row,_=meta.row(2,cursor);extends=row[3]
            if not extends or extends&3:break
            cursor=extends>>2
            if cursor in owners:raise ILUnsupported('CYCLIC_ITEM_INHERITANCE')
            if cursor not in types:raise ILUnsupported('INVALID_ITEM_BASE_TYPE')
            owners.add(cursor)
        # Explicit-layout fields may overlap storage. This numeric subset does
        # not model aliases, so neither the Item nor an accepted base owner may
        # claim independent field values under that layout.
        for rid in owners:
            self.checkpoint()
            typedef,_=meta.row(2,rid)
            if typedef[0]&0x18==0x10:
                raise ILUnsupported('UNSUPPORTED_EXPLICIT_FIELD_LAYOUT',token=0x02000000|rid)
        self.fields={}
        for rid in owners:
            row=types[rid]
            for field in range(row['firstField'],row['lastField']):
                self.checkpoint()
                values,offset=meta.row(4,field)
                if values[0]&0x10:continue
                if len(self.fields)>=limits.fields:raise ILUnsupported('FIELD_COUNT_LIMIT')
                name=meta.string(values[1]);self._name(name)
                signature,signature_offset=meta.blob(values[2])
                kind='other'
                if signature and signature[0]==6:
                    try:
                        kind,end=_type(signature,1)
                        if end!=len(signature):kind='other'
                    except ILUnsupported:pass
                self.fields[0x04000000|field]={'name':name,'kind':kind,'owner':row['fullName'],
                    'metadataOffset':offset,'signatureOffset':signature_offset}
        self.method_rows={}
        self.by_name={}
        if self.item['lastMethod']-self.item['firstMethod']>limits.method_index:
            raise ILUnsupported('METHOD_INDEX_COUNT_LIMIT')
        for rid in range(self.item['firstMethod'],self.item['lastMethod']):
            self.checkpoint()
            row,offset=meta.row(6,rid)
            name=meta.string(row[3]);token=0x06000000|rid
            self._name(name)
            self.method_rows[token]=(row,offset,name)
            self.by_name.setdefault(name,[]).append(token)
        self.cache={};self.used={};self.decoded_instructions=0;self.decoded_bytes=0

    def _name(self,name):
        size=len(name.encode('utf-8'))
        if self.name_bytes+size>self.limits.metadata_name_bytes:
            raise ILUnsupported('METADATA_NAME_BYTE_LIMIT')
        self.name_bytes+=size

    def _scan(self):
        """Share the existing metadata-index cap across added metadata reads."""
        self.checkpoint()
        if self.metadata_scans>=self.limits.method_index:
            raise ILUnsupported('METADATA_SCAN_LIMIT')
        self.metadata_scans+=1

    def _table(self,table):
        if table not in self.scanned_tables:
            rows=[]
            for rid in range(1,self.meta.rows[table]+1):
                self._scan();row,_=self.meta.row(table,rid);rows.append(row)
            self.scanned_tables[table]=rows
        return self.scanned_tables[table]

    def _enum(self,coded):
        """Only a sealed local enum with one legal integral storage field.

        Names alone do not identify the base: require an external core-library
        TypeRef and its known public-key token, never a local System.Enum lookalike.
        This validates metadata shape, not an assembly trust/signature claim.
        """
        if coded in self.enum_cache:return self.enum_cache[coded]
        if not coded or coded&3 or coded>>2 not in self.types:return 'other',None
        rid=coded>>2;typedef=self.types[rid];self._scan()
        row,offset=self.meta.row(2,rid)
        if row[0]&~0x102101 or not row[0]&0x100:
            return 'other',None
        if row[3]&3!=1 or not row[3]>>2:return 'other',None
        self._scan();base,base_offset=self.meta.row(1,row[3]>>2)
        base_name=self.meta.string(base[1]);base_ns=self.meta.string(base[2])
        self._name(base_name);self._name(base_ns)
        if (base_ns,base_name)!=('System','Enum') or base[0]&3!=2 or not base[0]>>2:
            return 'other',None
        self._scan();assembly,assembly_offset=self.meta.row(35,base[0]>>2)
        assembly_name=self.meta.string(assembly[6]);self._name(assembly_name)
        public_key,_=self.meta.blob(assembly[5])
        core_keys={'mscorlib':bytes.fromhex('b77a5c561934e089'),
                   'System.Private.CoreLib':bytes.fromhex('7cec85d7bea7798e'),
                   'System.Runtime':bytes.fromhex('b03f5f7f11d50a3a')}
        culture=self.meta.string(assembly[7]);self._name(culture)
        if assembly[4] or culture or public_key!=core_keys.get(assembly_name):return 'other',None
        if typedef['firstMethod']!=typedef['lastMethod']:return 'other',None
        if any(r[2]==rid<<1 for r in self._table(42)):return 'other',None
        first,last=typedef['firstField'],typedef['lastField']
        if not 1<=first<last<=self.meta.rows[4]+1:return 'other',None
        if any(first<=r[1]<last for r in self._table(16)) or any(r[2]==rid for r in self._table(15)):
            return 'other',None
        underlying=None;names=set()
        for field in range(first,last):
            self._scan()
            if len(self.fields)+self.enum_fields>=self.limits.fields:raise ILUnsupported('FIELD_COUNT_LIMIT')
            self.enum_fields+=1
            values,field_offset=self.meta.row(4,field)
            name=self.meta.string(values[1]);self._name(name)
            if name in names:return 'other',None
            names.add(name);signature,signature_offset=self.meta.blob(values[2])
            if values[0]&0x10:
                # Enum constants must be literal static fields of this enum.
                if values[0]!=0x8056 or len(signature)<3 or signature[:2]!=b'\x06\x11':return 'other',None
                target,end=_compressed(signature,2)
                if target!=coded or end!=len(signature):return 'other',None
                continue
            if underlying is not None or name!='value__' or values[0]!=0x606 or len(signature)!=2 or signature[0]!=6:
                return 'other',None
            kind=_KINDS.get(signature[1])
            if kind not in _ENUM_KINDS:return 'other',None
            underlying=(kind,field,field_offset,signature_offset,signature)
        if underlying is None:return 'other',None
        kind,field,field_offset,signature_offset,signature=underlying
        name=typedef['fullName'];self._name(name)
        evidence={'typeToken':f'0x{0x02000000|rid:08x}','typeName':name,'underlyingType':kind,
                  'typeMetadataOffset':offset,'baseTypeRefMetadataOffset':base_offset,
                  'coreAssemblyRefMetadataOffset':assembly_offset,'coreAssemblyName':assembly_name,
                  'valueFieldToken':f'0x{0x04000000|field:08x}','valueFieldMetadataOffset':field_offset,
                  'valueSignatureOffset':signature_offset,'valueSignatureSha256':hashlib.sha256(signature).hexdigest()}
        self.evidence_budget.charge(evidence)
        self.enum_cache[coded]=(kind,evidence)
        return kind,evidence

    def _foreign_method(self,token):
        if token>>24!=6 or not 1<=token&0xffffff<=self.meta.rows[6]:
            raise ILUnsupported('UNSUPPORTED_EXTERNAL_OR_GENERIC_CALL',token=token)
        if len(self.method_rows)>=self.limits.method_index:raise ILUnsupported('METHOD_INDEX_COUNT_LIMIT',token=token)
        if self.method_owners is None:
            owners=[];previous=1
            for rid,owner in sorted(self.types.items()):
                self._scan();first,last=owner['firstMethod'],owner['lastMethod']
                if not previous<=first<=last<=self.meta.rows[6]+1:
                    raise ILUnsupported('INVALID_METHOD_OWNER_RANGE',token=token)
                previous=last;owners.append((first,last,rid))
            self.method_owners=owners
        candidates=[rid for first,last,rid in self.method_owners if first<=token&0xffffff<last]
        if len(candidates)!=1:raise ILUnsupported('INVALID_METHOD_OWNER_RANGE',token=token)
        rid=candidates[0];self._scan();owner,_=self.meta.row(2,rid)
        if owner[0]&0x20 or owner[0]&7>1:
            raise ILUnsupported('UNSUPPORTED_HELPER_OWNER',token=token)
        if any(r[2] in (rid<<1,((token&0xffffff)<<1)|1) for r in self._table(42)):
            raise ILUnsupported('UNSUPPORTED_EXTERNAL_OR_GENERIC_CALL',token=token)
        row,offset=self.meta.row(6,token&0xffffff)
        name=self.meta.string(row[3]);self._name(name)
        owner_name=self.types[rid]['fullName'];self._name(owner_name)
        self.foreign_owners[token]=owner_name
        self.method_rows[token]=(row,offset,name)

    def get(self,token):
        self.checkpoint()
        if token in self.cache:
            value=self.cache[token]
            if isinstance(value,ILUnsupported):raise ILUnsupported(value.code,offset=value.offset,token=value.token)
            return value
        if len(self.cache)>=self.limits.methods:raise ILUnsupported('METHOD_COUNT_LIMIT',token=token)
        try:
            if token not in self.method_rows:self._foreign_method(token)
            row,offset,name=self.method_rows[token]
            if row[1]&3 or row[1]&4 or row[2]&0x2000 or row[2]&0x40:
                raise ILUnsupported('UNSUPPORTED_NATIVE_OR_VIRTUAL_METHOD',token=token)
            enum_types={}
            def resolve_enum(coded):
                kind,evidence=self._enum(coded)
                if evidence is not None:enum_types[coded]=evidence
                return kind
            signature,_=self.meta.blob(row[4]);instance,returned,args=method_signature(signature,resolve_enum)
            if bool(row[2]&0x10)==instance:raise ILUnsupported('INVALID_METHOD_THIS_FLAG',token=token)
            if returned not in ('void','bool','char','i1','u1','i2','u2','i4','u4','i8','u8','r4','r8') or any(x in ('other','void') for x in args):
                raise ILUnsupported('UNSUPPORTED_NONNUMERIC_HELPER_SIGNATURE',token=token)
            foreign=token in self.foreign_owners
            if foreign and (instance or returned not in _INTEGRAL_KINDS or any(x not in _INTEGRAL_KINDS for x in args)
                            or name.startswith('.') or row[1]&~0x100 or row[2]&~0x97 or not 1<=row[2]&7<=6):
                raise ILUnsupported('UNSUPPORTED_PURE_HELPER_SIGNATURE',token=token)
            if foreign:
                # Keep the new cross-owner route narrower than Item methods:
                # only bare primitive integers, without modifiers or enum aliases.
                codes={kind:code for code,kind in _KINDS.items() if kind in _INTEGRAL_KINDS}
                if signature!=bytes((0,len(args),codes[returned],*(codes[kind] for kind in args))):
                    raise ILUnsupported('UNSUPPORTED_PURE_HELPER_SIGNATURE',token=token)
            if not row[0]:raise ILUnsupported('METHOD_HAS_NO_IL',token=token)
            body=self.meta.rva(row[0],1);first=self.meta.reader.uint(body,1)
            local_token=0;initialized=False;max_stack=8
            if first&3==2:header,size=1,first>>2
            elif first&3==3:
                flags=self.meta.reader.uint(body,2);header=(flags>>12)*4
                if flags&8:raise ILUnsupported('UNSUPPORTED_EXCEPTION_REGIONS',token=token)
                if foreign and flags&~0xf013:raise ILUnsupported('INVALID_METHOD_HEADER',token=token)
                if not 12<=header<=60:raise ILUnsupported('INVALID_METHOD_HEADER',token=token)
                if foreign and header!=12:raise ILUnsupported('INVALID_METHOD_HEADER',token=token)
                size=self.meta.reader.uint(body+4,4);max_stack=self.meta.reader.uint(body+2,2)
                initialized=bool(flags&16);local_token=self.meta.reader.uint(body+8,4)
            else:raise ILUnsupported('UNSUPPORTED_METHOD_HEADER',token=token)
            if size>self.limits.method_bytes:raise ILUnsupported('METHOD_BYTE_LIMIT',token=token)
            code_offset=self.meta.rva(row[0]+header,size);code=self.meta.reader.take(code_offset,size)
            locals_=[]
            if local_token:
                if local_token>>24!=0x11:raise ILUnsupported('INVALID_LOCAL_SIGNATURE_TOKEN',token=token)
                localrow,_=self.meta.row(17,local_token&0xffffff);blob,_=self.meta.blob(localrow[0])
                if not blob or blob[0]!=7:raise ILUnsupported('INVALID_LOCAL_SIGNATURE',token=token)
                count,pos=_compressed(blob,1)
                if count>self.limits.locals:raise ILUnsupported('LOCAL_COUNT_LIMIT',token=token)
                locals_start=pos
                for _ in range(count):kind,pos=_type(blob,pos,enum_resolver=resolve_enum);locals_.append(kind)
                if pos!=len(blob):raise ILUnsupported('SIGNATURE_TRAILING_BYTES',token=token)
                if foreign and (any(kind not in _INTEGRAL_KINDS for kind in locals_) or
                                blob[locals_start:]!=bytes(codes[kind] for kind in locals_)):
                    raise ILUnsupported('UNSUPPORTED_PURE_HELPER_SIGNATURE',token=token)
            evidence={'methodToken':f'0x{token:08x}','methodName':name,'methodMetadataOffset':offset,
                      'bodyOffset':body,'codeOffset':code_offset,'codeBytes':size,'ilSha256':hashlib.sha256(code).hexdigest(),
                      'signatureSha256':hashlib.sha256(signature).hexdigest()}
            if enum_types:evidence['enumTypes']=list(enum_types.values())
            if foreign:
                evidence['declaringType']=self.foreign_owners[token]
                evidence['resolution']='same-assembly-call-free-integer-helper'
            self.evidence_budget.charge(evidence)
            if self.decoded_bytes+size>self.limits.total_method_bytes:raise ILUnsupported('TOTAL_METHOD_BYTE_LIMIT',token=token)
            instructions=decode_il(code,self.limits,self.checkpoint)
            if self.decoded_instructions+len(instructions)>self.limits.total_decoded_instructions:raise ILUnsupported('TOTAL_DECODED_INSTRUCTION_LIMIT',token=token)
            self.decoded_bytes+=size;self.decoded_instructions+=len(instructions)
            if foreign:
                if any(kind not in _INTEGRAL_KINDS for kind in locals_):
                    raise ILUnsupported('UNSUPPORTED_PURE_HELPER_SIGNATURE',token=token)
                for instruction in instructions.values():
                    self.checkpoint()
                    if instruction.opcode not in _PURE_INTEGER_OPS:
                        raise ILUnsupported('UNSUPPORTED_IMPURE_HELPER_BODY',offset=instruction.offset,token=token)
            method=Method(token,name,instance,returned,args,instructions,
                          evidence,tuple(locals_),initialized,max_stack)
            self.cache[token]=method;self.used[token]=evidence
            return method
        except ILUnsupported as exc:
            self.cache[token]=exc
            raise


class AbstractEvaluator:
    def __init__(self,program,limits=StaticILLimits(),checkpoint=None,evidence_budget=None):
        self.program,self.limits=program,limits
        self.checkpoint=checkpoint or (lambda:None)
        self.evidence_budget=evidence_budget or getattr(program,'evidence_budget',None) or EvidenceBudget(limits.evidence_bytes,self.checkpoint)
        self.total_steps=0

    def stage(self,token,item_id):
        self.state={};self.excluded={};self.callchain=[];self.path_steps=0;self.path_methods=set();self.static_reads=set()
        result={'id':item_id,'entryMethodToken':f'0x{token:08x}', 'executedInput':False,
                'complete':False,'finalItemDefaults':False,'scope':'SetDefaultsN-numeric-stage-only',
                'initialInstanceFields':'unknown','arguments':[item_id],
                'analysisAssumptions':['normal-return numeric dataflow only',
                    'static initialization, faults and other runtime side effects are not evaluated',
                    'not a complete CLI verifier or runtime execution certificate']}
        # Reserve this record's lists, status, diagnostic/call chain and method
        # label before starting. If it cannot fit, no uncharged record is added.
        self.evidence_budget.charge(result,extra=768+16*self.limits.call_depth)
        try:
            method=self.program.get(token)
            if not method.instance or method.args!=('i4',) or method.returned!='void':
                raise ILUnsupported('INVALID_STAGE_SIGNATURE',token=token)
            self._method(method,[THIS,Value('i4',item_id)])
            result.update(status='PROVEN_NUMERIC_STAGE_WRITES' if self.state else 'NO_PROVEN_NUMERIC_WRITES',
                          fields=list(self.state.values()),prefixFields=[])
        except ILUnsupported as exc:
            result.update(status=exc.code,fields=[],prefixFields=list(self.state.values()),
                          diagnostic={'code':exc.code,'ilOffset':exc.offset,
                                      'token':f'0x{exc.token:08x}' if exc.token is not None else None,
                                      'callChain':[f'0x{x:08x}' for x in getattr(exc,'callchain',())]})
        result['excludedFields']=list(self.excluded.values())
        result['unresolvedStaticReads']=[f'0x{token:08x}' for token in sorted(self.static_reads)]
        result['analyzedInstructions']=self.path_steps
        return result

    def _method(self,method,args):
        if len(self.callchain)>=self.limits.call_depth:raise ILUnsupported('CALL_DEPTH_LIMIT',token=method.token)
        if method.token in self.callchain:raise ILUnsupported('UNSUPPORTED_RECURSIVE_CALL',token=method.token)
        if len(args)!=len(method.args)+int(method.instance):raise ILUnsupported('INVALID_CALL_ARGUMENT_COUNT',token=method.token)
        args=([args[0]] if method.instance else [])+[_typed_value(value,kind) for value,kind in zip(args[int(method.instance):],method.args)]
        self.callchain.append(method.token)
        self.path_methods.add(method.token)
        stack=[];pc=0;visited=set();steps=0
        locals_=[Value('r4' if kind=='r4' else 'r8' if kind=='r8' else 'i8' if kind in ('i8','u8') else 'i4',0)
                 if method.init_locals and kind!='other' else UNKNOWN for kind in method.locals]
        def pop():
            if not stack:raise ILUnsupported('STACK_UNDERFLOW',offset=pc,token=method.token)
            return stack.pop()
        def index(values,n):
            if n<0 or n>=len(values):raise ILUnsupported('INVALID_VARIABLE_INDEX',offset=pc,token=method.token)
            return values[n]
        try:
            while True:
                self.checkpoint()
                steps+=1;self.total_steps+=1;self.path_steps+=1
                if steps>self.limits.method_steps:raise ILUnsupported('METHOD_STEP_LIMIT',offset=pc,token=method.token)
                if self.total_steps>self.limits.total_steps:raise ILUnsupported('TOTAL_STEP_LIMIT',offset=pc,token=method.token)
                if pc in visited:raise ILUnsupported('UNSUPPORTED_LOOP',offset=pc,token=method.token)
                visited.add(pc)
                ins=method.instructions.get(pc)
                if ins is None:raise ILUnsupported('FALLTHROUGH_OUTSIDE_METHOD',offset=pc,token=method.token)
                op,operand,next_pc=ins.opcode,ins.operand,ins.next_offset
                if op==0:pass
                elif 2<=op<=5:stack.append(index(args,op-2))
                elif op in (0x0e,0xfe09):stack.append(index(args,operand))
                elif 6<=op<=9:stack.append(index(locals_,op-6))
                elif op in (0x11,0xfe0c):stack.append(index(locals_,operand))
                elif 0x0a<=op<=0x0d or op in (0x13,0xfe0e):
                    n=op-0x0a if 0x0a<=op<=0x0d else operand;index(locals_,n);locals_[n]=_typed_value(pop(),method.locals[n])
                elif op==0x14:stack.append(Value('null'))
                elif 0x15<=op<=0x1e:stack.append(Value('i4',op-0x16))
                elif op in (0x1f,0x20):stack.append(Value('i4',operand))
                elif op==0x21:stack.append(Value('i8',operand))
                elif op in (0x22,0x23):
                    if not math.isfinite(operand):raise ILUnsupported('NONFINITE_FLOAT',offset=pc,token=method.token)
                    stack.append(Value('r4' if op==0x22 else 'r8',operand))
                elif op==0x25:value=pop();stack.extend((value,value))
                elif op==0x26:pop()
                elif op in (0x7b,0x7d):
                    value=pop() if op==0x7d else None;receiver=pop()
                    field=self.program.fields.get(operand)
                    if receiver!=THIS or field is None:raise ILUnsupported('UNSUPPORTED_FIELD_RECEIVER_OR_OWNER',offset=pc,token=operand)
                    if op==0x7b:
                        old=self.state.get(operand)
                        if old is None:stack.append(UNKNOWN)
                        else:
                            kind='i8' if field['kind'] in ('i8','u8') else field['kind'] if field['kind'] in ('r4','r8') else 'i4'
                            stack.append(Value(kind,old['value'] if kind in ('r4','r8') else _i(old['value'],64 if kind=='i8' else 32)))
                    elif field['kind']=='other' or value.kind=='unknown':
                        self.state.pop(operand,None)
                        excluded={'fieldToken':f'0x{operand:08x}','fieldName':field['name'],
                            'reason':'NON_NUMERIC_FIELD' if field['kind']=='other' else 'UNKNOWN_VALUE',
                            'methodToken':f'0x{method.token:08x}','ilOffset':pc}
                        self.evidence_budget.charge(excluded)
                        self.excluded[operand]=excluded
                    else:
                        _typed_value(value,field['kind'])
                        stored=_storage(value,field['kind'])
                        assignment={'fieldToken':f'0x{operand:08x}','fieldName':field['name'],'fieldType':field['kind'],
                            'value':stored,'evidence':{'methodToken':f'0x{method.token:08x}','ilOffset':pc,
                            'instructionFileOffset':method.evidence['codeOffset']+pc,'ilSha256':method.evidence['ilSha256'],
                            'fieldMetadataOffset':field.get('metadataOffset'),
                            'dependencyMethodTokens':[f'0x{x:08x}' for x in sorted(self.path_methods)], 'callChain':[f'0x{x:08x}' for x in self.callchain]}}
                        self.evidence_budget.charge(assignment)
                        self.evidence_budget.charge(field['name']) # final fieldNames summary
                        self.excluded.pop(operand,None)
                        self.state[operand]=assignment
                elif op in (0x7e,0x72):
                    if op==0x7e and operand not in self.static_reads:
                        self.evidence_budget.charge(f'0x{operand:08x}')
                        self.static_reads.add(operand)
                    stack.append(UNKNOWN)
                elif op in (0x2b,0x38):next_pc=operand
                elif op in (0x2c,0x2d,0x39,0x3a):
                    value=pop()
                    if value.kind=='unknown':raise ILUnsupported('UNSUPPORTED_UNKNOWN_BRANCH',offset=pc,token=method.token)
                    truth=value.kind=='this' or value.kind!='null' and bool(value.value)
                    if truth==(op in (0x2d,0x3a)):next_pc=operand
                elif 0x2e<=op<=0x37 or 0x3b<=op<=0x44:
                    right,left=pop(),pop();branch=op if op<=0x37 else op-0x0d
                    comparison=self._compare(left,right,branch)
                    if comparison:next_pc=operand
                elif op==0x45:
                    value=pop()
                    if value.kind=='unknown':raise ILUnsupported('UNSUPPORTED_UNKNOWN_BRANCH',offset=pc,token=method.token)
                    if value.kind!='i4':raise ILUnsupported('INVALID_SWITCH_STACK_TYPE',offset=pc,token=method.token)
                    n=int(value.value)&0xffffffff
                    if n<len(operand):next_pc=operand[n]
                elif op in range(0x58,0x65):
                    right,left=pop(),pop();stack.append(self._binary(op,left,right))
                elif op in (0x65,0x66):
                    value=pop()
                    if value.kind=='unknown':stack.append(UNKNOWN)
                    elif value.kind in ('i4','i8'):
                        stack.append(Value(value.kind,_i(-value.value if op==0x65 else ~value.value,64 if value.kind=='i8' else 32)))
                    else:raise ILUnsupported('UNSUPPORTED_FLOAT_ARITHMETIC',offset=pc,token=method.token)
                elif op in (0x67,0x68,0x69,0x6a,0x6b,0x6c,0x6d,0x6e,0xd1,0xd2):
                    stack.append(self._convert(pop(),op))
                elif op in (0xfe01,0xfe02,0xfe03,0xfe04,0xfe05):
                    right,left=pop(),pop();branch={0xfe01:0x2e,0xfe02:0x30,0xfe03:0x35,0xfe04:0x32,0xfe05:0x37}[op]
                    if left.kind=='unknown' or right.kind=='unknown':stack.append(UNKNOWN)
                    else:stack.append(Value('i4',int(self._compare(left,right,branch))))
                elif op==0x28:
                    target=self.program.get(operand)
                    count=len(target.args)+int(target.instance)
                    if len(stack)<count:raise ILUnsupported('STACK_UNDERFLOW',offset=pc,token=method.token)
                    call_args=stack[-count:] if count else []
                    if count:del stack[-count:]
                    if target.instance and call_args[0]!=THIS:raise ILUnsupported('UNSUPPORTED_CALL_RECEIVER',offset=pc,token=operand)
                    returned=self._method(target,call_args)
                    if target.returned!='void':stack.append(returned)
                elif op==0x2a:
                    value=pop() if method.returned!='void' else None
                    if stack:raise ILUnsupported('NONEMPTY_RETURN_STACK',offset=pc,token=method.token)
                    return _typed_value(value,method.returned) if method.returned!='void' else None
                elif op in (0x27,0x29,0x6f,0x73):
                    raise ILUnsupported({0x27:'UNSUPPORTED_JMP_CALL',0x29:'UNSUPPORTED_INDIRECT_CALL',0x6f:'UNSUPPORTED_VIRTUAL_CALL',0x73:'UNSUPPORTED_CONSTRUCTOR_CALL'}[op],offset=pc,token=operand)
                else:raise ILUnsupported('UNSUPPORTED_OPCODE',offset=pc,token=method.token)
                if len(stack)>min(self.limits.stack,method.max_stack):raise ILUnsupported('STACK_LIMIT',offset=pc,token=method.token)
                pc=next_pc
        except ILUnsupported as exc:
            if exc.offset is None:exc.offset=pc
            if exc.token is None:exc.token=method.token
            if not hasattr(exc,'callchain'):exc.callchain=tuple(self.callchain)
            raise
        finally:self.callchain.pop()

    @staticmethod
    def _compare(left,right,branch):
        if left.kind=='unknown' or right.kind=='unknown':raise ILUnsupported('UNSUPPORTED_UNKNOWN_BRANCH')
        if left.kind not in ('i4','i8') or right.kind!=left.kind:raise ILUnsupported('UNSUPPORTED_BRANCH_VALUE_TYPE')
        a,b=int(left.value),int(right.value)
        if branch>=0x33:
            mask=(1<<(64 if left.kind=='i8' else 32))-1;a&=mask;b&=mask
        return {0x2e:a==b,0x2f:a>=b,0x30:a>b,0x31:a<=b,0x32:a<b,
                0x33:a!=b,0x34:a>=b,0x35:a>b,0x36:a<=b,0x37:a<b}[branch]

    @staticmethod
    def _binary(op,left,right):
        if left.kind=='unknown' or right.kind=='unknown':return UNKNOWN
        if left.kind not in ('i4','i8') or right.kind not in ('i4','i8'):
            raise ILUnsupported('UNSUPPORTED_FLOAT_ARITHMETIC')
        if left.kind!=right.kind and op not in (0x62,0x63,0x64):raise ILUnsupported('INVALID_NUMERIC_STACK_TYPE')
        if op in (0x62,0x63,0x64) and right.kind!='i4':raise ILUnsupported('INVALID_SHIFT_COUNT_TYPE')
        bits=64 if left.kind=='i8' else 32;a,b=int(left.value),int(right.value);mask=(1<<bits)-1
        if op==0x58:value=a+b
        elif op==0x59:value=a-b
        elif op==0x5a:value=a*b
        elif op in (0x5b,0x5c,0x5d,0x5e):
            if op in (0x5c,0x5e):a&=mask;b&=mask
            if b==0:raise ILUnsupported('PROVEN_DIVIDE_BY_ZERO')
            if a==-(1<<(bits-1)) and b==-1:raise ILUnsupported('UNSUPPORTED_SIGNED_DIVISION_EDGE')
            quotient=(abs(a)//abs(b))*(-1 if (a<0)!=(b<0) else 1)
            value=quotient if op in (0x5b,0x5c) else a-quotient*b
        elif op==0x5f:value=a&b
        elif op==0x60:value=a|b
        elif op==0x61:value=a^b
        elif op==0x62:value=a<<(b&(bits-1))
        elif op==0x63:value=a>>(b&(bits-1))
        else:value=(a&mask)>>(b&(bits-1))
        return Value(left.kind,_i(value,bits))

    @staticmethod
    def _convert(value,op):
        if value.kind=='unknown':return UNKNOWN
        if value.kind not in ('i4','i8'):raise ILUnsupported('UNSUPPORTED_FLOAT_CONVERSION')
        kinds={0x67:'i1',0x68:'i2',0x69:'i4',0x6a:'i8',0x6d:'u4',0x6e:'u8',0xd1:'u2',0xd2:'u1'}
        if op in (0x6b,0x6c):
            if op==0x6b and value.kind=='i8' and abs(value.value)>(1<<53):raise ILUnsupported('UNSUPPORTED_HIGH_PRECISION_FLOAT_CONVERSION')
            result=float(value.value)
            if op==0x6b:result=struct.unpack('<f',struct.pack('<f',result))[0]
            return Value('r4' if op==0x6b else 'r8',result)
        kind=kinds[op]
        if kind=='u8' and value.kind=='i4':return Value('i8',int(value.value)&0xffffffff)
        stored=_storage(value,kind)
        return Value('i8' if kind in ('i8','u8') else 'i4',_i(stored,64 if kind in ('i8','u8') else 32))


def extract_item_default_stages(meta,types,item_ids,limits=StaticILLimits(),checkpoint=None,*,selection=None,input_sha256=None):
    checkpoint=checkpoint or (lambda:None)
    requested=[]
    for value in item_ids:
        if len(requested)>=limits.ids:raise PipelineError('Static IL stage IDs exceed the bounded positive-integer policy')
        if type(value) is not int or not 1<=value<=0x7fffffff:
            raise PipelineError('Static IL stage IDs must be positive integers')
        requested.append(value)
    ids=sorted(set(requested))
    if any(type(value) is not int or not 1<=value<=0x7fffffff for value in ids) or len(ids)>limits.ids:
        raise PipelineError('Static IL stage IDs exceed the bounded positive-integer policy')
    result={'schemaVersion':1,'status':'PARTIAL','executedInput':False,'complete':False,'finalItemDefaults':False,
            'stage':'SetDefaults1-through-5-numeric-abstract-evaluation','requestedIds':ids,'methods':[],
            'records':[],'unsupported':['ResetStats','food-dispatch','variant-selection','static-set-initializers',
                                        'outer-SetDefaults-postlude','external-calls','unknown-branches','loops','dynamic-tooltips']}
    if selection is not None:result['selection']=selection
    if input_sha256 is not None:result['inputSha256']=input_sha256
    deadline=time.monotonic()+limits.wall_seconds
    def bounded_checkpoint():
        checkpoint()
        if time.monotonic()>=deadline:raise ILUnsupported('TOTAL_TIME_LIMIT')
    budget=EvidenceBudget(limits.evidence_bytes,bounded_checkpoint)
    def finish(program=None,evaluator=None):
        rows=result['records']
        attempted_ids={row['id'] for row in rows}
        result['unattemptedSelectedIds']=[value for value in ids if value not in attempted_ids]
        result['attemptedStagePairs']=len(rows)
        result['unattemptedStagePairs']=len(ids)*5-len(rows)
        result['methods']=list(program.used.values()) if program is not None else []
        result['analyzedInstructions']=evaluator.total_steps if evaluator is not None else 0
        statuses={}
        for row in rows:statuses[row['status']]=statuses.get(row['status'],0)+1
        result['statusCounts']=statuses
        result['prefixFieldAssignments']=sum(len(row['prefixFields']) for row in rows)
        result['provenFieldAssignments']=sum(len(row['fields']) for row in rows)
        result['fieldNames']=sorted({field['fieldName'] for row in rows for field in row['fields']})
        result['evidenceBudget']={'limitBytes':limits.evidence_bytes,'accountedConstructionBytes':budget.used,
                                  'accounting':'cumulative; includes replaced entries and reserved diagnostic envelopes'}
        json_evidence_size(result,limits.evidence_bytes,checkpoint)
        return result
    # An oversized selection envelope cannot be silently omitted. It is an API
    # rejection; ordinary mid-analysis exhaustion instead returns partial facts.
    try:budget.charge(result)
    except ILUnsupported as exc:
        if exc.code=='TOTAL_TIME_LIMIT':
            result['diagnostic']={'code':exc.code};return finish()
        raise PipelineError('TOTAL_EVIDENCE_BYTE_LIMIT: selection envelope exceeds budget') from exc
    try:program=MetadataProgram(meta,types,limits,bounded_checkpoint,budget)
    except ILUnsupported as exc:
        result['diagnostic']={'code':exc.code};return finish()
    evaluator=AbstractEvaluator(program,limits,bounded_checkpoint,budget)
    entries=[]
    for name in ('SetDefaults1','SetDefaults2','SetDefaults3','SetDefaults4','SetDefaults5'):
        tokens=program.by_name.get(name,[])
        if len(tokens)!=1:
            result.setdefault('diagnostics',[]).append({'method':name,'code':'STAGE_METHOD_MISSING_OR_OVERLOADED'})
        else:entries.append((name,tokens[0]))
    for item_id in ids:
        checkpoint()
        for name,token in entries:
            try:row=evaluator.stage(token,item_id)
            except ILUnsupported as exc:
                result['diagnostic']={'code':exc.code,'stoppedStagePair':{'id':item_id,'method':name}}
                break
            row['method']=name
            result['records'].append(row)
            if row['status'] in ('TOTAL_STEP_LIMIT','TOTAL_TIME_LIMIT','TOTAL_EVIDENCE_BYTE_LIMIT'):
                result['diagnostic']={'code':row['status']}
                break
        if result.get('diagnostic'):break
    return finish(program,evaluator)


def select_stage_sample(domain, maximum=32):
    """Coverage sample over observed IDs, without presuming dispatcher ranges."""
    values=sorted(set(domain))
    if maximum<3:raise ValueError('Sample capacity must cover the first three observed IDs')
    if len(values)<=maximum:return values
    selected=set(values[:3])
    remaining=maximum-len(selected)
    for position in range(1,remaining+1):
        selected.add(values[round(position*(len(values)-1)/remaining)])
    if len(selected)<maximum:
        for value in values:
            selected.add(value)
            if len(selected)==maximum:break
    return sorted(selected)
