"""Whole-method proof of accessory prefix field-add effects, without game execution.

This closes one finite source fact family. It does not initialize a Player,
extract weapon-prefix multipliers, or authorize a complete item publication.
"""
from __future__ import annotations

from pathlib import Path
import math
import struct

from .item_texture_aliases import (_Program, _Budget, ItemTextureAliasLimits, _CheckpointCancelled,
                                  _compressed_bytes, _constant, _require)
from .server_semantics import read_assembly_bytes, SemanticLimits
from .security import sha256
from .static_il import ILUnsupported, json_evidence_size

PROFILES = {
    '960a03bff6050cf7be16dfc1a7b19e10fc2c4f8f835a6a3b135a50dd9e6ba2f3': {
        'role': 'client', 'token': 0x060008f1,
        'ilSha256': 'cebe32f18e40537804cbb8c0fff03ffc5722a9bc1dac93dc33388b4b30de9ff8',
        'signatureSha256': 'd1173e92f50a39e5ed0b83c99d6dfbb486467bb2f79008280d663c88454a8f9b'},
    'd87e3faf08637f6be8882c63e7f11fb7e792b0230006309618473ece0f863e1e': {
        'role': 'server', 'token': 0x06000abe,
        'ilSha256': 'bb8e05b0ce7ed7206cf62e50bd464295889e109da399972a57faba787329f5b9',
        'signatureSha256': 'fa0c186fa5bc7b2e6263e946a5550aadbdd5c66eb914f79cb7527fa3dbd22b2b'},
}
GROUPS = (
    ('defense', ('statDefense',), 'int32'),
    ('maxMana', ('statManaMax2',), 'int32'),
    ('critBonus', ('meleeCrit', 'rangedCrit', 'magicCrit'), 'int32'),
    ('damageBonus', ('meleeDamage', 'rangedDamage', 'magicDamage', 'minionDamage'), 'float32-percent'),
    ('moveBonus', ('moveSpeed',), 'float32-percent'),
    ('meleeSpeedBonus', ('meleeSpeed',), 'float32-percent'),
)


def _instance_field(program, token, owner, name=None, signature=None):
    _require(token >> 24 == 4, 'PREFIX_EXPECTED_FIELDDEF')
    rid = token & 0xffffff
    _, typedef = program.owner(owner)
    _require(typedef['firstField'] <= rid < typedef['lastField'], 'PREFIX_FIELD_OWNER')
    row, offset = program.row(4, rid)
    _require(not row[0] & (0x10 | 0x20 | 0x40 | 0x100 | 0x2000), 'PREFIX_FIELD_FLAGS')
    actual = program.meta.string(row[1]); sig, at = program.meta.blob(row[2])
    matches = [n for n in range(typedef['firstField'], typedef['lastField'])
               if program.meta.string(program.row(4, n)[0][1]) == actual]
    _require(matches == [rid], 'PREFIX_AMBIGUOUS_FIELD_NAME')
    if name is not None: _require(actual == name, 'PREFIX_FIELD_NAME')
    if signature is not None: _require(sig == signature, 'PREFIX_FIELD_SIGNATURE')
    return actual, sig, {'fieldToken': f'0x{token:08x}', 'owner': owner, 'name': actual,
                        'metadataOffset': offset, 'signatureOffset': at, 'signatureSha256': sha256(sig)}


