"""Finite prefix coefficient proof from pinned IL; no CLR or game execution.

The first nine out cells are initialized using only the prefix ID and literals.
An independent typed CFG check proves the remaining tail cannot write those
cells. Eligibility and price score are deliberately not certified here.
"""
from pathlib import Path
import math

from .item_texture_aliases import _Program, _Budget, ItemTextureAliasLimits, _constant, _require, _CheckpointCancelled
from .server_semantics import read_assembly_bytes, SemanticLimits
from .security import sha256
from .static_il import ILUnsupported, json_evidence_size
from .accessory_prefix_semantics import _instance_field

PROFILES = {
    '960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3': ('client', 0x0600075a, 'e7c949f4e936e0ba41edfc5870c41df249ebe6ae2b0990b16b58a96d2f7208d4'),
    'd87e3faf08637f6be8882c63e7f11fb7e792b0230006309618473ece0f863e1e': ('server', 0x06000293, 'd1c6c6933a04a3eac8e0de203e78cbf4427c5a45adc92b813a2ebfb96ed15373'),
}
SIGNATURE = bytes.fromhex('200b0208100c100c100c100c100c100c100810081008100c')
NAMES = {2: 'dmg', 3: 'kb', 4: 'spd', 5: 'size', 6: 'shtspd', 7: 'mcst', 8: 'crt', 9: 'tagdmg', 10: 'arpen'}


def _arg(instruction):
    if 2 <= instruction.opcode <= 5: return instruction.opcode - 2
    if instruction.opcode in (0x0e, 0xfe09): return instruction.operand
    return None


def _kind(index): return 'F' if index in (*range(2, 8), 11) else 'I4'


def _targets(code):
    result = {i.offset: n for n, i in enumerate(code)}
    _require(len(result) == len(code), 'COEFFICIENT_DUPLICATE_IL_OFFSET')
    return result


def _tail(program, method, start, max_stack):
    """Type-check every reachable tail edge, disallow all writes except ref11."""
    code = method['instructions']; offsets = _targets(code)
    pending, states, fields, calls = [(start, ())], {}, {}, {}
    while pending:
        index, incoming = pending.pop()
        program.budget.check()
        _require(start <= index < len(code), 'COEFFICIENT_TAIL_TARGET')
        if index in states:
            _require(states[index] == incoming, 'COEFFICIENT_TAIL_STACK_MERGE')
            continue
        states[index] = incoming; stack = list(incoming); instruction = code[index]; op = instruction.opcode
        def pop():
            _require(stack, 'COEFFICIENT_TAIL_STACK_UNDERFLOW'); return stack.pop()
        argument = _arg(instruction)
        if argument is not None:
            _require(0 <= argument <= 11, 'COEFFICIENT_ARGUMENT_DOMAIN')
            stack.append(('Item',) if argument == 0 else ('I4',) if argument == 1 else ('ref', argument, _kind(argument)))
        elif op in (*range(0x15, 0x1f), 0x1f, 0x20): stack.append(('I4',))
        elif op in (0x22, 0x23): stack.append(('F',))
        elif op in (0x4a, 0x4e):
            ref = pop(); kind = 'I4' if op == 0x4a else 'F'
            _require(len(ref) == 3 and ref[0] == 'ref' and ref[2] == kind, 'COEFFICIENT_INDIRECT_LOAD_TYPE')
            stack.append((kind,))
        elif op in (0x54, 0x56):
            value, ref = pop(), pop(); kind = 'I4' if op == 0x54 else 'F'
            _require(ref == ('ref', 11, 'F') and kind == 'F' and value == ('F',), 'COEFFICIENT_TAIL_WRITES_SELECTED_OUT')
        elif op == 0x7b:
            _require(pop() == ('Item',), 'COEFFICIENT_TAIL_FIELD_RECEIVER')
            name, sig, evidence = _instance_field(program, instruction.operand, 'Terraria.Item')
            _require((name in ('damage', 'useAnimation', 'mana') and sig == b'\x06\x08')
                     or (name == 'knockBack' and sig == b'\x06\x0c'), 'COEFFICIENT_TAIL_FIELD')
            fields[instruction.operand] = evidence; stack.append(('F' if name == 'knockBack' else 'I4',))
        elif op in (0x6b, 0x6c):
            _require(pop() in (('I4',), ('F',)), 'COEFFICIENT_CONVERSION_TYPE'); stack.append(('F',))
        elif op in (0x58, 0x59, 0x5a):
            right, left = pop(), pop()
            _require(right == left and left in (('I4',), ('F',)), 'COEFFICIENT_ARITHMETIC_TYPE'); stack.append(left)
        elif op == 0x28:
            program.member(instruction.operand, 'System.Math', 'Round', b'\x00\x01\x0d\x0d')
            _require(pop() == ('F',), 'COEFFICIENT_ROUND_ARGUMENT'); stack.append(('F',))
            calls[instruction.operand] = 'trusted-core-System.Math.Round(double), no ref arguments'
        elif op in (0x2e, 0x33, 0x3b, 0x40):
            a, b = pop(), pop()
            _require(a == b and a in (('I4',), ('F',)), 'COEFFICIENT_BRANCH_COMPARE_TYPE')
            target = offsets.get(instruction.operand)
            _require(target is not None and target > index, 'COEFFICIENT_TAIL_FORWARD_BRANCH')
            pending.append((target, tuple(stack)))
        elif op in (0x2b, 0x38):
            target = offsets.get(instruction.operand)
            _require(target is not None and target > index, 'COEFFICIENT_TAIL_FORWARD_JUMP')
            pending.append((target, tuple(stack))); continue
        elif op == 0x2a:
            _require(pop() == ('I4',) and not stack, 'COEFFICIENT_BOOL_RETURN_STACK'); continue
        else: raise ILUnsupported('COEFFICIENT_UNSUPPORTED_TAIL_OPCODE')
        _require(len(stack) <= max_stack, 'COEFFICIENT_DECLARED_MAXSTACK')
        _require(index + 1 < len(code), 'COEFFICIENT_TAIL_FALLTHROUGH')
        pending.append((index + 1, tuple(stack)))
    return {'selectedOutCellsPreserved': True, 'checkedTailInstructions': len(states),
            'fieldEvidence': list(fields.values()), 'callBindings': {f'0x{k:08x}': v for k, v in calls.items()},
            'eligibilityResultProven': False, 'priceMultiplierProven': False}


