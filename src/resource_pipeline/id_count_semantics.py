"""Finite independent ID Count initializer proofs, using PE/CLI bytes only.

The public entry accepts two fixed source hashes. A closed grammar covers the
whole Count initializer and every statement/callback on its ReLogic tail.
Framework identities are checked declarations under explicit intrinsic contracts,
not resolved framework byte hashes. No executable image or CLR is loaded.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct

from .item_texture_aliases import (
    ItemTextureAliasLimits, _Budget, _Program, _CheckpointCancelled, _constant,
    _compressed_bytes, _hex, _require, _shape,
)
from .security import sha256
from .server_semantics import SemanticLimits, read_assembly_bytes
from .source_closure_audit import _assembly
from .static_il import _compressed, _type, decode_il, StaticILLimits, json_evidence_size

_ID = 'ReLogic.Reflection.IdDictionary'
_CLOSURE = _ID + '+<>c__DisplayClass15_0'
_SINGLETON = _ID + '+<>c'
_DEP_SHA = 'e1c5dccefff5fd1c789ff712babfa1a305fced0d03c96ef30f2c14d99aa0af29'
_CCTOR_SIG = b'\x00\x00\x01'
_CCTOR_SHA = sha256(_CCTOR_SIG)
_PROFILES = {
 '960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3': ('client', {
  'ItemID': (0x060020df, 'b20114f00d5aadcd7cda2fbedfa8377d78fc08650d961ae0a4a4732457356b15', 6196, 6244),
  'TileID': (0x060020ee, 'bfbc13096450f6d7f697e3be626ab96294eb29c81d60a67e1e15b3a291292396', 754, 754),
  'WallID': (0x060020e7, 'fc2f1c122ba0ff16021c6ac11d0ca670c20e08014da2f49efa6dc7bd4ae9596c', 367, 367)}),
 'd87e3faf08637f6be8882c63e7f11fb7e792b0230006309618473ece0f863e1e': ('server', {
  'ItemID': (0x06001fcc, '4f7fec29de602e0798eeb5caca3a0dd2896eb68a22c34633181afe38b67d2fbe', 6196, 6244),
  'TileID': (0x06001fea, 'e021a483e53624c4cd3eb62e4e128ad1af5b05dd57391706936803f5da2c5c94', 754, 754),
  'WallID': (0x06001fec, 'ca4202f94562fedaae7e584d86527405e9ab417064ef35060dccf2ea943e2603', 367, 367)}),
}
# Pins supplement, never replace, the grammar/type/effect checks below.
_DEP_PINS = {
 0x0600009a: ('67f9f7401630d6052f74f83db761df261bad818b7feaaec3a8541315eea98068','c889396c66b535112cb33472b6ecf61ad5f09860a1ea642fb8c31a4ed004296a'),
 0x060000a4: ('fb4c560025bba21c3c869b412335ca37c7a73a5807878cc07c7f9fd0207124bb','2a6957b79191e91518061c617d8758e018134680a7fb0e7fa076e899f3bbfeef'),
 0x060000a5: ('55679e4ef1aadd702c988ee5d9579a6018d81ba589b9f7cd2607770d963690ca','9d64a75cebc97f33af92c5f538a85b38d6ba3656ee03aa2141fb7570b41caf4e'),
 0x06000414: ('b8fef064950de03da7ee3d2d734c31f347006811e80dc633a1eccab22e575091','0c9373898d2dd1c85902837a9e979cb88484e2548a008ad2b2176cc7119d09df'),
 0x06000415: ('fd4b46cef5d86ca078c23493c5cdaca7990a41e28e5c75d9abcb466192cede4c','b8f0b347f340152f07520923977bf9495b4367ce8fbb0ad50609c023fd0bc079'),
 0x06000416: ('1b98b42d46b85aa5b85d93c4f9ef2baf0ff65f04a1c19c4f979b78d7f78deef8','ad7a2a38c365452a7db6a11a771aa27bde1f775629fbffd2ad24b35d53c68ccd'),
 0x06000417: ('efa05c28867276dc8cbfb0dedba6f10985f959cb3db58b342e8e80db3b9b9f51',_CCTOR_SHA),
 0x06000418: ('b8fef064950de03da7ee3d2d734c31f347006811e80dc633a1eccab22e575091','0c9373898d2dd1c85902837a9e979cb88484e2548a008ad2b2176cc7119d09df'),
 0x06000419: ('eb9c7f8bf8efd9f61b90a3ffc41b9e3e25d86b7c5b8c94f0b7e1e306b6bf13aa','b8f0b347f340152f07520923977bf9495b4367ce8fbb0ad50609c023fd0bc079'),
 0x0600041a: ('af94ff3d8601150f4819c6a9d6951d89be3f641231d730662cebec5cf63a1103','cdecfed883d7e512f0ee3de57e66b55431538ec0ced56bd6bdd3ecc722882045'),
 0x0600041b: ('e544dc3f5fd6200d7dc71b2bcfa319d91a527a472dd9b39d4043386a7b812211','6a6f3ad18729f42ae2ab544b2bf1533186c4293190f9753570b9f73fadc0ca56'),
}

# Symbolic signatures retain class/value distinctions and generic argument order.
def C(name): return ('class', name)
def V(name): return ('value', name)
def G(name, *args, value=False): return ('generic', V(name) if value else C(name), args)
TYPE = C('System.Type')
FIELD = C('System.Reflection.FieldInfo')
PAIR = G('System.Collections.Generic.KeyValuePair`2', 'string', 'i4', value=True)
DICT = G('System.Collections.Generic.Dictionary`2', 'string', 'i4')
REVERSE = G('System.Collections.Generic.Dictionary`2', 'i4', 'string')
def FUNC(a, b): return G('System.Func`2', a, b)
def ACTION(a): return G('System.Action`1', a)
def ENUM(a): return G('System.Collections.Generic.IEnumerable`1', a)
def LIST(a): return G('System.Collections.Generic.List`1', a)
def M(ret, *args, this=False, generic=0): return (0x20 * this | 0x10 * bool(generic), generic, ret, args)
_VAR = ('var', 0)
_M0, _M1, _M2 = (('mvar', n) for n in range(3))


def _rid(p, table, rid):
    _require(type(rid) is int and 0 < rid <= min(p.meta.rows[table], 0xffffff), 'COUNT_METADATA_RID_RANGE')
    return rid


def _assembly_ref(p, rid, name):
    _rid(p, 35, rid)
    r, _ = p.row(35, rid)
    expected = (1, 0, 0, 0) if name == 'ReLogic' else (4, 0, 0, 0)
    key = b'' if name == 'ReLogic' else bytes.fromhex('b77a5c561934e089')
    _require(name in ('ReLogic', 'mscorlib', 'System.Core')
             and tuple(r[:4]) == expected and not r[4] and p.meta.string(r[6]) == name
             and not p.meta.string(r[7]) and p.meta.blob(r[5])[0] == key,
             'COUNT_ASSEMBLY_IDENTITY')


def _type_name(p, coded):
    tag, rid = coded & 3, coded >> 2
    if tag == 0:
        _rid(p, 2, rid)
        _require(rid in p.types, 'COUNT_LOCAL_TYPE_TOKEN')
        name = p.types[rid]['fullName']
        _require(name in (_ID, _CLOSURE, _SINGLETON, 'Terraria.ID.ItemID', 'Terraria.ID.TileID', 'Terraria.ID.WallID'),
                 'COUNT_UNTRUSTED_LOCAL_SIGNATURE_TYPE')
        if name.startswith('ReLogic.'):
            _require(_assembly(p.meta)['name'] == 'ReLogic', 'COUNT_LOCAL_DEPENDENCY_TYPE_IDENTITY')
        return name
    _require(tag == 1, 'COUNT_TYPE_SIGNATURE_TOKEN')
    _rid(p, 1, rid)
    r, _ = p.row(1, rid)
    name = (p.meta.string(r[2]) + '.' if p.meta.string(r[2]) else '') + p.meta.string(r[1])
    _require(r[0] & 3 == 2 and r[0] >> 2, 'COUNT_TYPE_SCOPE')
    _require(name == _ID or name in ('System.Object', 'System.Type', 'System.Reflection.FieldInfo',
             'System.Reflection.MemberInfo', 'System.Convert', 'System.String', 'System.Exception',
             'System.RuntimeTypeHandle', 'System.Reflection.BindingFlags', 'System.Linq.Enumerable',
             'System.Collections.Generic.Dictionary`2', 'System.Collections.Generic.KeyValuePair`2',
             'System.Func`2', 'System.Action`1', 'System.Collections.Generic.IEnumerable`1',
             'System.Collections.Generic.List`1'), 'COUNT_UNTRUSTED_EXTERNAL_SIGNATURE_TYPE')
    assembly = 'ReLogic' if name == _ID else 'System.Core' if name == 'System.Linq.Enumerable' else 'mscorlib'
    _assembly_ref(p, r[0] >> 2, assembly)
    return name


def _sig_type(p, blob, pos=0, depth=0):
    p.budget.check()
    _require(depth < 16 and pos < len(blob), 'COUNT_SIGNATURE_DEPTH_OR_TRUNCATED')
    op = blob[pos]; pos += 1
    primitives = {1:'void',2:'bool',6:'i2',7:'u2',8:'i4',14:'string',24:'nativeint',28:'object'}
    if op in primitives: return primitives[op], pos
    if op in (0x11, 0x12):
        coded, pos = _compressed(blob, pos)
        return (C if op == 0x12 else V)(_type_name(p, coded)), pos
    if op in (0x13, 0x1e):
        n, pos = _compressed(blob, pos)
        _require(n <= 2, 'COUNT_GENERIC_INDEX')
        return ('var' if op == 0x13 else 'mvar', n), pos
    if op == 0x1d:
        element, pos = _sig_type(p, blob, pos, depth + 1)
        return ('array', element), pos
    if op == 0x15:
        owner, pos = _sig_type(p, blob, pos, depth + 1)
        _require(isinstance(owner, tuple) and owner[0] in ('class', 'value'), 'COUNT_GENERIC_OWNER')
        n, pos = _compressed(blob, pos)
        _require(1 <= n <= 3, 'COUNT_GENERIC_ARITY')
        args = []
        for _ in range(n):
            a, pos = _sig_type(p, blob, pos, depth + 1); args.append(a)
        return ('generic', owner, tuple(args)), pos
    _require(False, 'COUNT_UNSUPPORTED_SIGNATURE_TYPE')


def _type_blob(p, blob):
    value, pos = _sig_type(p, blob)
    _require(pos == len(blob), 'COUNT_SIGNATURE_TRAILING_BYTES')
    return value


def _method_sig(p, blob):
    _require(blob and blob[0] in (0, 0x20, 0x10), 'COUNT_METHOD_SIGNATURE_FLAGS')
    flags, pos, generic = blob[0], 1, 0
    if flags & 0x10: generic, pos = _compressed(blob, pos)
    n, pos = _compressed(blob, pos)
    _require(n <= 4 and generic <= 3, 'COUNT_METHOD_SIGNATURE_ARITY')
    ret, pos = _sig_type(p, blob, pos); args = []
    for _ in range(n):
        arg, pos = _sig_type(p, blob, pos); args.append(arg)
    _require(pos == len(blob), 'COUNT_SIGNATURE_TRAILING_BYTES')
    return flags, generic, ret, tuple(args)


def _member(p, token, owner, name, signature):
    _require(token >> 24 == 10, 'COUNT_MEMBER_TOKEN')
    r, _ = p.row(10, token & 0xffffff)
    tag, rid = r[0] & 7, r[0] >> 3
    _require(tag in (1, 4), 'COUNT_MEMBER_OWNER')
    _rid(p, {1:1, 4:27}[tag], rid)
    if tag == 1: actual = C(_type_name(p, rid << 2 | 1))
    elif tag == 4: actual = _type_blob(p, p.type_signature(0x1b000000 | rid))
    else: _require(False, 'COUNT_MEMBER_OWNER')
    _require(actual == owner and p.meta.string(r[1]) == name
             and _method_sig(p, p.meta.blob(r[2])[0]) == signature, 'COUNT_MEMBER_SIGNATURE_OR_OWNER')


def _spec(p, token, name, signature, arguments):
    _require(token >> 24 == 43, 'COUNT_METHODSPEC_TOKEN')
    r, _ = p.row(43, token & 0xffffff)
    _require(r[0] & 1 == 1, 'COUNT_METHODSPEC_TARGET')
    _rid(p, 10, r[0] >> 1)
    _member(p, 0x0a000000 | r[0] >> 1, C('System.Linq.Enumerable'), name, signature)
    blob = p.meta.blob(r[1])[0]
    _require(blob[:1] == b'\x0a', 'COUNT_METHODSPEC_PREFIX')
    n, pos = _compressed(blob, 1); actual = []
    _require(n == len(arguments), 'COUNT_METHODSPEC_ARITY')
    for _ in range(n):
        arg, pos = _sig_type(p, blob, pos); actual.append(arg)
    _require(pos == len(blob) and tuple(actual) == arguments, 'COUNT_METHODSPEC_ARGUMENTS')


def _no_cctor(p, owner):
    _, t = p.owner(owner)
    _require(not any(p.meta.string(p.row(6, n)[0][3]) == '.cctor'
                     for n in range(t['firstMethod'], t['lastMethod'])), 'COUNT_UNEXPECTED_TYPE_INITIALIZER')


def _owner(p, name, *, nested=False):
    rid, t = p.owner(name); row, _ = p.row(2, rid)
    _require(row[0] & 0x18 == 0 and not row[0] & (0x20 | 0x80)
             and row[3] & 3 == 1 and _type_name(p, row[3]) == 'System.Object', 'COUNT_OWNER_BASE_OR_LAYOUT')
    if nested:
        _require(row[0] & 7 == 3 and row[0] & 0x100, 'COUNT_CALLBACK_OWNER_NOT_PRIVATE_SEALED')
    _require(not any(r[2] == rid for r in p.table(15))
             and not any(t['firstField'] <= r[1] < t['lastField'] for r in p.table(16)), 'COUNT_OWNER_FIELD_LAYOUT')
    return rid, t


def _field(p, owner, name, signature, flags):
    _, t = p.owner(owner); found = []
    for rid in range(t['firstField'], t['lastField']):
        r, _ = p.row(4, rid)
        if p.meta.string(r[1]) == name: found.append((rid, r))
    _require(len(found) == 1, 'COUNT_FIELD_UNIQUE')
    rid, r = found[0]; blob = p.meta.blob(r[2])[0]
    _require(r[0] == flags and blob[:1] == b'\x06' and _type_blob(p, blob[1:]) == signature,
             'COUNT_FIELD_FLAGS_OR_SIGNATURE')
    return 0x04000000 | rid


def _method(p, owner, name, signature, *, locals=(), flags=None):
    _, t = p.owner(owner); found = []
    for rid in range(t['firstMethod'], t['lastMethod']):
        r, _ = p.row(6, rid)
        if p.meta.string(r[3]) == name and _method_sig(p, p.meta.blob(r[4])[0]) == signature:
            found.append(0x06000000 | rid)
    _require(len(found) == 1, 'COUNT_METHOD_UNIQUE')
    method = p.body(found[0]); row, _ = p.row(6, found[0] & 0xffffff)
    _require(flags is None or row[2] == flags, 'COUNT_METHOD_FLAGS')
    blob = method['local']; actual = []
    if blob:
        _require(blob[:1] == b'\x07', 'COUNT_LOCALS_SIGNATURE')
        n, pos = _compressed(blob, 1)
        _require(n == len(locals), 'COUNT_LOCALS_SIGNATURE')
        for _ in range(n):
            a, pos = _sig_type(p, blob, pos); actual.append(a)
        _require(pos == len(blob), 'COUNT_LOCALS_SIGNATURE')
    _require(tuple(actual) == locals, 'COUNT_LOCALS_SIGNATURE')
    return method


def _stack(p, m):
    """CFG stack-height proof for the closed methods, including both cache arms."""
    il = m['instructions']; by = {i.offset: i for i in il}
    _require(il and not m['eh'], 'COUNT_EMPTY_OR_EH_METHOD')
    returns = _method_sig(p, m['signature'])[2] != 'void'
    incoming, todo, maximum = {0: 0}, [0], 0
    while todo:
        p.budget.check(); at = todo.pop(); ins = by[at]; op = ins.opcode; h = incoming[at]
        pop = push = 0; targets = [ins.next_offset]
        if op in (2,3,4,6,7,8,0x0f,0x14,0x1f,0x20,0x72,0x7e,0xd0,0xfe06): push = 1
        elif op in (0x0a,0x0b,0x0c,0x26,0x80): pop = 1
        elif op == 0x25: pop,push = 1,2
        elif op == 0x7b: pop,push = 1,1
        elif op == 0x7d: pop = 2
        elif op in (0x28,0x6f,0x73):
            tok = ins.operand
            if tok >> 24 == 43:
                r, _ = p.row(43, tok & 0xffffff); tok = (0x0a000000 if r[0] & 1 else 0x06000000) | r[0] >> 1
            r, _ = p.row(tok >> 24, tok & 0xffffff)
            flags, _, ret, args = _method_sig(p, p.meta.blob(r[4] if tok >> 24 == 6 else r[2])[0])
            pop, push = len(args) + bool(flags & 0x20), int(ret != 'void')
            if op == 0x73: pop -= 1; push = 1
        elif op in (0x2c,0x2d): pop = 1; targets.append(ins.operand)
        elif op == 0x2f: pop = 2; targets.append(ins.operand)
        elif op == 0x7a: pop = 1; targets = []
        elif op == 0x2a:
            pop = int(returns); targets = []
            _require(h == pop, 'COUNT_RETURN_STACK')
        else: _require(False, 'COUNT_UNSUPPORTED_STACK_INSTRUCTION')
        _require(h >= pop, 'COUNT_STACK_UNDERFLOW'); h = h - pop + push; maximum = max(maximum, h)
        for target in targets:
            _require(target in by, 'COUNT_STACK_BRANCH_TARGET')
            if target in incoming: _require(incoming[target] == h, 'COUNT_STACK_JOIN')
            else: incoming[target] = h; todo.append(target)
    declared = 8 if m['header'] == 1 else p.meta.reader.uint(m['evidence']['bodyOffset'] + 2, 2)
    _require(len(incoming) == len(il) and declared >= maximum, 'COUNT_MAXSTACK_OR_UNREACHABLE')
    return {'declaredMaxStack': declared, 'requiredMaxStack': maximum, 'allInstructionsReachable': True,
            'allStackJoinsConsistent': True, 'instructionCount': len(il)}


@dataclass(frozen=True)
class _DependencyProof:
    program: object
    evidence: dict


def prove_id_dictionary_dependency(p):
    """Internal grammar proof, also exercised by original synthetic PE fixtures."""
    for name in (_ID, _CLOSURE, _SINGLETON): _owner(p, name, nested=name != _ID)
    for name in ('<Module>', _ID, _CLOSURE): _no_cctor(p, name)
    # Exact instance fields rule out overlapping storage and arbitrary references.
    fields = {
      'names': _field(p,_ID,'_nameToId',DICT,0x21),
      'reverse': _field(p,_ID,'_idToName',REVERSE,1),
      'count': _field(p,_ID,'Count','i4',0x26),
      'type': _field(p,_CLOSURE,'idType',TYPE,6),
      'dictionary': _field(p,_CLOSURE,'dictionary',C(_ID),6),
      'singleton': _field(p,_SINGLETON,'<>9',C(_SINGLETON),0x36),
      'predicate': _field(p,_SINGLETON,'<>9__15_0',FUNC(FIELD,'bool'),0x16),
      'value': _field(p,_SINGLETON,'<>9__15_3',FUNC(PAIR,'i4'),0x16),
      'key': _field(p,_SINGLETON,'<>9__15_4',FUNC(PAIR,'string'),0x16),
    }
    for owner,n in ((_ID,3),(_CLOSURE,2),(_SINGLETON,4)):
        _,t=p.owner(owner);_require(t['lastField']-t['firstField']==n,'COUNT_EXACT_DEPENDENCY_FIELDS')
    for owner,n in ((_CLOSURE,3),(_SINGLETON,5)):
        _,t=p.owner(owner);_require(t['lastMethod']-t['firstMethod']==n,'COUNT_EXACT_CALLBACK_METHODS')
    ctor = _method(p,_ID,'.ctor',M('void','i4',this=True),flags=0x1881)
    create = _method(p,_ID,'Create',M(C(_ID),TYPE,TYPE),locals=(C(_CLOSURE),'i4',FIELD),flags=0x96)
    generic = _method(p,_ID,'Create',M(C(_ID),generic=2),flags=0x96)
    closure = _method(p,_CLOSURE,'.ctor',M('void',this=True),flags=0x1886)
    predicate = _method(p,_CLOSURE,'<Create>b__1',M('bool',FIELD,this=True),flags=0x83)
    action = _method(p,_CLOSURE,'<Create>b__2',M('void',FIELD,this=True),locals=('i4',),flags=0x83)
    init = _method(p,_SINGLETON,'.cctor',M('void'),flags=0x1891)
    singleton = _method(p,_SINGLETON,'.ctor',M('void',this=True),flags=0x1886)
    named = _method(p,_SINGLETON,'<Create>b__15_0',M('bool',FIELD,this=True),flags=0x83)
    value = _method(p,_SINGLETON,'<Create>b__15_3',M('i4',PAIR,this=True),flags=0x83)
    key = _method(p,_SINGLETON,'<Create>b__15_4',M('string',PAIR,this=True),flags=0x83)
    methods = [ctor,create,generic,closure,predicate,action,init,singleton,named,value,key]
    for m in (closure,singleton):
        cap=_shape(m['instructions'],[2,(0x28,'object'),0x2a])
        _member(p,cap['object'],C('System.Object'),'.ctor',M('void',this=True))
    cap=_shape(ctor['instructions'],[2,(0x73,'dict'),(0x7d,fields['names']),2,(0x28,'object'),2,3,(0x7d,fields['count']),0x2a])
    _member(p,cap['dict'],DICT,'.ctor',M('void',this=True))
    _member(p,cap['object'],C('System.Object'),'.ctor',M('void',this=True))
    _shape(init['instructions'],[(0x73,singleton['token']),(0x80,fields['singleton']),0x2a])
    cap=_shape(generic['instructions'],[(0xd0,'container'),(0x28,'handle'),(0xd0,'primitive'),(0x28,'handle'),(0x28,create['token']),0x2a])
    for tok,arg in ((cap['container'],_M0),(cap['primitive'],_M1)):
        _require(tok>>24==27 and _type_blob(p,p.type_signature(tok))==arg,'COUNT_GENERIC_RUNTIME_TYPE_HANDLE')
    _member(p,cap['handle'],TYPE,'GetTypeFromHandle',M(TYPE,V('System.RuntimeTypeHandle')))
    cap=_shape(predicate['instructions'],[3,(0x6f,'fieldType'),2,(0x7b,fields['type']),(0x28,'equals'),0x2a])
    _member(p,cap['fieldType'],FIELD,'get_FieldType',M(TYPE,this=True))
    _member(p,cap['equals'],TYPE,'op_Equality',M('bool',TYPE,TYPE))
    cap=_shape(named['instructions'],[3,(0x6f,'name'),(0x72,'countName'),(0x28,'equals'),0x2a])
    _require(p.user_string(cap['countName'])=='Count','COUNT_NAME_PREDICATE')
    _member(p,cap['name'],C('System.Reflection.MemberInfo'),'get_Name',M('string',this=True))
    _member(p,cap['equals'],C('System.String'),'op_Equality',M('bool','string','string'))
    for method,name,ret in ((value,'get_Value',('var',1)),(key,'get_Key',_VAR)):
        cap=_shape(method['instructions'],[(0x0f,1),(0x28,'accessor'),0x2a])
        _member(p,cap['accessor'],PAIR,name,M(ret,this=True))
    cap=_shape(action['instructions'],[3,0x14,(0x6f,'read'),(0x28,'convert'),0x0a,6,2,
          (0x7b,fields['dictionary']),(0x7b,fields['count']),(0x2f,'@17'),2,
          (0x7b,fields['dictionary']),(0x7b,fields['names']),3,(0x6f,'name'),6,(0x6f,'add'),0x2a])
    _member(p,cap['read'],FIELD,'GetValue',M('object','object',this=True))
    _member(p,cap['convert'],C('System.Convert'),'ToInt32',M('i4','object'))
    _member(p,cap['name'],C('System.Reflection.MemberInfo'),'get_Name',M('string',this=True))
    _member(p,cap['add'],DICT,'Add',M('void',_VAR,('var',1),this=True))
    # This is the entire Create(Type,Type), not a call list or hash-only summary.
    pattern=[(0x73,closure['token']),0x0a,6,3,(0x7d,fields['type']),(0x20,2147483647),0x0b,2,
      (0x6f,'getFields'),(0x7e,fields['predicate']),0x25,(0x2d,'@18'),0x26,
      (0x7e,fields['singleton']),(0xfe06,named['token']),(0x73,'predicateCtor'),0x25,(0x80,fields['predicate']),
      (0x28,'first'),0x0c,8,0x14,(0x28,'notnull'),(0x2c,'@34'),8,0x14,(0x6f,'read'),(0x28,'convert'),
      0x0b,7,(0x2d,'@34'),(0x72,'error'),(0x73,'exception'),0x7a,6,7,(0x73,ctor['token']),
      (0x7d,fields['dictionary']),2,(0x1f,24),(0x6f,'staticFields'),6,(0xfe06,predicate['token']),
      (0x73,'predicateCtor'),(0x28,'where'),(0x28,'list'),6,(0xfe06,action['token']),
      (0x73,'actionCtor'),(0x6f,'each'),6,(0x7b,fields['dictionary']),6,(0x7b,fields['dictionary']),
      (0x7b,fields['names']),(0x7e,fields['value']),0x25,(0x2d,'@64'),0x26,
      (0x7e,fields['singleton']),(0xfe06,value['token']),(0x73,'valueCtor'),0x25,(0x80,fields['value']),
      (0x7e,fields['key']),0x25,(0x2d,'@73'),0x26,(0x7e,fields['singleton']),
      (0xfe06,key['token']),(0x73,'keyCtor'),0x25,(0x80,fields['key']),
      (0x28,'reverse'),(0x7d,fields['reverse']),6,(0x7b,fields['dictionary']),0x2a]
    cap=_shape(create['instructions'],pattern)
    _member(p,cap['getFields'],TYPE,'GetFields',M(('array',FIELD),this=True))
    _member(p,cap['staticFields'],TYPE,'GetFields',M(('array',FIELD),V('System.Reflection.BindingFlags'),this=True))
    _member(p,cap['notnull'],FIELD,'op_Inequality',M('bool',FIELD,FIELD))
    _member(p,cap['read'],FIELD,'GetValue',M('object','object',this=True))
    _member(p,cap['convert'],C('System.Convert'),'ToInt32',M('i4','object'))
    p.user_string(cap['error'])
    _member(p,cap['exception'],C('System.Exception'),'.ctor',M('void','string',this=True))
    for name,owner in (('predicateCtor',FUNC(FIELD,'bool')),('actionCtor',ACTION(FIELD)),
                       ('valueCtor',FUNC(PAIR,'i4')),('keyCtor',FUNC(PAIR,'string'))):
        _member(p,cap[name],owner,'.ctor',M('void','object','nativeint',this=True))
    _member(p,cap['each'],LIST(FIELD),'ForEach',M('void',ACTION(_VAR),this=True))
    _spec(p,cap['first'],'FirstOrDefault',M(_M0,ENUM(_M0),FUNC(_M0,'bool'),generic=1),(FIELD,))
    _spec(p,cap['where'],'Where',M(ENUM(_M0),ENUM(_M0),FUNC(_M0,'bool'),generic=1),(FIELD,))
    _spec(p,cap['list'],'ToList',M(LIST(_M0),ENUM(_M0),generic=1),(FIELD,))
    _spec(p,cap['reverse'],'ToDictionary',M(G('System.Collections.Generic.Dictionary`2',_M1,_M2),
          ENUM(_M0),FUNC(_M0,_M1),FUNC(_M0,_M2),generic=3),(PAIR,'i4','string'))
    method_evidence=[{**m['evidence'],**_stack(p,m)} for m in methods]
    expected_writes={(init['token'],init['instructions'][1].offset,fields['singleton'])}
    expected_writes.update((create['token'],i.offset,i.operand) for i in create['instructions'] if i.opcode==0x80)
    cache_scan=_scan_cache_writers(p,{fields[n] for n in ('singleton','predicate','value','key')},expected_writes)
    return _DependencyProof(p,{'status':'PROVEN_FINITE_ID_DICTIONARY_EFFECTS',
      'methodEvidence':method_evidence,'genericCreateToken':_hex(generic['token']),
      'cacheWriterScan':cache_scan,'allElevenMethodsWholeBodyProven':True,
      'cachedDelegatesHaveOnlyFixedTargets':True,'instanceAllocationsFreshAndNonEscapingUntilReturn':True,
      'gameFieldWrites':False,'reflectionOperation':'metadata and primitive static-field reads only',
      'callbackEffects':['Count-name equality over runtime FieldInfo metadata',
          'exact primitive FieldType equality',
          'read boxed primitive; convert Int16/UInt16 to Int32; write fresh Dictionary<string,int>',
          'read KeyValuePair<string,int> value/key; build fresh reverse dictionary'],
      'countNonmutationRequiresConcretePrimitiveFieldProof':True})


def _raw_type_name(p, token, depth=0):
    """Name-only alias detection, never confers a trusted intrinsic identity."""
    _require(depth < 16, 'COUNT_SCAN_TYPE_DEPTH')
    table,rid=token>>24,token&0xffffff
    if table==2:
        _require(rid in p.types,'COUNT_SCAN_TYPE_TOKEN'); return p.types[rid]['fullName']
    if table==1:
        r,_=p.row(1,rid); name=p.meta.string(r[1]); namespace=p.meta.string(r[2])
        if r[0]&3==3:
            _rid(p,1,r[0]>>2)
            return _raw_type_name(p,0x01000000|r[0]>>2,depth+1)+'+'+name
        _require(r[0]&3 in (0,1,2),'COUNT_SCAN_TYPE_SCOPE')
        return (namespace+'.' if namespace else '')+name
    _require(table==27,'COUNT_SCAN_TYPE_TOKEN')
    blob=p.type_signature(token); pos=1 if blob[:1]==b'\x15' else 0
    _require(pos<len(blob) and blob[pos] in (0x11,0x12),'COUNT_SCAN_FIELD_OWNER_TYPESPEC')
    coded,_=_compressed(blob,pos+1)
    _require(coded&3 in (0,1),'COUNT_SCAN_FIELD_OWNER_TYPESPEC')
    _rid(p,2 if coded&3==0 else 1,coded>>2)
    return _raw_type_name(p,((2 if coded&3==0 else 1)<<24)|coded>>2,depth+1)


def _scan_cache_writers(p, caches, expected):
    """Decode every RVA-bearing body: no catches/skips for malformed methods.

    This proves only direct writer/address-use closure for four private-owner
    fields, not arbitrary unrelated method effects. Indirect tampering is an
    explicit precondition, never inferred from an operand scan.
    """
    aliases={t:t for t in caches}; names={}
    for t in caches:
        r,_=p.row(4,t&0xffffff); names[p.meta.string(r[1])]=(t,p.meta.blob(r[2])[0])
    for rid in range(1,p.meta.rows[10]+1):
        p.budget.check();r,_=p.row(10,rid); blob=p.meta.blob(r[2])[0]
        if blob[:1]!=b'\x06': continue
        tag,owner=r[0]&7,r[0]>>3
        _require(tag in (0,1,2,4),'COUNT_SCAN_FIELD_MEMBER_OWNER')
        if tag==2: continue  # ModuleRef global fields cannot denote a nested type.
        _rid(p,{0:2,1:1,4:27}[tag],owner)
        name=_raw_type_name(p,({0:2,1:1,4:27}[tag]<<24)|owner)
        field=p.meta.string(r[1])
        if name==_SINGLETON and field in names:
            target,sig=names[field]
            _require(blob==sig,'COUNT_SCAN_CACHE_ALIAS_SIGNATURE')
            aliases[0x0a000000|rid]=target
    writes=set(); uses=[]; bodies=0; bytes_count=0
    for rid in range(1,p.meta.rows[6]+1):
        p.budget.check();r,_=p.row(6,rid)
        if not r[0]:
            _require(bool(r[2]&(0x400|0x2000)) or r[1]&3==3,'COUNT_SCAN_MISSING_METHOD_BODY')
            continue
        _require(r[1]&3==0 and not r[1]&4 and not r[2]&(0x400|0x2000),'COUNT_SCAN_NON_IL_BODY')
        at=p.meta.rva(r[0],1); first=p.meta.reader.uint(at,1)
        if first&3==2: header,size,flags,local=1,first>>2,2,0
        else:
            flags=p.meta.reader.uint(at,2)
            _require(first&3==3 and flags>>12==3 and not flags&~0x301b,'COUNT_SCAN_METHOD_HEADER')
            header,size,local=12,p.meta.reader.uint(at+4,4),p.meta.reader.uint(at+8,4)
        _require(0<size<=p.budget.limits.method_bytes,'COUNT_SCAN_METHOD_BYTES')
        _require(size<=p.budget.limits.total_method_bytes-p.budget.method_bytes,'COUNT_SCAN_TOTAL_METHOD_BYTES')
        p.budget.method_bytes+=size;bytes_count+=size
        raw=p.meta.reader.take(p.meta.rva(r[0]+header,size),size)
        il=decode_il(raw,StaticILLimits(method_bytes=p.budget.limits.method_bytes),p.budget.check,
                     instruction_budget=p.budget.instruction)
        if local:
            _require(local>>24==17,'COUNT_SCAN_LOCAL_TOKEN')
            lr,_=p.row(17,local&0xffffff);blob=p.meta.blob(lr[0])[0]
            _require(blob[:1]==b'\x07' and len(blob)<=16384,'COUNT_SCAN_LOCAL_SIGNATURE')
            n,pos=_compressed(blob,1);_require(n<=4096,'COUNT_SCAN_LOCAL_LIMIT')
            for _ in range(n):
                p.budget.check();kind,pos=_type(blob,pos);_require(kind!='void','COUNT_SCAN_LOCAL_VOID')
            _require(pos==len(blob),'COUNT_SCAN_LOCAL_TRAILING_BYTES')
        if flags&8:
            section=(r[0]+header+size+3)&~3; more=True; sections=0
            while more:
                p.budget.check();sections+=1;_require(sections<=16,'COUNT_SCAN_EH_SECTION_LIMIT')
                address=p.meta.rva(section,4);head=p.meta.reader.take(address,4)
                _require(head[0]&0x3f==1,'COUNT_SCAN_EH_KIND')
                more=bool(head[0]&0x80);fat=bool(head[0]&0x40)
                length=int.from_bytes(head[1:4],'little') if fat else head[1]
                width=24 if fat else 12
                _require(length>=4 and (length-4)%width==0 and (fat or head[2:]==b'\0\0'),'COUNT_SCAN_EH_LENGTH')
                _require(length<=65536,'COUNT_SCAN_EH_LIMIT')
                clauses=p.meta.reader.take(p.meta.rva(section,length),length)[4:]
                boundaries=set(il)|{size}
                for off in range(0,len(clauses),width):
                    p.budget.check()
                    if fat:flag,start,length_,handler,hlen,extra=struct.unpack_from('<IIIIII',clauses,off)
                    else:flag,start,length_,handler,hlen,extra=struct.unpack_from('<HHBHBI',clauses,off)
                    _require(flag in (0,1,2,4) and length_>0 and hlen>0 and start in il and handler in il
                             and start+length_ in boundaries and handler+hlen in boundaries,'COUNT_SCAN_EH_CLAUSE')
                    if flag==1: _require(extra in il and extra<handler,'COUNT_SCAN_EH_FILTER')
                    elif flag in (2,4): _require(extra==0,'COUNT_SCAN_EH_EXTRA')
                    else:
                        _require(extra>>24 in (1,2,27),'COUNT_SCAN_EH_CATCH_TYPE')
                        p.row(extra>>24,extra&0xffffff)
                section=(section+(4+len(clauses))+3)&~3
        bodies+=1
        for i in il.values():
            p.budget.check()
            if i.opcode in (0x7b,0x7c,0x7d,0x7e,0x7f,0x80):
                table=i.operand>>24;rid_=i.operand&0xffffff
                _require(table in (4,10),'COUNT_SCAN_FIELD_TOKEN')
                _rid(p,table,rid_)
                field_row,_=p.row(table,rid_)
                _require(p.meta.blob(field_row[2])[0][:1]==b'\x06','COUNT_SCAN_FIELD_SIGNATURE')
            if i.opcode not in (0x7b,0x7c,0x7d,0x7e,0x7f,0x80,0xd0) or i.operand not in aliases: continue
            target=aliases[i.operand]
            _require(i.opcode in (0x7e,0x80),'COUNT_CACHE_ADDRESS_OR_INVALID_INSTANCE_USE')
            uses.append({'methodToken':_hex(0x06000000|rid),'ilOffset':i.offset,'opcode':i.opcode,'fieldToken':_hex(target)})
            if i.opcode==0x80: writes.add((0x06000000|rid,i.offset,target))
    _require(writes==expected,'COUNT_CACHE_UNEXPECTED_WRITER')
    return {'completeScopedOperandScan':True,'methodBodiesDecoded':bodies,'ilBytesDecoded':bytes_count,
            'directWriterCount':len(writes),'fieldAddressTaking':False,'memberRefAliases':len(aliases)-len(caches),
            'uses':uses,'notAWholeAssemblyEffectProof':True}


def prove_id_count_initializer(p, owner, dependency_proof):
    """Internal typed theorem composition; callers cannot supply boolean claims."""
    _require(type(dependency_proof) is _DependencyProof,'COUNT_DEPENDENCY_CERTIFICATE_TYPE')
    _require(owner in ('Terraria.ID.ItemID','Terraria.ID.TileID','Terraria.ID.WallID'),'COUNT_OWNER_UNSUPPORTED')
    rid,t=_owner(p,owner);_no_cctor(p,'<Module>')
    primitive='i2' if owner.endswith('.ItemID') else 'u2';element=6 if primitive=='i2' else 7
    count_field=_field(p,owner,'Count',primitive,0x36)
    search_field=_field(p,owner,'Search',C(_ID),0x36)
    # Unique .cctor name, not merely a matching overload.
    methods=[n for n in range(t['firstMethod'],t['lastMethod']) if p.meta.string(p.row(6,n)[0][3])=='.cctor']
    _require(len(methods)==1,'COUNT_INITIALIZER_UNIQUE')
    m=_method(p,owner,'.cctor',M('void'),flags=0x1891)
    il=m['instructions'];_require(len(il)==5,'COUNT_WHOLE_INITIALIZER')
    cap=_shape(il[1:],[(0x80,count_field),(0x28,'create'),(0x80,search_field),0x2a])
    count=_constant(il[0]);_require(0<count<=(32767 if primitive=='i2' else 65535),'COUNT_PRIMITIVE_DOMAIN')
    _require(count<=p.budget.limits.item_count,'COUNT_DOMAIN_BUDGET')
    _require(cap['create']>>24==43,'COUNT_CREATE_METHODSPEC')
    spec,_=p.row(43,cap['create']&0xffffff)
    _require(spec[0]&1==1 and p.meta.blob(spec[1])[0]==b'\x0a\x02\x12'+_compressed_bytes(rid<<2)+bytes((element,)),
             'COUNT_CREATE_TYPE_ARGUMENTS')
    _rid(p,10,spec[0]>>1)
    _member(p,0x0a000000|spec[0]>>1,C(_ID),'Create',M(C(_ID),generic=2))
    # Exact concrete runtime type means GetFields/GetValue dispatches only to
    # trusted runtime reflection, never a user-defined Type/FieldInfo subclass.
    constants={}
    for row in p.table(11):
        if row[1]&3==0: constants.setdefault(row[1]>>2,[]).append(row)
    public_names=set();literal_count=0
    for fid in range(t['firstField'],t['lastField']):
        p.budget.check();f,_=p.row(4,fid);name=p.meta.string(f[1]);sig=p.meta.blob(f[2])[0]
        if f[0]&7!=6: continue
        _require(name not in public_names,'COUNT_DUPLICATE_REFLECTED_FIELD_NAME');public_names.add(name)
        _require(f[0]&0x10,'COUNT_REFLECTED_INSTANCE_FIELD')
        if (0x04000000|fid)==search_field:
            _require(not constants.get(fid),'COUNT_SEARCH_CONSTANT');continue
        _require(sig==bytes((6,element)),'COUNT_REFLECTED_NONPRIMITIVE_FIELD')
        if (0x04000000|fid)==count_field:
            _require(not constants.get(fid),'COUNT_NONLITERAL_HAS_CONSTANT');continue
        rows=constants.get(fid,[])
        _require(f[0]==0x8056 and len(rows)==1 and rows[0][0]==element,'COUNT_REFLECTED_LITERAL_REQUIRED')
        data=p.meta.blob(rows[0][2])[0]
        _require(len(data)==2,'COUNT_LITERAL_PRIMITIVE_WIDTH')
        # Decode as exactly Int16/UInt16, retaining no game names or values.
        struct.unpack('<h' if element==6 else '<H',data)
        literal_count+=1
    _require('Count' in public_names and 'Search' in public_names,'COUNT_REFLECTED_FIELD_COVERAGE')
    # Search is not selected by the exact primitive FieldType predicate. The
    # only selected nonliteral is Count, assigned immediately before Create.
    stack=_stack(p,m)
    return {'owner':owner,'field':'Count','fieldToken':_hex(count_field),'count':count,
       'minInclusive':0,'maxExclusive':count,'primitiveType':'Int16' if element==6 else 'UInt16',
       'factScope':'INITIALIZER_BOUNDARY','wholeInitializerProven':True,
       'independentDomainInitializerProven':True,'externalTailCountNonmutationProven':True,
       'normalReturnGuaranteed':False,'runtimeSnapshotUsable':False,
       'source':'whole readonly Count initializer plus concrete primitive reflection/callback effect proof',
       'countMethodEvidence':{**m['evidence'],**stack},
       'reflectionEvidence':{'publicPrimitiveLiteralFields':literal_count,'selectedNonliteralFields':['Count'],
          'otherPublicFields':['Search'],'uniquePublicCountName':True,'inheritedFields':'none from trusted System.Object',
          'getValueCannotInvokeGameAccessor':True,'conversionCannotInvokeGameIConvertible':True},
       'createMethodSpecToken':_hex(cap['create']),'dependencyGenericCreateToken':dependency_proof.evidence['genericCreateToken']}


def prove_id_count_program(p):
    """Public same-Program composition. Always derives the pinned dependency."""
    digest=sha256(p.meta.reader.data);profile=_PROFILES.get(digest)
    _require(profile is not None,'COUNT_UNSUPPORTED_INPUT_PROFILE')
    role,pins=profile;assembly=_assembly(p.meta)
    _require(assembly['name']==('Terraria' if role=='client' else 'TerrariaServer')
             and assembly['version']=='1.4.5.8','COUNT_SOURCE_ASSEMBLY_IDENTITY')
    embedded=[]
    for row in p.table(40):
        if not p.meta.string(row[2]).endswith('ReLogic.dll'):continue
        _require(not row[3] and p.meta.resources is not None,'COUNT_EMBEDDED_RESOURCE')
        at=p.meta.resources.start+row[0];length=p.meta.resources.uint(at,4)
        _require(0<length<=16*1024*1024,'COUNT_EMBEDDED_RESOURCE_LIMIT')
        embedded.append(p.meta.resources.take(at+4,length))
    _require(len(embedded)==1 and sha256(embedded[0])==_DEP_SHA,'COUNT_DEPENDENCY_BYTE_PIN')
    e=_Program(embedded[0],p.budget);identity=_assembly(e.meta)
    _require(identity['name']=='ReLogic' and identity['version']=='1.0.0.0'
             and not identity['culture'] and not identity['flags'] and not identity['publicKeyToken'],
             'COUNT_DEPENDENCY_ASSEMBLY_IDENTITY')
    dependency=prove_id_dictionary_dependency(e)
    actual={int(m['methodToken'],16):(m['ilSha256'],m['signatureSha256']) for m in dependency.evidence['methodEvidence']}
    _require(actual==_DEP_PINS,'COUNT_DEPENDENCY_METHOD_PIN')
    domains={}
    for name,(token,ilhash,count,literals) in pins.items():
        value=prove_id_count_initializer(p,'Terraria.ID.'+name,dependency);m=value['countMethodEvidence']
        _require(m['methodToken']==_hex(token) and m['ilSha256']==ilhash and m['signatureSha256']==_CCTOR_SHA
                 and value['count']==count and value['reflectionEvidence']['publicPrimitiveLiteralFields']==literals,
                 'COUNT_SOURCE_METHOD_PIN')
        domains[name]=value
    result={'schemaVersion':1,'family':'id-counts','status':'PROVEN_ID_COUNT_INITIALIZER_BOUNDARIES',
       'inputSha256':digest,'sourceRole':role,'gameVersion':'1.4.5.8','domains':domains,
       'modeledReLogicDependency':{'assemblyName':'ReLogic','version':'1.0.0.0','embeddedSha256':_DEP_SHA},
       'runtimeDependencyBindingVerified':False,
       'scope':'declared numeric Count values at independent normal-return initializer boundaries; not valid-item or consumer selection membership',
       'wholeInitializerProven':True,'independentDomainInitializerProven':True,'factScope':'INITIALIZER_BOUNDARY',
       'normalReturnGuaranteed':False,'executedInput':False,'complete':False,'publishable':False,'runtimeSnapshotUsable':False,
       'dependencyEvidence':{**dependency.evidence,'inputSha256':_DEP_SHA,'assembly':identity},
       'preconditions':['modeled ReLogic assembly references bind to the audited embedded ReLogic bytes; actual CLR/AssemblyResolve binding is not proven',
          'normal-returning CLI initialization, including the compiler singleton initializer',
          'no concurrent or external mutation of the Count fields during the initializer observation',
          'no reflection, unsafe, native, runtime instrumentation or external tampering with private delegate caches or fresh objects',
          'trusted CLI/core identities and ordinary allocation, initialization, delegate and primitive semantics',
          'runtime Type/FieldInfo metadata and primitive static-field reads do not invoke game callbacks',
          'LINQ/List enumerate only the proven callbacks; Dictionary string/int default comparers and primitive conversions are trusted'],
       'unsupported':['valid item/material membership or complete consumer selection from the numeric interval alone',
          'runtime success or termination guarantee','lifetime immutability or FINAL runtime snapshot',
          'arbitrary reflection or custom IConvertible/Type/FieldInfo implementations',
          'actual CLR loader/AssemblyResolve binding','cryptographically resolved framework bytes','full source production or publication approval']}
    json_evidence_size(result,p.budget.limits.evidence_bytes,p.budget.check)
    return result


def extract_id_count_semantics(input_path: Path, checkpoint=None):
    """Read fixed client/server PE as data. Unknown profiles fail closed."""
    check=checkpoint or (lambda:None);check()
    limits=ItemTextureAliasLimits(steps=4_000_000,instructions=300_000,method_bytes=1024*1024,
        total_method_bytes=4*1024*1024,evidence_bytes=2*1024*1024,wall_seconds=120)
    raw=read_assembly_bytes(input_path,SemanticLimits(input_bytes=limits.input_bytes),check)
    try:return prove_id_count_program(_Program(raw,_Budget(limits,check)))
    except _CheckpointCancelled as exc:raise exc.original
