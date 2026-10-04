"""Finite pure Color body contracts from pinned official XNA bytes, never CLR.

Module/native startup and actual runtime assembly binding are outside this model.
Only value-type arithmetic bodies used by the map palette model are certified.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import math
import struct

from .id_count_semantics import _assembly
from .item_texture_aliases import (_Program, _Budget, ItemTextureAliasLimits, _CheckpointCancelled,
    _require, _shape, _constant, _compressed_bytes, _hex)
from .security import sha256
from .server_semantics import read_assembly_bytes, SemanticLimits
from .static_il import decode_il, StaticILLimits, json_evidence_size

VENDOR_SHA256 = '38e7093f52d7474bbc6256906519781a1210d7da50a1c667b52716fcf49ca130'
OWNER = 'Microsoft.Xna.Framework.Color'


def _core(p, token, name):
    _require(token >> 24 == 1 and 0 < token & 0xffffff <= p.meta.rows[1], 'COLOR_CORE_TYPE_TOKEN')
    row = p.external_type(token, name)
    a, _ = p.row(35, row[0] >> 2)
    _require(tuple(a[:4]) == (4, 0, 0, 0) and p.meta.string(a[6]) == 'mscorlib', 'COLOR_CORE_VERSION')


def _method(p, name, signature, flags, stack, local=b''):
    _, owner = p.owner(OWNER)
    found = []
    for n in range(owner['firstMethod'], owner['lastMethod']):
        row, _ = p.row(6, n)
        if p.meta.string(row[3]) == name and p.meta.blob(row[4])[0] == signature:
            found.append((n, row))
    _require(len(found) == 1, 'COLOR_METHOD_UNIQUE')
    n, row = found[0]
    _require(row[2] == flags and row[0] and row[1] == 0, 'COLOR_METHOD_FLAGS')
    if not flags & 0x40:
        m = p.body(0x06000000 | n)
        maximum = 8 if m['header'] == 1 else p.meta.reader.uint(m['evidence']['bodyOffset'] + 2, 2)
    else:
        # Exact final virtual value-type Equals body, called directly by token.
        # The generic alias parser intentionally disallows virtual methods.
        _require(name == 'Equals' and flags == 0x1e6, 'COLOR_VIRTUAL_METHOD')
        _require(not any(r[2] == (n << 1 | 1) for r in p.table(42)), 'COLOR_GENERIC_METHOD')
        at = p.meta.rva(row[0], 1); first = p.meta.reader.uint(at, 1)
        if first & 3 == 2:
            header, size, maximum = 1, first >> 2, 8
        else:
            f = p.meta.reader.uint(at, 2)
            _require(f in (0x3003, 0x3013) and p.meta.reader.uint(at + 8, 4) == 0,
                     'COLOR_EQUALS_HEADER')
            header, size, maximum = 12, p.meta.reader.uint(at + 4, 4), p.meta.reader.uint(at + 2, 2)
        _require(0 < size <= p.budget.limits.method_bytes and
                 size <= p.budget.limits.total_method_bytes - p.budget.method_bytes, 'COLOR_METHOD_BUDGET')
        p.budget.method_bytes += size
        offset = p.meta.rva(row[0] + header, size); raw = p.meta.reader.take(offset, size)
        instructions = list(decode_il(raw, StaticILLimits(method_bytes=p.budget.limits.method_bytes),
            p.budget.check, instruction_budget=p.budget.instruction).values())
        m = {'token': 0x06000000 | n, 'signature': signature, 'local': b'', 'eh': False,
             'instructions': instructions, 'evidence': {'methodToken': _hex(0x06000000 | n),
             'bodyOffset': at, 'codeOffset': offset, 'codeBytes': size,
             'ilSha256': sha256(raw), 'signatureSha256': sha256(signature)}}
    _require(not m['eh'] and m['local'] == local and stack <= maximum <= 256,
             'COLOR_LOCALS_EH_OR_MAXSTACK')
    m = {**m, 'evidence': {**m['evidence'], 'declaredMaxStack': maximum, 'requiredMaxStack': stack}}
    return m


def _pattern(rows):
    """Resolve labels in an independent closed grammar, never in input IL."""
    labels = {}; out = []
    for row in rows:
        if isinstance(row, str): labels[row] = len(out)
        else: out.append(row)
    return [(op, '@' + str(labels[arg])) if isinstance(arg, str) and arg in labels else (op, arg)
            for op, arg in (v if isinstance(v, tuple) else (v, None) for v in out)]


@dataclass(frozen=True)
class ColorContract:
    named: dict
    evidence: dict


def prove_color_intrinsics(p):
    """Whole finite method grammars; internal original-fixture composition API."""
    rid, t = p.owner(OWNER); tr, _ = p.row(2, rid)
    _require(tr[0] & 7 == 1 and tr[0] & 0x18 == 8 and tr[0] & 0x100
             and not tr[0] & (0x20 | 0x80), 'COLOR_VALUE_LAYOUT_FLAGS')
    _require(tr[3] & 3 == 1 and 0 < tr[3] >> 2 <= min(p.meta.rows[1], 0xffffff), 'COLOR_VALUE_BASE')
    _core(p, 0x01000000 | tr[3] >> 2, 'System.ValueType')
    _require(t['lastField'] == t['firstField'] + 1, 'COLOR_EXACT_SINGLE_FIELD')
    f, _ = p.row(4, t['firstField']); field = 0x04000000 | t['firstField']
    _require(f[0] == 1 and p.meta.string(f[1]) == 'packedValue' and p.meta.blob(f[2])[0] == b'\x06\x09',
             'COLOR_PACKED_UINT_FIELD')
    _require(not any(r[2] == rid for r in p.table(15)) and
             not any(r[1] == t['firstField'] for r in p.table(16)), 'COLOR_EXPLICIT_FIELD_LAYOUT')
    _require(not any(p.meta.string(p.row(6, n)[0][3]) == '.cctor'
                     for n in range(t['firstMethod'], t['lastMethod'])), 'COLOR_TYPE_INITIALIZER')
    c = b'\x11' + _compressed_bytes(rid << 2)
    methods = []
    def method(name, sig, flags, stack, local=b''):
        m = _method(p, name, sig, flags, stack, local); methods.append(m); return m
    packed = method('.ctor', b'\x20\x01\x01\x09', 0x1881, 2)
    _shape(packed['instructions'], [2, 3, (0x7d, field), 0x2a])
    clamp = method('ClampToByte64', b'\x00\x01\x08\x0a', 0x91, 2)
    _shape(clamp['instructions'], _pattern([2, 0x16, 0x6a, (0x2f, 'lower'), 0x16, 0x2a,
        'lower', 2, (0x20,255), 0x6a, (0x31,'upper'), (0x20,255), 0x2a,
        'upper', 2, 0x69, 0x2a]))
    rgb = method('.ctor', b'\x20\x03\x01\x08\x08\x08', 0x1886, 3)
    _shape(rgb['instructions'], _pattern([3,4,0x60,5,0x60,(0x20,-256),0x5f,(0x2c,'bytes'),
        3,0x6a,(0x28,clamp['token']),(0x10,1),4,0x6a,(0x28,clamp['token']),(0x10,2),
        5,0x6a,(0x28,clamp['token']),(0x10,3),'bytes',4,0x1e,0x62,(0x10,2),
        5,(0x1f,16),0x62,(0x10,3),2,3,4,0x60,5,0x60,(0x20,-16777216),0x60,(0x7d,field),0x2a]))
    for channel, shift in [('R',0),('G',8),('B',16),('A',24)]:
        m = method('get_'+channel, b'\x20\x00\x05', 0x886, 1 if shift == 0 else 2)
        middle = [] if shift == 0 else [0x1e if shift == 8 else (0x1f,shift), 0x64]
        _shape(m['instructions'], [2,(0x7b,field),*middle,0xd2,0x2a])
    alpha = method('set_A', b'\x20\x01\x01\x05', 0x886, 4)
    _shape(alpha['instructions'], [2,2,(0x7b,field),(0x20,16777215),0x5f,3,(0x1f,24),0x62,0x60,(0x7d,field),0x2a])
    equal = method('Equals', b'\x20\x01\x02'+c, 0x1e6, 2)
    cap = _shape(equal['instructions'], [2,(0x7c,field),(0x0f,1),(0x7b,field),(0x28,'eq'),0x2a])
    p.member(cap['eq'], 'System.UInt32', 'Equals', b'\x20\x01\x02\x09')
    mr, _ = p.row(10, cap['eq'] & 0xffffff); _core(p,0x01000000 | mr[0] >> 3,'System.UInt32')
    equal_op = method('op_Equality', b'\x00\x02\x02'+c+c, 0x896, 2)
    _shape(equal_op['instructions'], [(0x0f,0),3,(0x28,equal['token']),0x2a])
    mul = method('op_Multiply', b'\x00\x02'+c+c+b'\x0c', 0x896, 4, b'\x07\x07'+b'\x09'*6+c)
    rows=[(0x0f,0),(0x7b,field),(0x13,5),(0x11,5),0xd2,(0x13,4),
        (0x11,5),0x1e,0x64,0xd2,0x0d,(0x11,5),(0x1f,16),0x64,0xd2,0x0c,
        (0x11,5),(0x1f,24),0x64,0xd2,0x0b,3,(0x22,65536.0),0x5a,(0x10,1),
        3,(0x22,0.0),(0x34,'nonnegative'),0x16,0x0a,(0x2b,'factor'),
        'nonnegative',3,(0x22,16777215.0),(0x36,'convert'),(0x20,16777215),0x0a,(0x2b,'factor'),
        'convert',3,0x6d,0x0a,'factor']
    for load,store in [((0x11,4),(0x13,4)),(9,0x0d),(8,0x0c),(7,0x0b)]:
        rows.extend([load,6,0x5a,(0x1f,16),0x64,store])
    for n,(load,store) in enumerate([((0x11,4),(0x13,4)),(9,0x0d),(8,0x0c),(7,0x0b)]):
        label='lane'+str(n);rows.extend([load,(0x20,255),(0x36,label),(0x20,255),store,label])
    rows.extend([(0x12,6),(0x11,4),9,0x1e,0x62,0x60,8,(0x1f,16),0x62,0x60,
                 7,(0x1f,24),0x62,0x60,(0x7d,field),(0x11,6),0x2a])
    _shape(mul['instructions'], _pattern(rows))
    named = {}
    for name in ('Transparent','Black','Gray','LightGray'):
        m=method('get_'+name,b'\x00\x00'+c,0x896,1);il=m['instructions']
        _require(len(il)==3,'COLOR_NAMED_BODY');_shape(il[1:],[(0x73,packed['token']),0x2a])
        named[name]=_constant(il[0]) & 0xffffffff
    return ColorContract(named, {'status':'PROVEN_FINITE_COLOR_VALUE_BODIES','valueTypeSingleUInt32':True,
        'wholeBodiesProven':True,'methodEvidence':[m['evidence'] for m in methods],
        'ordinaryCoreUInt32EqualsAssumed':True,'moduleInitializationProven':False,
        'runtimeDependencyBindingVerified':False,'executedInput':False,'complete':False,'publishable':False})


def rgba_from_ints(r,g,b):
    _require(all(type(v) is int and -2**31<=v<2**31 for v in (r,g,b)), 'COLOR_INT32_ARGUMENT')
    return 0xff000000 | sum(min(255,max(0,v)) << (8*i) for i,v in enumerate((r,g,b)))


def multiply_rgba(packed, scale):
    """Exact finite binary32 literal -> Q16 intrinsic, including negative clamps.

    Multiplication by 2**16 is an exponent shift, with no mantissa rounding.
    Overflow clamps to the same positive/negative endpoint; finite subnormals
    become larger. The converted factor and every byte product fit uint32.
    """
    _require(type(packed) is int and 0<=packed<=0xffffffff and type(scale) is float
             and math.isfinite(scale), 'COLOR_FINITE_ARGUMENT')
    try: is_f32 = struct.unpack('<f',struct.pack('<f',scale))[0] == scale
    except OverflowError: is_f32 = False
    _require(is_f32, 'COLOR_SCALE_NOT_BINARY32')
    n,d=scale.as_integer_ratio(); factor=min(16777215,max(0,(n*65536)//d))
    return sum(min(255,(((packed>>(8*i))&255)*factor)>>16)<<(8*i) for i in range(4))


def extract_color_intrinsics(input_path: Path, checkpoint=None):
    check=checkpoint or (lambda:None);check()
    raw=read_assembly_bytes(input_path,SemanticLimits(input_bytes=2*1024*1024),check)
    _require(sha256(raw)==VENDOR_SHA256,'COLOR_UNSUPPORTED_VENDOR_PROFILE')
    limits=ItemTextureAliasLimits(input_bytes=2*1024*1024,steps=1000000,instructions=10000,evidence_bytes=256*1024)
    try:
        p=_Program(raw,_Budget(limits,check));identity=_assembly(p.meta)
        _require(identity['name']=='Microsoft.Xna.Framework' and identity['version']=='4.0.0.0'
                 and identity['publicKeyToken']=='842cf8be1de50553' and not identity['culture'],
                 'COLOR_VENDOR_ASSEMBLY_IDENTITY')
        contract=prove_color_intrinsics(p)
        result={'schemaVersion':1,'inputSha256':VENDOR_SHA256,'assembly':identity,
                'namedColors':contract.named,**contract.evidence,
                'scope':'pure Color bodies only; not native/module startup or a runtime loader proof'}
        json_evidence_size(result,limits.evidence_bytes,p.budget.check)
        return result
    except _CheckpointCancelled as exc:raise exc.original