def _evaluate_dispatch(program, code, stop, prefix, max_stack):
    offsets = _targets(code); stack, cells, index, trace = [], {}, 0, []
    while index < stop:
        program.budget.check(); instruction = code[index]; op = instruction.opcode
        trace.append(instruction.offset)
        def pop():
            _require(stack, 'COEFFICIENT_DISPATCH_STACK_UNDERFLOW'); return stack.pop()
        argument = _arg(instruction)
        if argument is not None:
            _require(1 <= argument <= 10, 'COEFFICIENT_DISPATCH_ARGUMENT')
            stack.append(('I4', prefix) if argument == 1 else ('ref', argument, _kind(argument)))
        elif op in (*range(0x15, 0x1f), 0x1f, 0x20): stack.append(('I4', _constant(instruction)))
        elif op == 0x22:
            _require(type(instruction.operand) is float and math.isfinite(instruction.operand), 'COEFFICIENT_NONFINITE_LITERAL')
            stack.append(('F', instruction.operand))
        elif op in (0x54, 0x56):
            value, ref = pop(), pop(); kind = 'I4' if op == 0x54 else 'F'
            _require(len(ref) == 3 and ref[0] == 'ref' and 2 <= ref[1] <= 10
                     and ref[2] == kind and value[0] == kind, 'COEFFICIENT_DISPATCH_STORE_TYPE')
            cells[ref[1]] = value[1]
        elif op in (0x2e, 0x33, 0x3b, 0x40):
            a, b = pop(), pop()
            _require(a[0] == b[0] == 'I4', 'COEFFICIENT_PREFIX_ONLY_BRANCH')
            target = offsets.get(instruction.operand)
            _require(target is not None and index < target <= stop, 'COEFFICIENT_DISPATCH_BRANCH_TARGET')
            equal = a[1] == b[1]
            if equal == (op in (0x2e, 0x3b)):
                index = target; continue
        elif op in (0x2b, 0x38):
            target = offsets.get(instruction.operand)
            _require(target is not None and index < target <= stop, 'COEFFICIENT_DISPATCH_JUMP_TARGET')
            index = target; continue
        else: raise ILUnsupported('COEFFICIENT_UNSUPPORTED_DISPATCH_OPCODE')
        _require(len(stack) <= max_stack, 'COEFFICIENT_DECLARED_MAXSTACK')
        index += 1
    _require(index == stop and not stack and set(cells) == set(NAMES), 'COEFFICIENT_TOTAL_OUT_INITIALIZATION')
    return cells, trace


