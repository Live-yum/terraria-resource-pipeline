"""Fresh Item primitive baseline evidence, from bytes only; never final defaults.

This API deliberately does not participate in the producer. It reuses the
bounded numeric interpreter, adding only fresh zero storage, a resolved Object
base constructor, immediately consumed opaque field addresses, null stores and
verified direct null setters. Static reads require a whole closed initializer;
an initializer prefix is diagnostic evidence, never a scalar snapshot.
"""
from __future__ import annotations

import hashlib
import time

from .security import PipelineError
from .static_il import (
    AbstractEvaluator, EvidenceBudget, ILUnsupported, MetadataProgram,
    StaticILLimits, THIS, UNKNOWN, Value, _KINDS, _INTEGRAL_KINDS,
    _PURE_INTEGER_OPS, _compressed, _storage, _typed_value, json_evidence_size,
)


_PRIMITIVES = _INTEGRAL_KINDS | {'r4', 'r8'}
_CORE_KEYS = {'mscorlib': bytes.fromhex('b77a5c561934e089'),
              'System.Private.CoreLib': bytes.fromhex('7cec85d7bea7798e'),
              'System.Runtime': bytes.fromhex('b03f5f7f11d50a3a')}


def _hex(token):
    return f'0x{token:08x}'


def _coded(token):
    tag = {2: 0, 1: 1, 27: 2}.get(token >> 24)
    if tag is None or not token & 0xffffff:
        raise ILUnsupported('INVALID_BASELINE_TYPE_TOKEN', token=token)
    return (token & 0xffffff) << 2 | tag


def _compress(value):
    if value < 0x80:
        return bytes((value,))
    if value < 0x4000:
        return bytes((0x80 | value >> 8, value & 255))
    if value < 0x20000000:
        return bytes((0xc0 | value >> 24, value >> 16 & 255, value >> 8 & 255, value & 255))
    raise ILUnsupported('INVALID_SIGNATURE_INTEGER')


