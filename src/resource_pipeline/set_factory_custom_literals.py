"""Exact primitive/Nullable literal custom sets; no general conversion engine.

All paths are shape-bound, but only primitive/Nullable branches are assigned
semantics. Convert.ChangeType, class and arbitrary struct/enum instantiations
remain unsupported. Input assemblies are data and are never executed.
"""
import hashlib

from .item_texture_aliases import _require, _shape, _hex, _constant, _compressed_bytes
from .set_factory_empty_custom import prove_empty_custom_set
from .static_il import _compressed, _type


# Restrict literals to CLI int32-stack integral values with an exact boxed type.
INTEGRAL = {
    2: ('Boolean', 0, 1), 3: ('Char', 0, 65535),
    4: ('SByte', -128, 127), 5: ('Byte', 0, 255),
    6: ('Int16', -32768, 32767), 7: ('UInt16', 0, 65535),
    8: ('Int32', -2147483648, 2147483647),
}


def _pattern():
    """Labels bind branch targets to instructions, independent of byte offsets."""
    out = []; labels = {}
    def add(*items): out.extend(items)
    def mark(name): labels[name] = len(out)
    add(4,0x8e,0x69,0x18,0x5d,(0x2c,'#allocate'),(0x72,'error'),(0x73,'throwctor'),0x7a)
    mark('allocate'); add(2,(0x7b,'size'),(0x8d,'element'),0x0a,0x16,0x0b,(0x2b,'#fillcheck'))
    mark('fill'); add(6,7,3,(0xa4,'element'),7,0x17,0x58,0x0b)
    mark('fillcheck'); add(7,6,0x8e,0x69,(0x32,'#fill'),4,(0x39,'#return'),0x16,0x0c,(0x38,'#check'))
    mark('pair'); add((0xd0,'element'),(0x28,'typeof'),(0x28,'primitive'),(0x2c,'#generic'),
                     4,8,0x17,0x58,0x9a,(0xa5,'element'),0x0d,(0x2b,'#key'))
    mark('generic'); add((0xd0,'element'),(0x28,'typeof'),(0x6f,'generic'),(0x2c,'#class'),
                        (0xd0,'element'),(0x28,'typeof'),(0x6f,'definition'),(0xd0,'nullable'),
                        (0x28,'typeof'),(0x28,'equality'),(0x2c,'#class'),
                        4,8,0x17,0x58,0x9a,(0xa5,'element'),0x0d,(0x2b,'#key'))
    mark('class'); add((0xd0,'element'),(0x28,'typeof'),(0x28,'class'),(0x2c,'#convert'),
                      4,8,0x17,0x58,0x9a,(0xa5,'element'),0x0d,(0x2b,'#key'))
    mark('convert'); add(4,8,0x17,0x58,0x9a,(0xd0,'element'),(0x28,'typeof'),
                        (0x28,'convert'),(0xa5,'element'),0x0d)
    mark('key'); add(4,8,0x9a,(0x75,'ushort'),(0x2c,'#intkey'),6,4,8,0x9a,(0xa5,'ushort'),
                    9,(0xa4,'element'),(0x2b,'#next'))
    mark('intkey'); add(4,8,0x9a,(0x75,'int'),(0x2c,'#shortkey'),6,4,8,0x9a,(0xa5,'int'),
                       9,(0xa4,'element'),(0x2b,'#next'))
    mark('shortkey'); add(6,4,8,0x9a,(0xa5,'short'),9,(0xa4,'element'))
    mark('next'); add(8,0x18,0x58,0x0c)
    mark('check'); add(8,4,0x8e,0x69,(0x3f,'#pair'))
    mark('return'); add(6,0x2a)
    return [(v[0], '@'+str(labels[v[1][1:]])) if isinstance(v,tuple) and isinstance(v[1],str)
            and v[1].startswith('#') else v for v in out]


def _type_ref_signature(p, signature, at, kind, name):
    _require(at < len(signature) and signature[at] == kind, 'CUSTOM_CORE_SIGNATURE')
    coded, end = _compressed(signature, at+1)
    _require(coded & 3 == 1, 'CUSTOM_CORE_SIGNATURE')
    p.external_type(0x01000000 | coded >> 2, name)
    return signature[at:end], end