def _prove(program, method, prefix_count):
    _require(method['signature'] == SIGNATURE and not method['local'] and not method['eh'], 'COEFFICIENT_METHOD_CONTRACT')
    _require(type(prefix_count) is int and 1 <= prefix_count <= 256, 'COEFFICIENT_PREFIX_COUNT')
    maximum = 8 if method['header'] == 1 else program.meta.reader.uint(method['evidence']['bodyOffset'] + 2, 2)
    code = method['instructions']; starts = [n for n, i in enumerate(code) if _arg(i) == 11]
    _require(starts, 'COEFFICIENT_MISSING_PRICE_TAIL')
    stop = starts[0]
    tail = _tail(program, method, stop, maximum)
    records = []
    for prefix in range(prefix_count):
        cells, trace = _evaluate_dispatch(program, code, stop, prefix, maximum)
        records.append({'prefixId': prefix, 'coefficients': {NAMES[n]: cells[n] for n in NAMES},
                        'dispatchIlOffsets': trace})
    return {'coefficientModelComplete': True, 'prefixCount': prefix_count,
            'methodEvidence': {**method['evidence'], 'declaredMaxStack': maximum},
            'coefficientBoundaryIlOffset': code[stop].offset, 'tail': tail, 'records': records,
            'preconditions': ['nonnull well-typed Item receiver', 'eleven-parameter method has distinct byref out cells',
                              'out cells do not alias receiver storage; no concurrent mutation',
                              'method-body normal-return boundary excludes type initialization and caller behavior'],
            'scope': 'dmg/kb/spd/size/shtspd/mcst/crt/tagdmg/arpen final out values only'}


def extract_prefix_coefficient_semantics(input_path: Path, checkpoint=None):
    check = checkpoint or (lambda: None); check()
    raw = read_assembly_bytes(input_path, SemanticLimits(), check); digest = sha256(raw)
    profile = PROFILES.get(digest); _require(profile is not None, 'COEFFICIENT_UNSUPPORTED_INPUT_PROFILE')
    try:
        limits = ItemTextureAliasLimits(method_bytes=8192, total_method_bytes=16384, instructions=16384,
                                        steps=2000000, evidence_bytes=4 * 1024 * 1024)
        p = _Program(raw, _Budget(limits, check))
        count_method = p.method('Terraria.ID.PrefixID', '.cctor')
        il = count_method['instructions']; count_field = p.field('Terraria.ID.PrefixID', 'Count', b'\x06\x08')
        _require(count_method['signature'] == b'\x00\x00\x01' and not count_method['local'] and len(il) == 3
                 and il[1].opcode == 0x80 and il[1].operand == count_field and il[2].opcode == 0x2a, 'COEFFICIENT_COUNT_INITIALIZER')
        max_count_stack = 8 if count_method['header'] == 1 else p.meta.reader.uint(count_method['evidence']['bodyOffset'] + 2, 2)
        _require(max_count_stack >= 1, 'COEFFICIENT_COUNT_MAXSTACK')
        count = _constant(il[0]); method = p.method('Terraria.Item', 'TryGetPrefixStatMultipliersForItem')
        _require(method['token'] == profile[1] and method['evidence']['ilSha256'] == profile[2]
                 and method['evidence']['signatureSha256'] == 'a5f97ad176107e41d661b393a901d4becb6751927e8312e6d50c8288144152c9',
                 'COEFFICIENT_METHOD_TOKEN_OR_HASH')
        result = _prove(p, method, count)
        result.update(schemaVersion=1, family='prefix-coefficients', status='PROVEN_FINITE_OUT_TRANSFORM',
                      sourceRole=profile[0], inputSha256=digest, gameVersion='1.4.5.8', countEvidence=count_method['evidence'],
                      executedInput=False, complete=False, publishable=False, runtimeSnapshotUsable=False,
                      unsupported=['prefix eligibility and price score', 'accessory additive benefits',
                                   'final item defaults', 'full item consumer group and publication'])
        json_evidence_size(result, limits.evidence_bytes, check)
        return result
    except _CheckpointCancelled as exc: raise exc.original