class BaselineProgram(MetadataProgram):
    """Stricter metadata contract than stage-only field-write evaluation."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.item_methods = frozenset(self.method_rows)
        self.signatures = {}
        self.storage = {}
        self.setters = {}
        self.static_fields = {}
        self.static_owners = None
        self.initializer_cache = {}
        self.initializer_closed = {}
        self.unclosed_initializers = set()
        self.initializer_records = []
        self.active_initializers = []
        self.base_intrinsics = {}
        self._scan()
        row, offset = self.meta.row(2, self.type_rid)
        # No inherited instance storage, generic Item, interfaces or aliases.
        if row[0] & 0x20 or row[0] & 0x18:
            raise ILUnsupported('UNSUPPORTED_BASELINE_ITEM_LAYOUT')
        self.object_type = row[3]
        base = self._core_type(self.object_type, 'Object')
        if base is None:
            raise ILUnsupported('BASELINE_REQUIRES_CORE_OBJECT_BASE')
        if any(r[2] == self.type_rid << 1 for r in self._table(42)):
            raise ILUnsupported('UNSUPPORTED_GENERIC_BASELINE_ITEM')
        if any(r[2] == self.type_rid for r in self._table(15)) or any(
                self.item['firstField'] <= r[1] < self.item['lastField'] for r in self._table(16)):
            raise ILUnsupported('UNSUPPORTED_BASELINE_ITEM_LAYOUT')
        self.type_evidence = {'typeToken': _hex(0x02000000 | self.type_rid),
                              'typeMetadataOffset': offset, 'base': base,
                              'layout': 'auto; no ClassLayout or FieldLayout entries'}
        self.evidence_budget.charge(self.type_evidence)
        for token, field in self.fields.items():
            self._scan()
            values, _ = self.meta.row(4, token & 0xffffff)
            signature, _ = self.meta.blob(values[2])
            if (len(signature) > 64 or not signature or signature[0] != 6
                    or values[0] & (0x40 | 0x100 | 0x2000)):
                raise ILUnsupported('UNSUPPORTED_BASELINE_FIELD_SIGNATURE', token=token)
            storage, kind = self._field_type(signature[1:])
            if field['owner'] != self.item['fullName']:
                raise ILUnsupported('BASELINE_REQUIRES_CORE_OBJECT_BASE')
            # Primitive zero seeding uses exact bare signatures, never a
            # modifier, enum, pointer, native int or unverified value layout.
            field['kind'] = kind
            field['flags'] = values[0]
            self.signatures[token] = signature[1:]
            self.storage[token] = storage
            self.evidence_budget.charge({'token': _hex(token), 'signature': signature.hex(), 'storage': storage})
        constructors = []
        for token in self.by_name.get('.ctor', []):
            row, _, _ = self.method_rows[token]
            signature, _ = self.meta.blob(row[4])
            if signature == b'\x20\x00\x01':
                constructors.append(token)
        if len(constructors) != 1:
            raise ILUnsupported('BASELINE_PARAMETERLESS_CONSTRUCTOR_MISSING_OR_AMBIGUOUS')
        self.constructor = constructors[0]
        resets = self.by_name.get('ResetStats', [])
        if len(resets) != 1:
            raise ILUnsupported('BASELINE_RESET_MISSING_OR_OVERLOADED')
        self.reset = resets[0]
        for token, signature in ((self.constructor, b'\x20\x00\x01'),
                                 (self.reset, b'\x20\x01\x01\x08')):
            row, _, _ = self.method_rows[token]
            actual, _ = self.meta.blob(row[4])
            allowed_flags = 0x1887 if token == self.constructor else 0x87
            if (actual != signature or row[1] or row[2] & ~allowed_flags or not 1 <= row[2] & 7 <= 6
                    or any(r[2] == ((token & 0xffffff) << 1 | 1) for r in self._table(42))):
                raise ILUnsupported('INVALID_BASELINE_ENTRY_SIGNATURE', token=token)
        ctor_row = self.method_rows[self.constructor][0]
        if ctor_row[2] & 0x1800 != 0x1800:
            raise ILUnsupported('INVALID_BASELINE_CONSTRUCTOR_FLAGS', token=self.constructor)

    def _core_type(self, coded, name):
        if coded & 3 != 1 or not coded >> 2:
            return None
        self._scan()
        row, offset = self.meta.row(1, coded >> 2)
        actual_name, namespace = self.meta.string(row[1]), self.meta.string(row[2])
        self._name(actual_name); self._name(namespace)
        if (namespace, actual_name) != ('System', name) or row[0] & 3 != 2 or not row[0] >> 2:
            return None
        self._scan()
        assembly, assembly_offset = self.meta.row(35, row[0] >> 2)
        assembly_name, culture = self.meta.string(assembly[6]), self.meta.string(assembly[7])
        self._name(assembly_name); self._name(culture)
        key, _ = self.meta.blob(assembly[5])
        if assembly[4] or culture or key != _CORE_KEYS.get(assembly_name):
            return None
        return {'typeRefToken': _hex(0x01000000 | coded >> 2), 'typeRefMetadataOffset': offset,
                'assemblyRefMetadataOffset': assembly_offset, 'assemblyName': assembly_name,
                'typeName': 'System.' + name, 'identityPolicy': 'external core-library reference and public-key token; not signature verification'}

    def _field_type(self, signature):
        if len(signature) == 1 and _KINDS.get(signature[0]) in _PRIMITIVES:
            return 'primitive', _KINDS[signature[0]]
        if signature in (b'\x0e', b'\x1c'):
            return 'reference', 'other'
        if signature and signature[0] in (0x11, 0x12):
            coded, end = _compressed(signature, 1)
            if end == len(signature) and coded >> 2 and coded & 3 in (0, 1):
                self._scan(); self.meta.row(2 if coded & 3 == 0 else 1, coded >> 2)
                return ('opaque-value' if signature[0] == 0x11 else 'reference'), 'other'
        if signature[:2] == b'\x15\x11':
            coded, pos = _compressed(signature, 2)
            count, pos = _compressed(signature, pos)
            if count == 1 and len(signature) == pos + 1 and _KINDS.get(signature[pos]) in _PRIMITIVES:
                if self._core_type(coded, 'Nullable`1') is not None:
                    return 'opaque-value', 'other'
        raise ILUnsupported('UNSUPPORTED_BASELINE_FIELD_SIGNATURE')

    def _signature_supported(self, token, instance, returned, args, signature):
        if super()._signature_supported(token, instance, returned, args, signature):
            return True
        # The body is validated after bounded decoding, before any use. No
        # nonnumeric method is evaluated by the generic numeric interpreter.
        if token not in self.method_rows or not instance or returned != 'void' or args != ('other',):
            return False
        if signature[:3] != b'\x20\x01\x01':
            return False
        return any(self.storage[t] == 'reference' and signature[3:] == sig for t, sig in self.signatures.items())

    def get(self, token):
        # A foreign static helper could trigger another type initializer even
        # when its own body is pure. This baseline does not assume that effect
        # away; only Item-owned helpers are admitted here. The separate stage
        # API retains its existing, explicitly weaker initialization contract.
        if token not in self.item_methods:
            raise ILUnsupported('UNSUPPORTED_BASELINE_HELPER_OWNER', token=token)
        method = super().get(token)
        self._body_contract(method)
        if any(r[2] == ((token & 0xffffff) << 1 | 1) for r in self._table(42)):
            raise ILUnsupported('UNSUPPORTED_GENERIC_BASELINE_METHOD', token=token)
        if token in (self.constructor, self.reset):
            return method
        if method.args == ('other',) and method.instance and method.returned == 'void':
            body = list(method.instructions.values())
            if len(body) != 4 or [i.opcode for i in body] != [2, 3, 0x7d, 0x2a]:
                raise ILUnsupported('UNSUPPORTED_BASELINE_REFERENCE_SETTER', token=token)
            field = body[2].operand
            row, _, _ = self.method_rows[token]
            signature, _ = self.meta.blob(row[4])
            if (self.storage.get(field) != 'reference' or signature[3:] != self.signatures[field]
                    or self.fields[field]['flags'] & 0x20 or row[1] or row[2] & ~0x887
                    or not 1 <= row[2] & 7 <= 6):
                raise ILUnsupported('UNSUPPORTED_BASELINE_REFERENCE_SETTER', token=token)
            self.setters[token] = field
            return method
        # Baseline helpers have no state, calls, object access or float math.
        if (method.instance or method.returned not in _INTEGRAL_KINDS or
                any(k not in _INTEGRAL_KINDS for k in method.args) or method.name.startswith('.')):
            raise ILUnsupported('UNSUPPORTED_BASELINE_HELPER', token=token)
        row, _, _ = self.method_rows[token]
        if row[1] & ~0x100 or row[2] & ~0x97 or not 1 <= row[2] & 7 <= 6:
            raise ILUnsupported('UNSUPPORTED_BASELINE_HELPER', token=token)
        for instruction in method.instructions.values():
            self.checkpoint()
            if instruction.opcode not in _PURE_INTEGER_OPS:
                raise ILUnsupported('UNSUPPORTED_BASELINE_HELPER', token=token, offset=instruction.offset)
        return method

    def _body_contract(self, method):
        first = self.meta.reader.uint(method.evidence['bodyOffset'], 1)
        if first & 3 == 3:
            flags = self.meta.reader.uint(method.evidence['bodyOffset'], 2)
            if flags & ~0xf013 or flags >> 12 != 3:
                raise ILUnsupported('INVALID_METHOD_HEADER', token=method.token)
        if any(kind not in _PRIMITIVES for kind in method.locals):
            raise ILUnsupported('UNSUPPORTED_BASELINE_LOCAL_SIGNATURE', token=method.token)

    def object_constructor(self, token):
        if token in self.base_intrinsics:
            return self.base_intrinsics[token]
        if token >> 24 != 10:
            return None
        self._scan()
        row, offset = self.meta.row(10, token & 0xffffff)
        name = self.meta.string(row[1]); self._name(name)
        signature, signature_offset = self.meta.blob(row[2])
        if row[0] != ((self.object_type >> 2) << 3 | 1) or name != '.ctor' or signature != b'\x20\x00\x01':
            return None
        evidence = {'methodToken': _hex(token), 'resolution': 'exact-core-System.Object-parameterless-constructor',
                    'memberRefMetadataOffset': offset, 'signatureOffset': signature_offset,
                    'signatureSha256': hashlib.sha256(signature).hexdigest()}
        self.evidence_budget.charge(evidence)
        self.base_intrinsics[token] = evidence
        return evidence

    def matching_initobj(self, field, token):
        if self.storage.get(field) != 'opaque-value':
            raise ILUnsupported('UNSUPPORTED_BASELINE_FIELD_ADDRESS', token=field)
        self._scan()
        table, rid = token >> 24, token & 0xffffff
        if table == 27:
            row, _ = self.meta.row(27, rid)
            signature, _ = self.meta.blob(row[0])
        elif table in (1, 2):
            self.meta.row(table, rid)
            signature = b'\x11' + _compress(_coded(token))
        else:
            raise ILUnsupported('INVALID_BASELINE_TYPE_TOKEN', token=token)
        if signature != self.signatures[field]:
            raise ILUnsupported('BASELINE_INITOBJ_TYPE_MISMATCH', token=token)

    def static_field(self, token):
        if token in self.static_fields:
            return self.static_fields[token]
        if token >> 24 != 4 or not 1 <= token & 0xffffff <= self.meta.rows[4]:
            raise ILUnsupported('UNSUPPORTED_BASELINE_STATIC_FIELD', token=token)
        if len(self.static_fields) + len(self.fields) >= self.limits.fields:
            raise ILUnsupported('FIELD_COUNT_LIMIT')
        if self.static_owners is None:
            owners = []
            previous = 1
            for rid, owner in sorted(self.types.items()):
                self._scan()
                first, last = owner['firstField'], owner['lastField']
                if not previous <= first <= last <= self.meta.rows[4] + 1:
                    raise ILUnsupported('INVALID_FIELD_OWNER_RANGE')
                previous = last; owners.append((first, last, rid))
            self.static_owners = owners
        candidates = [rid for first, last, rid in self.static_owners if first <= token & 0xffffff < last]
        if len(candidates) != 1:
            raise ILUnsupported('INVALID_FIELD_OWNER_RANGE', token=token)
        self._scan()
        row, offset = self.meta.row(4, token & 0xffffff)
        signature, signature_offset = self.meta.blob(row[2])
        if (not row[0] & 0x10 or row[0] & (0x40 | 0x100 | 0x2000) or len(signature) != 2
                or signature[0] != 6 or _KINDS.get(signature[1]) not in _PRIMITIVES):
            raise ILUnsupported('UNSUPPORTED_BASELINE_STATIC_FIELD', token=token)
        name = self.meta.string(row[1]); self._name(name)
        field = {'name': name, 'kind': _KINDS[signature[1]], 'ownerRid': candidates[0],
                 'fieldMetadataOffset': offset, 'signatureOffset': signature_offset,
                 'signatureSha256': hashlib.sha256(signature).hexdigest(), 'mutable': not bool(row[0] & 0x20)}
        self.evidence_budget.charge(field)
        self.static_fields[token] = field
        return field

    def initializer(self, owner):
        typedef = self.types[owner]
        self._scan(); row, _ = self.meta.row(2, owner)
        if row[0] & (0x18 | 0x20) or any(r[2] == owner << 1 for r in self._table(42)):
            raise ILUnsupported('UNSUPPORTED_BASELINE_STATIC_OWNER')
        if any(r[2] == owner for r in self._table(15)) or any(
                typedef['firstField'] <= r[1] < typedef['lastField'] for r in self._table(16)):
            raise ILUnsupported('UNSUPPORTED_BASELINE_STATIC_OWNER')
        found = []
        for rid in range(typedef['firstMethod'], typedef['lastMethod']):
            self._scan()
            row, offset = self.meta.row(6, rid)
            name = self.meta.string(row[3]); self._name(name)
            if name == '.cctor':
                found.append((rid, row, offset, name))
        if len(found) != 1:
            raise ILUnsupported('BASELINE_STATIC_INITIALIZER_MISSING_OR_AMBIGUOUS')
        rid, row, offset, name = found[0]
        token = 0x06000000 | rid
        signature, _ = self.meta.blob(row[4])
        if (signature != b'\x00\x00\x01' or row[1] or row[2] != 0x1891
                or any(r[2] == (rid << 1 | 1) for r in self._table(42))):
            raise ILUnsupported('INVALID_BASELINE_STATIC_INITIALIZER', token=token)
        if token not in self.method_rows:
            if len(self.method_rows) >= self.limits.method_index:
                raise ILUnsupported('METHOD_INDEX_COUNT_LIMIT')
            self.method_rows[token] = row, offset, name
        method = super().get(token)
        self._body_contract(method)
        return method


class _Work:
    def __init__(self, limits):
        self.steps = 0
        self.limits = limits

    def step(self):
        self.steps += 1
        if self.steps > self.limits.total_steps:
            raise ILUnsupported('TOTAL_STEP_LIMIT')


class _ScalarInitializer(AbstractEvaluator):
    def __init__(self, parent, owner):
        super().__init__(parent.program, parent.limits, parent.checkpoint, parent.evidence_budget)
        self.work, self.parent, self.owner = parent.work, parent, owner
        self.values = {}; self.writes = {}; self.dependencies = set()
        self.state = {}; self.excluded = {}; self.callchain = []
        self.path_steps = 0; self.path_methods = set(); self.static_reads = set()

    def _instruction(self, method, instruction, stack):
        self.work.step()
        op, token = instruction.opcode, instruction.operand
        if op in (0x7e, 0x80):
            field = self.program.static_field(token)
            if op == 0x7e:
                self.dependencies.add(token)
                if field['ownerRid'] == self.owner:
                    stack.append(self.values.get(token, UNKNOWN))
                else:
                    stack.append(self.parent.read_static(token))
            else:
                if field['ownerRid'] != self.owner:
                    raise ILUnsupported('BASELINE_FOREIGN_STATIC_WRITE', token=token)
                if not stack:
                    raise ILUnsupported('STACK_UNDERFLOW')
                value = _typed_value(stack.pop(), field['kind'])
                evidence = {'fieldToken': _hex(token), 'fieldName': field['name'],
                            'fieldType': field['kind'], 'value': _storage(value, field['kind']),
                            'known': value.kind != 'unknown', 'mutable': field['mutable'],
                            'methodToken': _hex(method.token), 'ilOffset': instruction.offset,
                            'instructionFileOffset': method.evidence['codeOffset'] + instruction.offset,
                            'ilSha256': method.evidence['ilSha256'],
                            'dependencyFieldTokens': [_hex(t) for t in sorted(self.dependencies)],
                            **{k: field[k] for k in ('fieldMetadataOffset', 'signatureOffset', 'signatureSha256')}}
                self.evidence_budget.charge(evidence)
                self.values[token] = value; self.writes[token] = evidence
            return True
        if op not in _PURE_INTEGER_OPS | {0x22, 0x23, 0x6b, 0x6c}:
            raise ILUnsupported('UNSUPPORTED_BASELINE_STATIC_EFFECT', token=method.token, offset=instruction.offset)
        return False


class BaselineEvaluator(AbstractEvaluator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.work = _Work(self.limits)
        self.state = {}; self.excluded = {}; self.callchain = []
        self.path_steps = 0; self.path_methods = set(); self.static_reads = set()
        self.references = {}; self.provenance = {}; self.resolved_statics = set()
        self.phase = 'fresh-zero'; self.base_calls = 0

    def ensure_initializer(self, owner):
        token = 0x02000000 | owner
        if owner in self.program.active_initializers:
            raise ILUnsupported('BASELINE_STATIC_DEPENDENCY_CYCLE', token=token)
        if owner not in self.program.initializer_cache:
            if len(self.program.active_initializers) + len(self.callchain) >= self.limits.call_depth:
                raise ILUnsupported('CALL_DEPTH_LIMIT', token=token)
            self.program.active_initializers.append(owner)
            evaluator = _ScalarInitializer(self, owner)
            record = {'declaringType': self.program.types[owner]['fullName'],
                      'phase': 'immediately after normal return; no subsequent mutable-static writes',
                      'status': 'UNRESOLVED', 'fields': [], 'prefixFields': []}
            self.evidence_budget.charge(record, extra=512)
            try:
                method = self.program.initializer(owner)
                record['methodToken'] = _hex(method.token)
                evaluator._method(method, [])
                record['status'] = 'CLOSED_SCALAR_INITIALIZER'
                record['fields'] = list(evaluator.writes.values())
                self.program.initializer_cache[owner] = evaluator.values
                self.program.initializer_closed[owner] = True
            except ILUnsupported as exc:
                record['diagnostic'] = {'code': exc.code, 'ilOffset': exc.offset,
                                        'token': _hex(exc.token) if exc.token else None}
                record['prefixFields'] = list(evaluator.writes.values())
                self.program.initializer_cache[owner] = {}
                self.program.initializer_closed[owner] = False
                self.program.unclosed_initializers.add(owner)
                if exc.code.endswith('_LIMIT'):
                    raise
            finally:
                self.program.active_initializers.pop()
                self.program.initializer_records.append(record)
        if self.program.active_initializers and not self.program.initializer_closed[owner]:
            raise ILUnsupported('BASELINE_UNCLOSED_STATIC_DEPENDENCY', token=token)

    def read_static(self, token):
        self.static_reads.add(token)
        field = self.program.static_field(token)
        owner = field['ownerRid']
        self.ensure_initializer(owner)
        # Unknown calls may affect any static storage, including initonly fields.
        # Never reuse an earlier closed snapshot after discovering such effects.
        value = (UNKNOWN if self.program.unclosed_initializers else
                 self.program.initializer_cache[owner].get(token, UNKNOWN))
        if value.kind != 'unknown':
            self.resolved_statics.add(token)
        return value

    def _record(self, field, method=None, offset=None):
        evidence = {'phase': self.phase, 'staticDependencyFieldTokens': [_hex(t) for t in sorted(self.static_reads)]}
        if method is not None:
            evidence.update(methodToken=_hex(method.token), ilOffset=offset,
                            instructionFileOffset=method.evidence['codeOffset'] + offset,
                            ilSha256=method.evidence['ilSha256'])
        self.evidence_budget.charge(evidence)
        self.provenance[field] = evidence

    def _instruction(self, method, instruction, stack):
        self.work.step()
        op, token, pc = instruction.opcode, instruction.operand, instruction.offset
        # A field address can live for exactly one adjacent initobj. It cannot
        # be copied, stored, passed, compared, popped or carried over a branch.
        addresses = [v for v in stack if v.kind == 'field-address']
        if addresses and (len(addresses) != 1 or stack[-1] != addresses[0] or op != 0xfe15):
            raise ILUnsupported('BASELINE_FIELD_ADDRESS_ESCAPE', offset=pc)
        if op == 0x7c:
            if not stack or stack.pop() != THIS or self.program.storage.get(token) != 'opaque-value':
                raise ILUnsupported('UNSUPPORTED_BASELINE_FIELD_ADDRESS', token=token)
            if self.program.fields[token]['flags'] & 0x20 and self.phase != 'constructor':
                raise ILUnsupported('BASELINE_READONLY_FIELD_WRITE', token=token)
            next_instruction = method.instructions.get(instruction.next_offset)
            if next_instruction is None or next_instruction.opcode != 0xfe15:
                raise ILUnsupported('BASELINE_FIELD_ADDRESS_ESCAPE', offset=pc)
            self.program.matching_initobj(token, next_instruction.operand)
            stack.append(Value('field-address', token))
            return True
        if op == 0xfe15:
            if not addresses:
                raise ILUnsupported('UNSUPPORTED_BASELINE_FIELD_ADDRESS', token=token)
            field = stack.pop().value
            self.program.matching_initobj(field, token)
            self.references[field] = Value('opaque-zero')
            self._record(field, method, pc)
            return True
        if op in (0x7b, 0x7d):
            field = self.program.fields.get(token)
            if field is None:
                raise ILUnsupported('UNSUPPORTED_FIELD_RECEIVER_OR_OWNER', token=token)
            if op == 0x7d:
                if field['flags'] & 0x20 and self.phase != 'constructor':
                    raise ILUnsupported('BASELINE_READONLY_FIELD_WRITE', token=token)
                self._record(token, method, pc)
            if field['kind'] == 'other':
                if op == 0x7b:
                    # Even opaque zero is not a numeric stack value. Null reads
                    # are also excluded to keep this extension narrowly scoped.
                    raise ILUnsupported('UNSUPPORTED_BASELINE_NONNUMERIC_READ', token=token)
                if len(stack) < 2:
                    raise ILUnsupported('STACK_UNDERFLOW')
                value, receiver = stack.pop(), stack.pop()
                if receiver != THIS or self.program.storage[token] != 'reference' or value.kind != 'null':
                    raise ILUnsupported('UNSUPPORTED_BASELINE_REFERENCE_WRITE', token=token)
                self.references[token] = value
                return True
        if op == 0x7e:
            stack.append(self.read_static(token))
            return True
        if op == 0x72:
            raise ILUnsupported('UNSUPPORTED_BASELINE_OBJECT_VALUE', token=token)
        if op == 0x28:
            intrinsic = self.program.object_constructor(token)
            if intrinsic is not None:
                if self.phase != 'constructor' or len(self.callchain) != 1 or self.base_calls:
                    raise ILUnsupported('INVALID_BASELINE_BASE_CONSTRUCTOR_ORDER', token=token)
                if not stack or stack.pop() != THIS:
                    raise ILUnsupported('UNSUPPORTED_CALL_RECEIVER', token=token)
                self.base_calls += 1
                return True
            if token in (self.program.constructor, self.program.reset):
                raise ILUnsupported('UNSUPPORTED_BASELINE_ENTRY_CALL', token=token)
            target = self.program.get(token)
            if token in self.program.setters:
                if len(stack) < 2:
                    raise ILUnsupported('STACK_UNDERFLOW')
                value, receiver = stack.pop(), stack.pop()
                if receiver != THIS or value.kind != 'null':
                    raise ILUnsupported('UNSUPPORTED_BASELINE_REFERENCE_SETTER', token=token)
                # Charge the verified setter's four instructions as well.
                if len(self.callchain) >= self.limits.call_depth:
                    raise ILUnsupported('CALL_DEPTH_LIMIT', token=token)
                if len(target.instructions) > self.limits.method_steps:
                    raise ILUnsupported('METHOD_STEP_LIMIT', token=token)
                if min(self.limits.stack, target.max_stack) < 2:
                    raise ILUnsupported('STACK_LIMIT', token=token)
                for _ in target.instructions:
                    self.checkpoint(); self.work.step()
                field = self.program.setters[token]
                self.references[field] = value
                self._record(field, target, 2)
                return True
        return False

    def run(self, reset_type):
        # Establish Item's type-initialization effects before construction. A
        # genuinely absent initializer has no effects; an invalid one is not absent.
        owner = self.program.type_rid
        typedef = self.program.types[owner]
        initializers = []
        for rid in range(typedef['firstMethod'], typedef['lastMethod']):
            self.program._scan()
            row, _ = self.program.meta.row(6, rid)
            if self.program.meta.string(row[3]) == '.cctor':
                initializers.append(rid)
        if initializers:
            self.ensure_initializer(owner)
        else:
            self.program.initializer_cache[owner] = {}
            self.program.initializer_closed[owner] = True
        uncertain_before_receiver = bool(self.program.unclosed_initializers)
        for token, field in self.program.fields.items():
            self.checkpoint()
            self._record(token)
            if field['kind'] in _PRIMITIVES:
                value = False if field['kind'] == 'bool' else 0.0 if field['kind'] in ('r4', 'r8') else 0
                assignment = {'fieldToken': _hex(token), 'fieldName': field['name'], 'fieldType': field['kind'],
                              'value': value, 'evidence': {'phase': 'fresh-zero',
                                  'fieldMetadataOffset': field['metadataOffset'],
                                  'signatureOffset': field['signatureOffset'],
                                  'signatureSha256': hashlib.sha256(b'\x06' + self.program.signatures[token]).hexdigest()}}
                self.evidence_budget.charge(assignment)
                self.state[token] = assignment
            else:
                self.references[token] = Value('null' if self.program.storage[token] == 'reference' else 'opaque-zero')
        self.phase = 'constructor'
        self._method(self.program.get(self.program.constructor), [THIS])
        if self.base_calls != 1:
            raise ILUnsupported('BASELINE_BASE_CONSTRUCTOR_NOT_CALLED')
        self.phase = 'reset'
        self._method(self.program.get(self.program.reset), [THIS, Value('i4', reset_type)])
        if self.program.unclosed_initializers and not uncertain_before_receiver:
            # Earlier static-driven branches can skip writes entirely. Per-field
            # write provenance cannot safely recover that control dependence.
            # Reject the whole receiver end-state rather than retain stale zeros.
            self.resolved_statics.clear()
            raise ILUnsupported('BASELINE_LATE_STATIC_EFFECTS')



def extract_fresh_item_baseline(meta, types, *, reset_type=0, limits=StaticILLimits(), checkpoint=None, input_sha256=None):
    """Prove primitive fields after new Item() then ResetStats(reset_type).

    No reused receiver or caller-supplied seed is accepted. No Item(id) overload
    equivalence, dispatch, postprocessing or final-default claim is made. Static
    values are conditional on the explicitly declared pristine snapshot phase.
    """
    if type(reset_type) is not int or not -0x80000000 <= reset_type <= 0x7fffffff:
        raise PipelineError('Baseline reset_type must be a signed 32-bit integer')
    checkpoint = checkpoint or (lambda: None)
    deadline = time.monotonic() + limits.wall_seconds
    def bounded_checkpoint():
        checkpoint()
        if time.monotonic() >= deadline:
            raise ILUnsupported('TOTAL_TIME_LIMIT')
    result = {'schemaVersion': 1, 'scope': 'fresh-instance-constructor-then-ResetStats-primitive-baseline',
              'status': 'UNRESOLVED', 'executedInput': False, 'complete': False, 'finalItemDefaults': False,
              'baselineComplete': False, 'initialObjectPolicy': 'fresh allocation only; zero storage; never a reused receiver',
              'resetArguments': [reset_type], 'fields': [], 'excludedFields': [], 'prefixFields': [],
              'methods': [], 'staticInitializers': [], 'baseConstructorIntrinsics': [],
              'analysisAssumptions': ['normal return only; not a complete CLI verifier or execution certificate',
                  'exact external core-library metadata identity; no runtime or signature verification',
                  'closed scalar type initializers have completed; no subsequent writes to their mutable statics',
                  'Item type initialization is inspected before fresh receiver construction',
                  'unclosed initializer effects invalidate all static snapshots; prefixes are never snapshots',
                  'nonnumeric fields remain excluded even when null or opaque zero is tracked']}
    if input_sha256 is not None:
        result['inputSha256'] = input_sha256
    budget = EvidenceBudget(limits.evidence_bytes, bounded_checkpoint)
    program = evaluator = None
    try:
        budget.charge(result, extra=2048)
        program = BaselineProgram(meta, types, limits, bounded_checkpoint, budget)
        evaluator = BaselineEvaluator(program, limits, bounded_checkpoint, budget)
        evaluator.run(reset_type)
        result['status'] = 'PROVEN_FRESH_PRIMITIVE_BASELINE'
        for token, field in program.fields.items():
            bounded_checkpoint()
            if field['kind'] not in _PRIMITIVES:
                entry = {'fieldToken': _hex(token), 'fieldName': field['name'], 'status': 'EXCLUDED_NONPRIMITIVE',
                         'storage': program.storage[token], 'trackedState': evaluator.references[token].kind,
                         'evidence': evaluator.provenance[token]}
                budget.charge(entry)
                result['excludedFields'].append(entry)
                continue
            if token in evaluator.state:
                entry = {**evaluator.state[token], 'status': 'PROVEN',
                         'evidence': {**evaluator.state[token]['evidence'], **evaluator.provenance[token]}}
            else:
                excluded = evaluator.excluded[token]
                entry = {'fieldToken': _hex(token), 'fieldName': field['name'], 'fieldType': field['kind'],
                         'status': 'UNKNOWN', 'reason': excluded['reason'], 'evidence': evaluator.provenance[token]}
            budget.charge(entry)
            result['fields'].append(entry)
        result['baselineComplete'] = all(f['status'] == 'PROVEN' for f in result['fields'])
        if not result['baselineComplete']:
            result['status'] = 'PARTIAL_FRESH_PRIMITIVE_BASELINE'
    except ILUnsupported as exc:
        result['status'] = exc.code
        result['diagnostic'] = {'code': exc.code, 'ilOffset': exc.offset,
                                'token': _hex(exc.token) if exc.token is not None else None,
                                'callChain': [_hex(t) for t in getattr(exc, 'callchain', ())]}
        # Any failure invalidates the end-of-reset state. Keep only diagnostic
        # prefixes; a previously known zero must not survive a later unknown effect.
        result['fields'] = []; result['excludedFields'] = []; result['baselineComplete'] = False
        if evaluator is not None:
            result['prefixFields'] = list(evaluator.state.values())
    if program is not None:
        result['typeEvidence'] = program.type_evidence
        result['methods'] = list(program.used.values())
        for record in program.initializer_records:
            annotation = {'snapshotUsable': not bool(program.unclosed_initializers) and record['status'] == 'CLOSED_SCALAR_INITIALIZER',
                          'snapshotScope': 'all discovered initializer effects closed' if not program.unclosed_initializers else
                                           'unusable: an initializer has unclosed effects; local writes are diagnostic only'}
            # Covered by each initializer record's 512-byte diagnostic reserve.
            record.update(annotation)
        result['staticInitializers'] = program.initializer_records
        result['baseConstructorIntrinsics'] = list(program.base_intrinsics.values())
    result['analyzedInstructions'] = evaluator.work.steps if evaluator else 0
    result['unresolvedStaticReads'] = [_hex(t) for t in sorted(evaluator.static_reads - evaluator.resolved_statics)] if evaluator else []
    result['provenPrimitiveFields'] = sum(f['status'] == 'PROVEN' for f in result['fields'])
    result['unknownPrimitiveFields'] = sum(f['status'] == 'UNKNOWN' for f in result['fields'])
    result['evidenceBudget'] = {'limitBytes': limits.evidence_bytes, 'accountedConstructionBytes': budget.used,
                              'accounting': 'cumulative; includes replaced entries and diagnostic reserve'}
    json_evidence_size(result, limits.evidence_bytes, checkpoint)
    return result
