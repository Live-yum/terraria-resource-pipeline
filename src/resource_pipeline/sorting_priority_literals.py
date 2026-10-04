"""Pinned data-only provenance for ItemID.Sets priority call-site overrides.

These are literal CreateIntSet arguments, not a final runtime array or a proof
of the whole initializer. The runtime adapter separately requires final arrays
to agree with this explicit default/override provenance.
"""
from pathlib import Path
from .item_texture_aliases import (_Program, _Budget, ItemTextureAliasLimits, _constant,
    _require, _linear_store, _rva_pairs, _initialize_array, _factory, _compressed_bytes)
from .security import PipelineError, sha256
from .server_semantics import SemanticLimits, read_assembly_bytes

SOURCES = {'960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3': 'client',
           'd87e3faf08637f6be8882c63e7f11fb7e792b0230006309618473ece0f863e1e': 'server'}
OWNER = 'Terraria.ID.ItemID+Sets'


def _literal_array(p, part):
    """Decode either an exact RVA-initialized or explicit two-element int array."""
    _require(len(part) in (9, 14), 'PRIORITY_LITERAL_GRAMMAR')
    elements = _constant(part[2])
    _require(part[3].opcode == 0x8d, 'PRIORITY_INT_ARRAY')
    p.external_type(part[3].operand, 'System.Int32')
    if len(part) == 9:
        _require([i.opcode for i in part[4:7]] == [0x25, 0xd0, 0x28], 'PRIORITY_RVA_GRAMMAR')
        _initialize_array(p, part[6].operand)
        return _rva_pairs(p, part[5].operand, elements)
    _require(elements == 2, 'PRIORITY_INLINE_COUNT')
    values = []
    for index, start in enumerate((4, 8)):
        q = part[start:start + 4]
        _require(q[0].opcode == 0x25 and _constant(q[1]) == index and q[3].opcode == 0x9e,
                 'PRIORITY_INLINE_ASSIGNMENT')
        values.append(_constant(q[2]))
    return [(values[0], values[1])], {'kind': 'inline-int32-pair'}


def extract_sorting_priority_literals(input_path: Path, *, checkpoint=None):
    checkpoint = checkpoint or (lambda: None)
    raw = read_assembly_bytes(Path(input_path), SemanticLimits(), checkpoint)
    digest = sha256(raw)
    if digest not in SOURCES: raise PipelineError('Unsupported priority source hash')
    p = _Program(raw, _Budget(ItemTextureAliasLimits(total_method_bytes=8 * 1024 * 1024,
                                                   instructions=2000000, wall_seconds=120), checkpoint))
    _, owner = p.owner(OWNER)
    names = [p.meta.string(p.row(4, rid)[0][1]) for rid in range(owner['firstField'], owner['lastField'])
             if p.meta.string(p.row(4, rid)[0][1]).startswith('SortingPriority')]
    _require(0 < len(names) <= 64 and len(set(names)) == len(names), 'PRIORITY_FIELD_DOMAIN')
    factory_rid, _ = p.owner('Terraria.ID.SetFactory')
    factory = p.field(OWNER, 'Factory', b'\x06\x12' + _compressed_bytes(factory_rid << 2))
    method = p.method(OWNER, '.cctor', b'\x00\x00\x01')
    result, evidence = {}, {}
    for name in sorted(names):
        p.budget.check()
        field = p.field(OWNER, name, b'\x06\x1d\x08')
        _require(not method['eh'], 'PRIORITY_EXCEPTION_REGION')
        # Single direct store and no branching/address escape in this method.
        end = _linear_store(method, field, 0)[0]
        at = next(n for n, ins in enumerate(method['instructions']) if ins.offset == end.offset)
        start = at - 1
        while start >= 0 and method['instructions'][start].opcode != 0x80: start -= 1
        part = method['instructions'][start + 1:at + 1]
        _require(len(part) in (9, 14) and part[0].opcode == 0x7e and part[0].operand == factory
                 and _constant(part[1]) == -1 and part[-2].opcode == 0x6f,
                 'PRIORITY_DEFAULT_OR_FACTORY_GRAMMAR')
        maxstack = 8 if method['header'] == 1 else p.meta.reader.uint(method['evidence']['bodyOffset'] + 2, 2)
        _require(maxstack >= (6 if len(part) == 14 else 5), 'PRIORITY_MAXSTACK_TOO_SMALL')
        pairs, literal = _literal_array(p, part)
        _factory(p, part[-2].operand)
        _require(all(0 <= n < 65536 for n, value in pairs), 'PRIORITY_LITERAL_ID_LIMIT')
        result[name] = {'default': -1, 'overrides': [list(pair) for pair in pairs]}
        evidence[name] = {'fieldToken': hex(field), 'callIlOffset': part[-2].offset,
                          'storeIlOffset': end.offset, 'declaredMaxStack': maxstack, 'literal': literal}
    return {'schemaVersion': 1, 'status': 'LITERAL_CALLSITE_PROVENANCE', 'sourceRole': SOURCES[digest],
            'inputSha256': digest, 'priorityDomain': sorted(names), 'priorities': result,
            'initializer': method['evidence'], 'fields': evidence, 'executedInput': False,
            'preconditions': ['selected initializer store reached normally',
                              'no external mutation of literal RVA storage before copying'],
            'finalRuntimeValuesProven': False, 'wholeInitializerProven': False,
            'complete': False, 'publishable': False}