def prove_literal_custom_method(p):
    empty = prove_empty_custom_set(p)
    method = p.body(int(empty['methodToken'],16))
    cap = _shape(method['instructions'], _pattern())
    params = [r for r in p.table(42) if r[2] == ((method['token'] & 0xffffff) << 1 | 1)]
    _require(len(params) == 1 and params[0][:2] == (0,0), 'CUSTOM_GENERIC_CONSTRAINT')
    # Derive exact Type/RuntimeTypeHandle coded references from the signature,
    # validate their core identities, then bind every reflection member exactly.
    row, _ = p.row(10, cap['typeof'] & 0xffffff)
    sig = p.meta.blob(row[2])[0]
    _require(sig[:3] == b'\x00\x01\x12', 'CUSTOM_TYPEOF_SIGNATURE')
    type_sig, end = _type_ref_signature(p,sig,2,0x12,'System.Type')
    _, end = _type_ref_signature(p,sig,end,0x11,'System.RuntimeTypeHandle')
    _require(end == len(sig), 'CUSTOM_TYPEOF_SIGNATURE')
    for token, owner, name, expected in (
        (cap['typeof'],'System.Type','GetTypeFromHandle',sig),
        (cap['primitive'],'System.Type','get_IsPrimitive',b'\x20\x00\x02'),
        (cap['generic'],'System.Type','get_IsGenericType',b'\x20\x00\x02'),
        (cap['definition'],'System.Type','GetGenericTypeDefinition',b'\x20\x00'+type_sig),
        (cap['equality'],'System.Type','op_Equality',b'\x00\x02\x02'+type_sig*2),
        (cap['class'],'System.Type','get_IsClass',b'\x20\x00\x02'),
        (cap['convert'],'System.Convert','ChangeType',b'\x00\x02\x1c\x1c'+type_sig),
    ):
        p.member(token,owner,name,expected)
    p.external_type(cap['nullable'],'System.Nullable`1')
    for name, core in [('ushort','UInt16'),('int','Int32'),('short','Int16')]:
        p.external_type(cap[name],'System.'+core)
    return {'methodToken': method['token'], 'sizeFieldToken': empty['sizeFieldToken'],
            'nullableToken': cap['nullable'], 'methodEvidence': method['evidence'],
            'conversionSemantics': 'exact primitive unbox or nullable unbox only; ChangeType path unreachable'}


def _instantiation(p, token, summary):
    _require(token >> 24 == 43, 'CUSTOM_EXPECTED_METHODSPEC')
    row, offset = p.row(43,token & 0xffffff)
    _require(row[0] == (summary['methodToken'] & 0xffffff) << 1, 'CUSTOM_METHODSPEC_TARGET')
    sig, sigoffset = p.meta.blob(row[1])
    _require(sig[:2] == b'\x0a\x01', 'CUSTOM_METHODSPEC_SIGNATURE')
    typ = sig[2:]; nullable = False
    if typ[:2] == b'\x15\x11':
        coded, end = _compressed(typ,2)
        _require(coded == ((summary['nullableToken'] & 0xffffff) << 2 | 1)
                 and typ[end:end+1] == b'\x01', 'CUSTOM_NULLABLE_IDENTITY')
        primitive = typ[end+1:]; nullable = True
    else:
        primitive = typ
    _require(len(primitive) == 1 and primitive[0] in INTEGRAL, 'CUSTOM_UNSUPPORTED_INSTANTIATION')
    return typ, primitive[0], nullable, {'methodSpecToken': _hex(token),'metadataOffset': offset,
        'signatureOffset': sigoffset,'signatureSha256': hashlib.sha256(sig).hexdigest()}


def _literal(ins, kind):
    value = _constant(ins); _, lower, upper = INTEGRAL[kind]
    _require(lower <= value <= upper, 'CUSTOM_LITERAL_RANGE')
    return value


def _default(p,cctor,il,at,typ,kind,nullable):
    if not nullable:
        return at+1, _literal(il[at],kind)
    _require(il[at].opcode == 0x12, 'CUSTOM_DEFAULT_INITOBJ')
    index = il[at].operand
    count, pos = _compressed(cctor['local'],1)
    _require(index < count, 'CUSTOM_DEFAULT_LOCAL')
    for n in range(count):
        start = pos; _, pos = _type(cctor['local'],pos)
        if n == index: _require(cctor['local'][start:pos] == typ,'CUSTOM_DEFAULT_LOCAL')
    _require(il[at+1].opcode == 0xfe15 and p.type_signature(il[at+1].operand) == typ,
             'CUSTOM_DEFAULT_INITOBJ')
    load = il[at+2]
    _require((index < 4 and load.opcode == 6+index and load.operand is None)
             or (load.opcode == 0x11 and load.operand == index),'CUSTOM_DEFAULT_LOCAL_LOAD')
    return at+3, None


