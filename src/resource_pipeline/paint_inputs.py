"""Private, bounded paint INPUT RGB evidence from a reviewed PE/CLI profile.

The input is data, never loaded by a CLR. Color.White remains symbolic. The
closed Color setter contract is conditional on its exact XNA assembly binding.
This module does not complete the paints family or implement map/shader output.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import stat
import struct
import time

from .security import PipelineError, canonical_json
from .static_il import EvidenceSizeLimit, Instruction, json_evidence_size


@dataclass(frozen=True)
class PaintInputLimits:
    input_bytes: int = 128 * 1024 * 1024
    metadata_bytes: int = 16 * 1024 * 1024
    metadata_rows: int = 200_000
    metadata_names_bytes: int = 2 * 1024 * 1024
    signature_bytes: int = 64 * 1024
    methods: int = 8
    method_bytes: int = 64 * 1024
    total_method_bytes: int = 64 * 1024
    instructions: int = 16_384
    path_steps: int = 512
    total_steps: int = 131_072
    work: int = 500_000
    stack: int = 16
    locals: int = 16
    evidence_bytes: int = 1024 * 1024
    wall_seconds: float = 15

    def __post_init__(self):
        for name, value in vars(self).items():
            if name == 'wall_seconds':
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 120:
                    raise ValueError('Invalid paint wall-time limit')
            elif type(value) is not int or value <= 0:
                raise ValueError('Invalid paint integer limit')


class PaintInputUnsupported(PipelineError):
    """The image/profile is outside this intentionally closed proof contract."""


class PaintInputBudgetExceeded(PipelineError):
    """Fatal cumulative bound; never an optional unsupported result."""


def _require(condition, code):
    if not condition:
        raise PaintInputUnsupported(code)


def _token(value):
    return f'0x{value:08x}'


def _compressed_bytes(value):
    if value < 0x80:
        return bytes((value,))
    if value < 0x4000:
        return bytes((0x80 | value >> 8, value & 255))
    if value < 0x20000000:
        return bytes((0xc0 | value >> 24, value >> 16 & 255, value >> 8 & 255, value & 255))
    raise PaintInputUnsupported('PAINT_SIGNATURE_INTEGER_LIMIT')


@dataclass(frozen=True)
class _Profile:
    name: str
    input_sha256: str
    assembly_name: str
    assembly_version: tuple[int, int, int, int]
    il_sha256: str | None
    current_ids: tuple[int, ...]
    owner_base: str | None = None


# Fingerprints identify reviewed bytes; they contain no palette or RGB values.
# Internal _Profile/_extract_bytes are test seams, not custom-profile public APIs.
_PROFILE = _Profile(
    'terraria-server-paint-input-v1',
    'd87e3faf08637f6be8882c63e7f11fb7e792b0230006309618473ece0f863e1e',
    'TerrariaServer', (1, 4, 5, 8),
    '7d78b3302d5329769c8c936435fffa3ab53e5fc7b5eb188123f0b7782240c95e',
    tuple(range(1, 31)), 'System.Object',
)


class _Budget:
    def __init__(self, limits, checkpoint=None):
        self.limits = limits
        self.checkpoint = checkpoint or (lambda: None)
        self.deadline = time.monotonic() + limits.wall_seconds
        self.used = dict(input_bytes=0, signature_bytes=0, methods=0,
                         total_method_bytes=0, instructions=0, total_steps=0,
                         work=0, evidence_bytes=0)

    def check(self):
        # Caller exceptions propagate unchanged, including cancellation expressed
        # as PipelineError or ILUnsupported. No catch wraps an external callback.
        self.checkpoint()
        self.charge('work', 1)
        if time.monotonic() >= self.deadline:
            raise PaintInputBudgetExceeded('PAINT_TIME_LIMIT')

    def charge(self, kind, amount):
        if amount < 0 or amount > getattr(self.limits, kind) - self.used[kind]:
            raise PaintInputBudgetExceeded('PAINT_' + kind.upper() + '_LIMIT')
        self.used[kind] += amount

    def evidence(self, value):
        # json_evidence_size normalizes ValueError/UnicodeError. Preserve the
        # caller's exact cancellation exception by keeping callbacks separate.
        class CheckpointFailure(BaseException):
            def __init__(self, original):
                self.original = original

        def checkpoint():
            try:
                self.check()
            except Exception as exc:
                raise CheckpointFailure(exc) from exc

        try:
            size = json_evidence_size(value, self.limits.evidence_bytes - self.used['evidence_bytes'], checkpoint)
        except CheckpointFailure as exc:
            raise exc.original
        except EvidenceSizeLimit:
            raise PaintInputBudgetExceeded('PAINT_EVIDENCE_BYTES_LIMIT') from None
        self.charge('evidence_bytes', size)
        return value

    def final_json(self, result):
        # Charge final JSON in addition to every previously retained evidence
        # entry. Preflight bounds allocation; recheck cancellation/time after it.
        self.evidence(result)
        encoded = canonical_json(result)
        self.check()
        return encoded


def _read_input(path, budget):
    path = Path(path)
    budget.check()
    _require(not any(part.is_symlink() for part in (path, *path.parents)), 'PAINT_INPUT_SYMLINK')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        _require(stat.S_ISREG(before.st_mode) and before.st_size > 0, 'PAINT_INPUT_NOT_REGULAR')
        if before.st_size > budget.limits.input_bytes:
            raise PaintInputBudgetExceeded('PAINT_INPUT_BYTES_LIMIT')
        chunks = []
        while True:
            budget.check()
            chunk = source.read(min(1024 * 1024, budget.limits.input_bytes - budget.used['input_bytes'] + 1))
            if not chunk:
                break
            budget.charge('input_bytes', len(chunk))
            chunks.append(chunk)
        after = os.fstat(source.fileno())
    _require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
             (after.st_size, after.st_mtime_ns, after.st_ctime_ns) and
             budget.used['input_bytes'] == before.st_size, 'PAINT_INPUT_CHANGED')
    data = b''.join(chunks)
    budget.check()
    return data


def _digest(data, budget):
    h = hashlib.sha256()
    for start in range(0, len(data), 1024 * 1024):
        budget.check()
        h.update(memoryview(data)[start:start + 1024 * 1024])
    return h.hexdigest()


class _Image:
    def __init__(self, data, budget, profile):
        from .server_semantics import SemanticLimits, _Metadata, _types
        self.budget = budget
        self.profile = profile
        self.evidence = []
        self.members = {}
        self.meta = _Metadata(data, SemanticLimits(
            input_bytes=budget.limits.input_bytes, metadata_bytes=budget.limits.metadata_bytes,
            table_rows=budget.limits.metadata_rows,
            metadata_names_bytes=budget.limits.metadata_names_bytes,
            type_fullname_bytes=budget.limits.metadata_names_bytes,
            resource_file_bytes=4096), budget.check)
        self.types = _types(self.meta)
        self._assembly()
        matches = []
        for rid, typedef in self.types.items():
            budget.check()
            if typedef['fullName'] == 'Terraria.WorldGen':
                matches.append(rid)
        _require(len(matches) == 1, 'PAINT_OWNER_MISSING_OR_AMBIGUOUS')
        self.owner = matches[0]
        row, _ = self.meta.row(2, self.owner)
        _require(row[0] == 0x100001 and self.meta.string(row[1]) == 'WorldGen' and
                 self.meta.string(row[2]) == 'Terraria', 'PAINT_OWNER_IDENTITY_MISMATCH')
        self._owner_base(row[3])
        for rid in range(1, self.meta.rows[42] + 1):
            row, _ = self.meta.row(42, rid)
            _require(row[2] != self.owner << 1, 'PAINT_GENERIC_OWNER')
        self.color = self._color_type()
        self.color_signature = b'\x11' + _compressed_bytes(self.color << 2 | 1)

    def blob(self, index):
        # The existing metadata reader checks the heap range and 4 KiB cap.
        self.budget.check()
        blob, offset = self.meta.blob(index)
        self.budget.charge('signature_bytes', len(blob))
        return blob, offset

    def keep(self, value):
        self.evidence.append(self.budget.evidence(value))

    def _assembly(self):
        _require(self.meta.rows[32] == 1, 'PAINT_ASSEMBLY_MISSING_OR_AMBIGUOUS')
        row, offset = self.meta.row(32, 1)
        key, _ = self.blob(row[6])
        _require(row[:6] == (0x8004, *self.profile.assembly_version, 0) and not key and
                 self.meta.string(row[7]) == self.profile.assembly_name and not self.meta.string(row[8]),
                 'PAINT_ASSEMBLY_IDENTITY_MISMATCH')
        self.keep({'assemblyToken': '0x20000001', 'name': self.profile.assembly_name,
                   'version': list(self.profile.assembly_version), 'metadataOffset': offset})

    def _owner_base(self, coded):
        if self.profile.owner_base is None:
            _require(coded == 0, 'PAINT_OWNER_BASE_MISMATCH')
            return
        _require(self.profile.owner_base == 'System.Object' and coded & 3 == 1 and coded >> 2 > 0,
                 'PAINT_OWNER_BASE_MISMATCH')
        row, offset = self.meta.row(1, coded >> 2)
        _require(self.meta.string(row[1]) == 'Object' and self.meta.string(row[2]) == 'System' and
                 row[0] & 3 == 2 and row[0] >> 2 > 0, 'PAINT_OWNER_BASE_MISMATCH')
        assembly, assembly_offset = self.meta.row(35, row[0] >> 2)
        key, _ = self.blob(assembly[5])
        assembly_hash, _ = self.blob(assembly[8])
        _require(assembly[:5] == (4, 0, 0, 0, 0) and self.meta.string(assembly[6]) == 'mscorlib' and
                 not self.meta.string(assembly[7]) and key == bytes.fromhex('b77a5c561934e089') and
                 not assembly_hash, 'PAINT_OWNER_BASE_ASSEMBLY_MISMATCH')
        self.keep({'ownerBaseToken': _token(0x01000000 | coded >> 2), 'type': 'System.Object',
                   'metadataOffset': offset, 'assemblyRefOffset': assembly_offset,
                   'assembly': 'mscorlib', 'version': [4, 0, 0, 0], 'publicKeyToken': key.hex()})

    def _color_type(self):
        matches = []
        for rid in range(1, self.meta.rows[1] + 1):
            row, offset = self.meta.row(1, rid)
            if self.meta.string(row[1]) == 'Color' and self.meta.string(row[2]) == 'Microsoft.Xna.Framework':
                matches.append((rid, row, offset))
        _require(len(matches) == 1, 'PAINT_COLOR_MISSING_OR_AMBIGUOUS')
        rid, row, offset = matches[0]
        _require(row[0] & 3 == 2 and row[0] >> 2 > 0, 'PAINT_COLOR_SCOPE_MISMATCH')
        assembly, assembly_offset = self.meta.row(35, row[0] >> 2)
        key, _ = self.blob(assembly[5])
        assembly_hash, _ = self.blob(assembly[8])
        _require(assembly[:5] == (4, 0, 0, 0, 0) and
                 self.meta.string(assembly[6]) == 'Microsoft.Xna.Framework' and
                 not self.meta.string(assembly[7]) and key == bytes.fromhex('842cf8be1de50553') and
                 not assembly_hash, 'PAINT_COLOR_ASSEMBLY_MISMATCH')
        self.keep({'typeToken': _token(0x01000000 | rid), 'type': 'Microsoft.Xna.Framework.Color',
                   'metadataOffset': offset, 'assemblyRefToken': _token(0x23000000 | row[0] >> 2),
                   'assemblyRefOffset': assembly_offset, 'assembly': 'Microsoft.Xna.Framework',
                   'version': [4, 0, 0, 0], 'publicKeyToken': key.hex(),
                   'intrinsicContract': 'value-copy; byte setters replace only the named channel; White channels symbolic'})
        return rid

    def member(self, token):
        if token in self.members:
            return self.members[token]
        _require(token >> 24 == 10, 'PAINT_CALL_NOT_MEMBERREF')
        row, offset = self.meta.row(10, token & 0xffffff)
        _require(row[0] == self.color << 3 | 1, 'PAINT_MEMBER_OWNER_MISMATCH')
        name = self.meta.string(row[1])
        signature, sig_offset = self.blob(row[2])
        if name == 'get_White':
            _require(signature == b'\x00\x00' + self.color_signature, 'PAINT_WHITE_SIGNATURE_MISMATCH')
            action = 'White'
        else:
            _require(name in ('set_R', 'set_G', 'set_B', 'set_A'), 'PAINT_UNSUPPORTED_MEMBER')
            _require(signature == b'\x20\x01\x01\x05', 'PAINT_SETTER_SIGNATURE_MISMATCH')
            action = name[-1]
        self.keep({'memberToken': _token(token), 'name': name, 'ownerToken': _token(0x01000000 | self.color),
                   'metadataOffset': offset, 'signatureOffset': sig_offset,
                   'signatureSha256': hashlib.sha256(signature).hexdigest()})
        self.members[token] = action
        return action

    def method(self):
        owner = self.types[self.owner]
        matches = []
        for rid in range(owner['firstMethod'], owner['lastMethod']):
            row, offset = self.meta.row(6, rid)
            if self.meta.string(row[3]) == 'paintColor':
                # Count rejected candidates and their signatures before testing.
                self.budget.charge('methods', 1)
                signature, sig_offset = self.blob(row[4])
                matches.append((rid, row, offset, signature, sig_offset))
        _require(len(matches) == 1, 'PAINT_METHOD_MISSING_OR_AMBIGUOUS')
        rid, row, metadata_offset, signature, signature_offset = matches[0]
        _require(signature == b'\x00\x01' + self.color_signature + b'\x08', 'PAINT_METHOD_SIGNATURE_MISMATCH')
        _require(row[0] and row[1] == 0 and row[2] == 0x96, 'PAINT_METHOD_FLAGS_MISMATCH')
        for generic_rid in range(1, self.meta.rows[42] + 1):
            generic, _ = self.meta.row(42, generic_rid)
            _require(generic[2] != rid << 1 | 1, 'PAINT_GENERIC_METHOD')
        start = self.meta.rva(row[0], 12)
        flags = self.meta.reader.uint(start, 2)
        # Only this fat, init-locals, no-EH header is in the reviewed profile.
        _require(flags == 0x3013, 'PAINT_METHOD_HEADER_OR_EH_UNSUPPORTED')
        max_stack = self.meta.reader.uint(start + 2, 2)
        if max_stack > self.budget.limits.stack:
            raise PaintInputBudgetExceeded('PAINT_STACK_LIMIT')
        _require(max_stack >= 1, 'PAINT_INVALID_MAX_STACK')
        size = self.meta.reader.uint(start + 4, 4)
        self.budget.charge('total_method_bytes', size)
        if size > self.budget.limits.method_bytes:
            raise PaintInputBudgetExceeded('PAINT_METHOD_BYTES_LIMIT')
        _require(size > 0, 'PAINT_EMPTY_METHOD')
        local_token = self.meta.reader.uint(start + 8, 4)
        _require(local_token >> 24 == 17, 'PAINT_LOCAL_TOKEN_MISMATCH')
        local_row, local_offset = self.meta.row(17, local_token & 0xffffff)
        local_sig, local_sig_offset = self.blob(local_row[0])
        if self.budget.limits.locals < 2:
            raise PaintInputBudgetExceeded('PAINT_LOCALS_LIMIT')
        _require(local_sig == b'\x07\x02' + self.color_signature + b'\x08', 'PAINT_LOCAL_SIGNATURE_MISMATCH')
        code_offset = self.meta.rva(row[0] + 12, size)
        _require(code_offset == start + 12, 'PAINT_NONCONTIGUOUS_METHOD')
        code = self.meta.reader.take(code_offset, size)
        code_hash = _digest(code, self.budget)
        if self.profile.il_sha256 is not None:
            _require(code_hash == self.profile.il_sha256, 'PAINT_UNREVIEWED_METHOD_PROFILE')
        # Decode all bytes, including unreachable instructions. Decoder callbacks
        # charge unsuccessful scans and partial decoding before any rejection.
        ins = _decode(code, self.budget)
        _validate_instructions(ins, self)
        evidence = {'methodToken': _token(0x06000000 | rid), 'owner': 'Terraria.WorldGen',
                    'method': 'paintColor', 'parameterType': 'int32', 'evaluatedDomain': 'integers 0..255',
                    'metadataOffset': metadata_offset, 'bodyOffset': start, 'headerBytes': 12,
                    'headerSha256': hashlib.sha256(self.meta.reader.take(start, 12)).hexdigest(),
                    'maxStack': max_stack, 'codeOffset': code_offset, 'codeBytes': size, 'ilSha256': code_hash,
                    'signatureOffset': signature_offset, 'signatureSha256': hashlib.sha256(signature).hexdigest(),
                    'localToken': _token(local_token), 'localMetadataOffset': local_offset,
                    'localSignatureOffset': local_sig_offset, 'localSignatureSha256': hashlib.sha256(local_sig).hexdigest(),
                    'exceptionRegions': False}
        self.keep(evidence)
        return ins, max_stack, evidence


def _decode(code, budget):
    """Decode only the closed grammar; charge even unknown/truncated attempts."""
    position, instructions = 0, {}
    no_operand = {2, 6, 7, 10, 11, 0x2a} | set(range(0x15, 0x1f))
    formats = {0x12: 'B', 0x1f: 'b', 0x20: 'i', 0x28: 'I', 0x2e: 'b', 0x33: 'b'}
    while position < len(code):
        budget.check()
        budget.charge('instructions', 1)
        offset = position
        opcode = code[position]
        position += 1
        operand = None
        if opcode in formats:
            fmt = formats[opcode]
            size = struct.calcsize('<' + fmt)
            _require(position <= len(code) - size, 'PAINT_TRUNCATED_IL_OPERAND')
            operand = struct.unpack_from('<' + fmt, code, position)[0]
            position += size
            if opcode in (0x2e, 0x33):
                operand += position
        else:
            _require(opcode in no_operand, 'PAINT_UNSUPPORTED_OPCODE')
        instructions[offset] = Instruction(offset, opcode, operand, position)
    for ins in instructions.values():
        budget.check()
        if ins.opcode in (0x2e, 0x33):
            _require(ins.operand in instructions, 'PAINT_BRANCH_TARGET_NOT_INSTRUCTION')
    return instructions


def _constant(ins):
    if 0x15 <= ins.opcode <= 0x1e:
        return ins.opcode - 0x16
    if ins.opcode in (0x1f, 0x20):
        return ins.operand
    raise PaintInputUnsupported('PAINT_EXPECTED_INTEGER_CONSTANT')


def _validate_instructions(instructions, image):
    allowed = {2, 6, 7, 10, 11, 0x12, 0x1f, 0x20, 0x28, 0x2a, 0x2e, 0x33} | set(range(0x15, 0x1f))
    for ins in instructions.values():
        image.budget.check()
        _require(ins.opcode in allowed, 'PAINT_UNSUPPORTED_OPCODE')
        if ins.opcode in (0x2e, 0x33):
            _require(ins.operand in instructions and ins.operand > ins.offset, 'PAINT_BACKWARD_OR_INVALID_BRANCH')
        if ins.opcode == 0x28:
            image.member(ins.operand)
        if ins.opcode == 0x12:
            _require(ins.operand == 0, 'PAINT_INVALID_LOCAL_ADDRESS')
            value = instructions.get(ins.next_offset)
            _require(value is not None, 'PAINT_ADDRESS_LIFETIME')
            _require(0 <= _constant(value) <= 255, 'PAINT_SETTER_BYTE_RANGE')
            setter = instructions.get(value.next_offset)
            _require(setter is not None and setter.opcode == 0x28 and
                     image.member(setter.operand) in 'RGBA', 'PAINT_ADDRESS_LIFETIME')
    # A branch may not enter the middle of an address/constant/setter sequence.
    protected = set()
    for ins in instructions.values():
        image.budget.check()
        if ins.opcode == 0x12:
            value = instructions[ins.next_offset]
            protected.update((value.offset, value.next_offset))
    for ins in instructions.values():
        image.budget.check()
        if ins.opcode in (0x2e, 0x33):
            _require(ins.operand not in protected, 'PAINT_BRANCH_INTO_SETTER')


@dataclass(frozen=True)
class _Channel:
    value: int | None
    source: str
    offset: int
    member: int


@dataclass(frozen=True)
class _Color:
    channels: tuple[_Channel, ...]


@dataclass(frozen=True)
class _Address:
    local: int
    generation: int
    setter_offset: int


def _run_path(instructions, argument, image, max_stack):
    _require(type(argument) is int and 0 <= argument <= 255, 'PAINT_ARGUMENT_OUTSIDE_BYTE_DOMAIN')
    stack, locals_, generations, writes = [], [None, None], [0, 0], []
    pc, steps = 0, 0

    def pop():
        _require(bool(stack), 'PAINT_STACK_UNDERFLOW')
        return stack.pop()

    def push(value):
        if len(stack) >= image.budget.limits.stack:
            raise PaintInputBudgetExceeded('PAINT_STACK_LIMIT')
        _require(len(stack) < max_stack, 'PAINT_DECLARED_MAX_STACK_EXCEEDED')
        stack.append(value)

    while True:
        image.budget.check()
        if steps >= image.budget.limits.path_steps:
            raise PaintInputBudgetExceeded('PAINT_PATH_STEPS_LIMIT')
        image.budget.charge('total_steps', 1)
        steps += 1
        _require(pc in instructions, 'PAINT_INVALID_PC_OR_MISSING_RETURN')
        ins = instructions[pc]
        pc = ins.next_offset
        op = ins.opcode
        if op == 2:
            push(argument)
        elif op in (6, 7):
            value = locals_[op - 6]
            _require(value is not None, 'PAINT_UNINITIALIZED_LOCAL')
            # _Color/_Channel are immutable: ldloc has value-copy semantics.
            push(value)
        elif op in (10, 11):
            value, local = pop(), op - 10
            _require(isinstance(value, _Color) if local == 0 else type(value) is int,
                     'PAINT_LOCAL_VALUE_TYPE_MISMATCH')
            _require(not any(isinstance(v, _Address) and v.local == local for v in stack), 'PAINT_LIVE_ADDRESS_REASSIGNMENT')
            locals_[local] = value
            generations[local] += 1
        elif op == 0x12:
            _require(ins.operand == 0 and isinstance(locals_[0], _Color), 'PAINT_INVALID_LOCAL_ADDRESS')
            setter_offset = instructions[ins.next_offset].next_offset
            push(_Address(0, generations[0], setter_offset))
        elif 0x15 <= op <= 0x20:
            push(_constant(ins))
        elif op in (0x2e, 0x33):
            right, left = pop(), pop()
            _require(type(left) is int and type(right) is int, 'PAINT_COMPARE_TYPE_MISMATCH')
            _require(not any(isinstance(v, _Address) for v in stack), 'PAINT_ADDRESS_ACROSS_BRANCH')
            if (left == right) == (op == 0x2e):
                pc = ins.operand
        elif op == 0x28:
            action = image.members[ins.operand]
            if action == 'White':
                push(_Color(tuple(_Channel(None, 'symbolic-Color.White', ins.offset, ins.operand) for _ in 'RGBA')))
            else:
                value, address = pop(), pop()
                _require(isinstance(address, _Address) and address.local == 0 and
                         address.generation == generations[0] and address.setter_offset == ins.offset and
                         isinstance(locals_[0], _Color), 'PAINT_INVALID_OR_STALE_ADDRESS')
                _require(type(value) is int and 0 <= value <= 255, 'PAINT_SETTER_BYTE_RANGE')
                channels = list(locals_[0].channels)
                channels['RGBA'.index(action)] = _Channel(value, 'explicit-byte-setter', ins.offset, ins.operand)
                locals_[0] = _Color(tuple(channels))
                writes.append(image.budget.evidence({'channel': action, 'value': value,
                              'ilOffset': ins.offset, 'memberToken': _token(ins.operand)}))
        elif op == 0x2a:
            value = pop()
            _require(not stack and isinstance(value, _Color), 'PAINT_INVALID_RETURN')
            return value, writes, steps
        else:
            raise PaintInputUnsupported('PAINT_UNSUPPORTED_OPCODE')


def _record(argument, color, writes, steps, profile):
    channels = {}
    for name, channel in zip('RGBA', color.channels):
        channels[name] = {'value': channel.value, 'source': channel.source,
                          'ilOffset': channel.offset, 'memberToken': _token(channel.member)}
    explicit = all(channels[c]['value'] is not None for c in 'RGB')
    classification = ('current-paint' if argument in profile.current_ids else
                      'no-paint-selector' if argument == 0 else
                      'legacy-selector-not-current-paint' if argument == 31 else 'unsupported-selector')
    return {'id': argument, 'classification': classification, 'explicitRgb': explicit,
            'rgb': [channels[c]['value'] for c in 'RGB'] if explicit else None,
            'channels': channels, 'writes': writes, 'steps': steps}


def _extract_bytes(data, profile, budget):
    """Closed core; tests supply an original fixture profile, never a real table."""
    budget.check()
    _require(type(data) is bytes, 'PAINT_EXPECTED_BYTES')
    if len(data) > budget.limits.input_bytes:
        raise PaintInputBudgetExceeded('PAINT_INPUT_BYTES_LIMIT')
    if budget.used['input_bytes'] == 0:
        budget.charge('input_bytes', len(data))
    else:
        _require(budget.used['input_bytes'] == len(data), 'PAINT_INPUT_ACCOUNTING_MISMATCH')
    input_hash = _digest(data, budget)
    _require(input_hash == profile.input_sha256, 'PAINT_UNKNOWN_INPUT_PROFILE')
    image = _Image(data, budget, profile)
    instructions, max_stack, method = image.method()
    records = []
    for argument in range(256):
        budget.check()
        color, writes, steps = _run_path(instructions, argument, image, max_stack)
        records.append(budget.evidence(_record(argument, color, writes, steps, profile)))
    explicit_ids = tuple(r['id'] for r in records if r['explicitRgb'])
    _require(explicit_ids == profile.current_ids, 'PAINT_CURRENT_DOMAIN_PROFILE_MISMATCH')
    # Unassigned selectors remain actual symbolic method outcomes. They are not
    # inferred white-valued paints, and no assertion about wrapper behavior follows.
    result = {'schema': 'paint-input-rgb-evidence-v1', 'profile': profile.name,
              'privateOnly': True, 'executedInput': False, 'inputSha256': input_hash,
              'source': 'static PE/CLI; explicit validated Color byte-setter operands',
              'selectorClassificationSource': 'reviewed-profile domain policy; default return is not a paint definition',
              'argumentDomain': {'methodParameter': 'int32', 'evaluatedMin': 0, 'evaluatedMax': 255,
                                 'outsideDomain': 'unsupported'},
              'scope': {'inputRgb': 'proven-for-current-domain-under-closed-XNA-setter-contract',
                        'alpha': 'channel-provenance-only', 'defaultChannels': 'symbolic-Color.White',
                        'mapColorTransforms': 'unsupported', 'texturedPaintRendering': 'unsupported',
                        'coatings': 'unsupported', 'localizedNames': 'unsupported', 'paintsFamily': 'incomplete'},
              'method': method, 'bindings': image.evidence,
              'summary': {'evaluatedArguments': 256, 'explicitRgbRecords': len(explicit_ids),
                          'distinctExplicitRgb': len({tuple(r['rgb']) for r in records if r['explicitRgb']}),
                          'symbolicRgbRecords': 256 - len(explicit_ids),
                          'explicitAlphaRecords': sum(r['channels']['A']['value'] is not None for r in records)},
              'records': records, 'limits': dict(vars(budget.limits)),
              'workSnapshotBeforeFinalSerialization': dict(budget.used)}
    budget.final_json(result)
    return result


def extract_paint_inputs(path, *, profile='terraria-server-paint-input-v1',
                         limits=PaintInputLimits(), checkpoint=None):
    """Extract private evidence for the single reviewed server fingerprint.

    Unknown profiles, malformed or unrecognized code and incomplete proofs raise;
    cancellation and all budgets propagate. No optional-success fallback exists.
    The result has all 256 method outcomes but only 1..30 are current RGB records.
    """
    budget = _Budget(limits, checkpoint)
    budget.check()
    _require(type(profile) is str and profile == _PROFILE.name, 'PAINT_UNKNOWN_PROFILE')
    data = _read_input(path, budget)
    return _extract_bytes(data, _PROFILE, budget)