def _prove(program, method):
    """Exact flat if(byte-prefix == literal) { this.field += literal; } grammar."""
    item, _ = program.owner('Terraria.Item')
    player, _ = program.owner('Terraria.Player')
    _require(all(program.row(2, rid)[0][0] & 0x18 != 0x10 for rid in (item, player)), 'PREFIX_OVERLAPPING_EXPLICIT_LAYOUT')
    signature = b'\x20\x01\x01\x12' + _compressed_bytes(item << 2)
    _require(method['signature'] == signature and not method['local'] and not method['eh'], 'PREFIX_METHOD_SIGNATURE_OR_LOCALS')
    max_stack = 8 if method['header'] == 1 else program.meta.reader.uint(method['evidence']['bodyOffset'] + 2, 2)
    _require(max_stack >= 3, 'PREFIX_MAXSTACK_TOO_SMALL')
    code = method['instructions']
    _require(code and code[-1].opcode == 0x2a and sum(i.opcode == 0x2a for i in code) == 1, 'PREFIX_SINGLE_FINAL_RETURN')
    evidence, result, prefix_field, index = {}, [], None, 0
    while index < len(code) - 1:
        program.budget.check()
        start = index
        _require(index + 4 < len(code), 'PREFIX_TRUNCATED_CASE')
        a, field, literal, branch = code[index:index + 4]
        _require(a.opcode == 3 and field.opcode == 0x7b and branch.opcode in (0x33, 0x40), 'PREFIX_CASE_SHAPE')
        _, _, proof = _instance_field(program, field.operand, 'Terraria.Item', 'prefix', b'\x06\x05')
        prefix_field = field.operand if prefix_field is None else prefix_field
        _require(field.operand == prefix_field, 'PREFIX_CHANGED_DISCRIMINATOR')
        prefix = _constant(literal)
        _require(0 <= prefix <= 255 and prefix not in {r['prefixId'] for r in result}, 'PREFIX_CASE_DOMAIN')
        evidence[proof['fieldToken']] = proof
        index += 4
        updates = []
        while index < len(code) - 1 and code[index].offset != branch.operand:
            program.budget.check()
            _require(index + 6 <= len(code) - 1, 'PREFIX_TRUNCATED_UPDATE')
            receiver, source, load, constant, add, store = code[index:index + 6]
            _require(receiver.opcode == 2 and source.opcode == 2 and load.opcode == 0x7b
                     and add.opcode == 0x58 and store.opcode == 0x7d and load.operand == store.operand,
                     'PREFIX_UPDATE_SHAPE')
            name, sig, field_proof = _instance_field(program, load.operand, 'Terraria.Player')
            _require(name not in {r['field'] for r in updates}, 'PREFIX_DUPLICATE_FIELD_UPDATE')
            if sig == b'\x06\x08':
                value = _constant(constant)
                _require(0 < value <= 1000, 'PREFIX_INTEGER_DELTA_RANGE')
                update = {'field': name, 'kind': 'int32-add-unchecked', 'delta': value}
            elif sig == b'\x06\x0c':
                _require(constant.opcode == 0x22 and type(constant.operand) is float
                         and math.isfinite(constant.operand) and 0 < constant.operand <= 1,
                         'PREFIX_FLOAT_LITERAL')
                value = constant.operand
                percent = round(value * 100)
                _require(0 < percent <= 100 and struct.pack('<f', percent / 100) == struct.pack('<f', value),
                         'PREFIX_EXACT_PERCENT_LITERAL')
                update = {'field': name, 'kind': 'float32-field-add', 'literalFloat32LE': struct.pack('<f', value).hex(),
                          'nominalPercent': percent}
            else: raise ILUnsupported('PREFIX_UNSUPPORTED_FIELD_TYPE')
            update['loadIlOffset'] = load.offset; update['storeIlOffset'] = store.offset
            updates.append(update); evidence[field_proof['fieldToken']] = field_proof
            _require(len(updates) <= 4, 'PREFIX_UPDATE_COUNT')
            index += 6
        _require(index < len(code) and code[index].offset == branch.operand and updates, 'PREFIX_EXACT_NEXT_CASE_BRANCH')
        matches = [(key, names, kind) for key, names, kind in GROUPS if set(names) == {r['field'] for r in updates}]
        _require(len(matches) == 1, 'PREFIX_UNKNOWN_CONSUMER_EFFECT_GROUP')
        key, names, kind = matches[0]
        values = [r.get('nominalPercent') if kind == 'float32-percent' else r.get('delta') for r in updates]
        _require(all(type(value) is int for value in values) and len(set(values)) == 1, 'PREFIX_EFFECT_GROUP_MISMATCH')
        result.append({'prefixId': prefix, 'caseIlOffset': code[start].offset,
                       'nextCaseIlOffset': branch.operand, 'fieldAdds': updates, 'consumerStats': {key: values[0]}})
        _require(len(result) <= 256, 'PREFIX_CASE_COUNT')
    _require(result and index == len(code) - 1, 'PREFIX_COMPLETE_METHOD_COVERAGE')
    return {'methodModelComplete': True, 'allBytePrefixInputsCovered': True,
            'defaultEffectForUnlistedPrefix': {}, 'effects': result,
            'fieldEvidence': list(evidence.values()), 'methodEvidence': {**method['evidence'], 'declaredMaxStack': max_stack},
            'consumerProjection': 'int field delta; exact f32(n/100) literal projects to nominal percentage n',
            'preconditions': ['nonnull Terraria.Player receiver and Terraria.Item argument',
                              'well-typed managed references without concurrent field mutation',
                              'CLI int32 unchecked addition and float32 field-store semantics'],
            'scope': 'GrantPrefixBenefits additive deltas only, not final Player stats or equipment eligibility'}


def extract_accessory_prefix_semantics(input_path: Path, checkpoint=None):
    check = checkpoint or (lambda: None)
    check()
    raw = read_assembly_bytes(input_path, SemanticLimits(), check)
    digest = sha256(raw); profile = PROFILES.get(digest)
    _require(profile is not None, 'PREFIX_UNSUPPORTED_INPUT_PROFILE')
    limits = ItemTextureAliasLimits(method_bytes=8192, total_method_bytes=8192, instructions=8192,
                                    steps=1000000, evidence_bytes=1024 * 1024)
    try:
        program = _Program(raw, _Budget(limits, check))
        method = program.method('Terraria.Player', 'GrantPrefixBenefits')
        _require(method['token'] == profile['token'] and method['evidence']['ilSha256'] == profile['ilSha256']
                 and method['evidence']['signatureSha256'] == profile['signatureSha256'], 'PREFIX_PROFILE_METHOD_PIN')
        result = _prove(program, method)
        result.update(schemaVersion=1, family='accessory-prefix-effects', status='PROVEN_FINITE_FIELD_TRANSFORM',
                      sourceRole=profile['role'], inputSha256=digest, gameVersion='1.4.5.8', executedInput=False,
                      complete=False, publishable=False, runtimeSnapshotUsable=False,
                      unsupported=['weapon prefix stat multipliers/eligibility', 'final item defaults',
                                   'final player stats', 'full item consumer group and publication'])
        json_evidence_size(result, limits.evidence_bytes, check)
        return result
    except _CheckpointCancelled as exc:
        raise exc.original