def literal_custom_slice(p,cctor,at,constructor,summary):
    """Caller is straight-line; factory load is already bound by the lifecycle."""
    from .set_factory_lifecycle import _store
    il = cctor['instructions']; start = at; at += 1
    # Find only the first call boundary. No call/default constructor may be
    # skipped while looking for a MethodSpec. Work is instruction-budget bounded.
    end = at
    while end < len(il) and il[end].opcode not in (0x28,0x6f,0x73,0x80,0x2a):
        p.budget.check(); end += 1
    _require(end+1 < len(il) and il[end].opcode == 0x6f and il[end+1].opcode == 0x80,
             'CUSTOM_CALL_STORE')
    typ, kind, nullable, spec_evidence = _instantiation(p,il[end].operand,summary)
    _require(summary['sizeFieldToken'] == constructor['sizeFieldToken'],'CUSTOM_SIZE_BINDING')
    at, default = _default(p,cctor,il,at,typ,kind,nullable)
    count = _constant(il[at]); allocation = il[at+1]
    _require(0 <= count <= p.budget.limits.literal_ids and count % 2 == 0
             and count * 16 <= p.budget.limits.literal_bytes,'CUSTOM_PAIR_LIMIT')
    _require(allocation.opcode == 0x8d,'CUSTOM_PAIR_ARRAY')
    p.external_type(allocation.operand,'System.Object'); at += 2
    values = {}; types = {}
    while at < end and il[at].opcode == 0x25:
        p.budget.check()
        _require(at+4 < end, 'CUSTOM_PAIR_TRUNCATED')
        index = _constant(il[at+1])
        _require(0 <= index < count,'CUSTOM_PAIR_INDEX')
        _shape(il[at:at+5],[0x25,(il[at+1].opcode,il[at+1].operand),(il[at+2].opcode,il[at+2].operand),
                              (0x8c,'box'),0xa2])
        token = il[at+3].operand
        # Do not infer a boxed kind from the stack value or an untrusted name.
        row, _ = p.row(1,token & 0xffffff)
        name = p.meta.string(row[1])
        matches = [k for k,(n,_,_) in INTEGRAL.items() if n == name]
        _require(len(matches) == 1,'CUSTOM_BOX_TYPE')
        boxkind = matches[0]; p.external_type(token,'System.'+INTEGRAL[boxkind][0])
        values[index] = _literal(il[at+2],boxkind); types[index] = boxkind; at += 5
    _require(at == end and len(values) == count,'CUSTOM_PAIR_COVERAGE')
    overrides = []
    for n in range(0,count,2):
        p.budget.check()
        _require(types[n] in (6,7,8) and values[n] >= 0,'CUSTOM_KEY_TYPE_OR_RANGE')
        _require(types[n+1] == kind,'CUSTOM_PAYLOAD_UNBOX_TYPE')
        overrides.append([values[n],values[n+1]])
    _store(p,il[end+1].operand,b'\x06\x1d'+typ)
    return end+2,[il[end]],{'kind':'fresh custom literal array',
        'startIlOffset':il[start].offset,'storeIlOffset':il[end+1].offset,
        'fieldToken':_hex(il[end+1].operand),'elementSignature':typ.hex(),
        'elementType': ('Nullable<' if nullable else '')+INTEGRAL[kind][0]+('>' if nullable else ''),
        'defaultValue':default,'orderedOverrides':overrides,
        'methodSpecEvidence':spec_evidence,'genericInstantiationValidityProven':True,
        'allocationIdentity':{'callerIlOffset':il[end].offset,'callee':_hex(il[end].operand)},
        'argumentArray':{'allocationIlOffset':allocation.offset,'length':count,'fullyInitialized':True},
        'size':'captured initial Count read','numericSizeProven':False,
        'factoryOrPoolWrites':False,'argumentArrayEscapes':False,
        'normalReturnImplies':'all override indexes are smaller than the captured array size',
        'conversionSemantics':summary['conversionSemantics']}
