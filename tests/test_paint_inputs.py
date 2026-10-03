"""Closed paint-input proof tests using only original synthetic PE/CLI data."""
from dataclasses import replace
import hashlib
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from resource_pipeline import paint_inputs as paint
from resource_pipeline.security import PipelineError, canonical_json
from resource_pipeline.static_il import EvidenceSizeLimit, ILUnsupported

from paint_input_fixture import (BranchEmitter, SETTER_TOKENS, WHITE_TOKEN,
                                 call, ldc, paint_input_code, paint_input_pe,
                                 setter)


def profile_for(data, *, current_ids=(1, 13, 30), il_sha256=None):
    return paint._Profile(
        name='synthetic', input_sha256=hashlib.sha256(data).hexdigest(),
        assembly_name='OriginalPaintFixture', assembly_version=(1, 0, 0, 0),
        il_sha256=il_sha256, current_ids=current_ids)


def extract_fixture(data=None, *, limits=None, checkpoint=None,
                    current_ids=(1, 13, 30), il_sha256=None, **fixture):
    if data is None:
        data = paint_input_pe(**fixture)
    budget = paint._Budget(limits or paint.PaintInputLimits(), checkpoint)
    return paint._extract_bytes(
        data, profile_for(data, current_ids=current_ids, il_sha256=il_sha256),
        budget)


def runtime(code, *, argument=0, max_stack=8, limits=None):
    """Exercise runtime defenses independently of structural prevalidation."""
    budget = paint._Budget(limits or paint.PaintInputLimits())
    instructions = paint._decode(code, budget)
    image = SimpleNamespace(budget=budget, members={
        WHITE_TOKEN: 'White', **{token: name for name, token in SETTER_TOKENS.items()}})
    return paint._run_path(instructions, argument, image, max_stack)


class PaintInputPublicBoundaryTests(unittest.TestCase):
    def test_unknown_profile_is_rejected_before_reading_any_path(self):
        for name in ('synthetic', '', None, 1, object()):
            with self.subTest(profile=name):
                with mock.patch.object(paint, '_read_input') as read:
                    with self.assertRaisesRegex(paint.PaintInputUnsupported, 'UNKNOWN_PROFILE'):
                        paint.extract_paint_inputs('/does/not/exist', profile=name)
                    read.assert_not_called()

    def test_public_api_does_not_accept_fixture_or_custom_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'original-fixture.dll'
            path.write_bytes(paint_input_pe())
            with self.assertRaisesRegex(paint.PaintInputUnsupported, 'UNKNOWN_INPUT_PROFILE'):
                paint.extract_paint_inputs(path)

    def test_public_input_limit_precedes_unknown_hash(self):
        data = paint_input_pe()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'original-fixture.dll'
            path.write_bytes(data)
            limits = replace(paint.PaintInputLimits(), input_bytes=len(data) - 1)
            with self.assertRaisesRegex(paint.PaintInputBudgetExceeded, 'INPUT_BYTES_LIMIT'):
                paint.extract_paint_inputs(path, limits=limits)

    def test_symlinks_and_empty_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'fixture.dll'
            target.write_bytes(paint_input_pe())
            link = root / 'alias.dll'
            link.symlink_to(target)
            with self.assertRaisesRegex(paint.PaintInputUnsupported, 'SYMLINK'):
                paint.extract_paint_inputs(link)
            empty = root / 'empty.dll'
            empty.touch()
            with self.assertRaisesRegex(paint.PaintInputUnsupported, 'NOT_REGULAR'):
                paint.extract_paint_inputs(empty)

    def test_internal_input_hash_and_il_hash_are_independent_guards(self):
        data = paint_input_pe()
        profile = replace(profile_for(data), input_sha256='0' * 64)
        with self.assertRaisesRegex(paint.PaintInputUnsupported, 'UNKNOWN_INPUT_PROFILE'):
            paint._extract_bytes(data, profile, paint._Budget(paint.PaintInputLimits()))
        with self.assertRaisesRegex(paint.PaintInputUnsupported, 'UNREVIEWED_METHOD_PROFILE'):
            extract_fixture(data, il_sha256='0' * 64)
        result = extract_fixture(data, il_sha256=hashlib.sha256(paint_input_code()).hexdigest())
        self.assertEqual('synthetic', result['profile'])

    def test_internal_core_requires_immutable_bytes(self):
        data = paint_input_pe()
        for value in (bytearray(data), memoryview(data)):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaisesRegex(paint.PaintInputUnsupported, 'EXPECTED_BYTES'):
                    paint._extract_bytes(value, profile_for(data), paint._Budget(paint.PaintInputLimits()))


class PaintInputEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.offsets = paint_input_pe(with_offsets=True)
        cls.result = extract_fixture(cls.data)

    def test_every_byte_selector_has_four_channel_outcomes(self):
        result = self.result
        self.assertEqual(list(range(256)), [record['id'] for record in result['records']])
        self.assertEqual({'evaluatedArguments': 256, 'explicitRgbRecords': 3,
                          'distinctExplicitRgb': 2, 'symbolicRgbRecords': 253,
                          'explicitAlphaRecords': 1}, result['summary'])
        for record in result['records']:
            with self.subTest(selector=record['id']):
                self.assertEqual(set('RGBA'), set(record['channels']))
                if record['id'] not in (1, 13, 30):
                    self.assertFalse(record['explicitRgb'])
                    self.assertIsNone(record['rgb'])
                    self.assertEqual([], record['writes'])
                    for channel in record['channels'].values():
                        self.assertIsNone(channel['value'])
                        self.assertEqual('symbolic-Color.White', channel['source'])
                        self.assertEqual(0, channel['ilOffset'])
                        self.assertEqual('0x0a000001', channel['memberToken'])

    def test_original_rgb_pair_and_alpha_keep_independent_provenance(self):
        records = self.result['records']
        for selector in (1, 13):
            record = records[selector]
            self.assertEqual([11, 23, 47], record['rgb'])
            self.assertEqual(['R', 'G', 'B'], [write['channel'] for write in record['writes']])
            self.assertIsNone(record['channels']['A']['value'])
            self.assertEqual('symbolic-Color.White', record['channels']['A']['source'])
        self.assertEqual(records[1]['channels'], records[13]['channels'])
        self.assertEqual([89, 101, 113], records[30]['rgb'])
        self.assertEqual(127, records[30]['channels']['A']['value'])
        self.assertEqual('explicit-byte-setter', records[30]['channels']['A']['source'])
        for selector in (1, 13, 30):
            self.assertEqual('current-paint', records[selector]['classification'])
            for write in records[selector]['writes']:
                offset = self.offsets['code'] + write['ilOffset']
                self.assertEqual(0x28, self.data[offset])
                token = struct.unpack_from('<I', self.data, offset + 1)[0]
                self.assertEqual(SETTER_TOKENS[write['channel']], token)
                self.assertEqual(write['value'], records[selector]['channels'][write['channel']]['value'])
        self.assertEqual('no-paint-selector', records[0]['classification'])
        self.assertEqual('legacy-selector-not-current-paint', records[31]['classification'])
        self.assertEqual('unsupported-selector', records[255]['classification'])

    def test_report_proves_only_inputs_and_records_actual_offsets(self):
        result = self.result
        self.assertTrue(result['privateOnly'])
        self.assertFalse(result['executedInput'])
        self.assertEqual('int32', result['argumentDomain']['methodParameter'])
        self.assertEqual((0, 255), (result['argumentDomain']['evaluatedMin'],
                                    result['argumentDomain']['evaluatedMax']))
        self.assertEqual('incomplete', result['scope']['paintsFamily'])
        for name in ('mapColorTransforms', 'texturedPaintRendering', 'coatings', 'localizedNames'):
            self.assertEqual('unsupported', result['scope'][name])
        method = result['method']
        self.assertEqual(self.offsets['method_header'], method['bodyOffset'])
        self.assertEqual(self.offsets['code'], method['codeOffset'])
        self.assertEqual(hashlib.sha256(paint_input_code()).hexdigest(), method['ilSha256'])
        self.assertFalse(method['exceptionRegions'])
        self.assertEqual(hashlib.sha256(self.data).hexdigest(), result['inputSha256'])

    def test_current_domain_mismatch_is_fatal_after_evaluation(self):
        for current_ids in ((), (1, 13), (1, 13, 30, 31), (13, 1, 30)):
            with self.subTest(current_ids=current_ids):
                with self.assertRaisesRegex(paint.PaintInputUnsupported, 'CURRENT_DOMAIN_PROFILE_MISMATCH'):
                    extract_fixture(self.data, current_ids=current_ids)

    def test_value_copy_does_not_mutate_a_previously_loaded_color(self):
        # Hold an old local value while setting R, then restore that old value.
        code = call() + b'\x0a\x06' + setter('R', 41) + b'\x0a\x06\x2a'
        result = extract_fixture(code=code, max_stack=3, current_ids=())
        for record in result['records']:
            self.assertIsNone(record['channels']['R']['value'])
            self.assertEqual([41], [write['value'] for write in record['writes']])

    def test_returned_snapshot_keeps_its_original_channels_after_local_mutation(self):
        code = call() + b'\x0a\x06' + setter('R', 41) + b'\x2a'
        result = extract_fixture(code=code, max_stack=3, current_ids=())
        for record in result['records']:
            self.assertTrue(all(channel['value'] is None for channel in record['channels'].values()))
            self.assertEqual([41], [write['value'] for write in record['writes']])

    def test_resetting_color_does_not_leak_overwritten_rgb_but_keeps_write_cost(self):
        plain = call() + b'\x0a\x06\x2a'
        reset = call() + b'\x0a'
        for channel, value in zip('RGB', (17, 29, 53)):
            reset += setter(channel, value)
        reset += call() + b'\x0a\x06\x2a'
        costs = []
        for code in (plain, reset):
            data = paint_input_pe(code=code)
            budget = paint._Budget(paint.PaintInputLimits())
            result = paint._extract_bytes(data, profile_for(data, current_ids=()), budget)
            costs.append(dict(budget.used))
            for record in result['records']:
                self.assertFalse(record['explicitRgb'])
                self.assertIsNone(record['rgb'])
                self.assertTrue(all(channel['value'] is None for channel in record['channels'].values()))
        self.assertEqual([17, 29, 53], [write['value'] for write in result['records'][0]['writes']])
        self.assertGreater(costs[1]['evidence_bytes'], costs[0]['evidence_bytes'])
        self.assertGreater(costs[1]['total_steps'], costs[0]['total_steps'])

    def test_supported_integer_encodings_preserve_byte_boundary_values(self):
        # Every selector has a synthetic explicit RGB triple; input is still int32.
        for encoded, expected in ((b'\x16', 0), (b'\x1e', 8), (b'\x1f\x7f', 127), (ldc(255), 255)):
            with self.subTest(expected=expected):
                code = call() + b'\x0a'
                for channel in 'RGB':
                    code += b'\x12\x00' + encoded + call(SETTER_TOKENS[channel])
                code += b'\x06\x2a'
                result = extract_fixture(code=code, current_ids=tuple(range(256)))
                self.assertTrue(all(record['rgb'] == [expected] * 3 for record in result['records']))


class PaintInputIdentityTests(unittest.TestCase):
    def assert_unsupported_fixture(self, **fixture):
        with self.assertRaises(paint.PaintInputUnsupported):
            extract_fixture(**fixture)

    def test_assembly_identity_is_exact(self):
        changes = (
            {'assembly_name': 'OriginalPaintFixture.Lookalike'},
            {'assembly_version': (1, 0, 0, 1)}, {'assembly_flags': 1},
            {'assembly_public_key': b'original-test-key'}, {'assembly_culture': 'en'},
            {'assembly_hash_algorithm': 0},
        )
        for change in changes:
            with self.subTest(change=change):
                self.assert_unsupported_fixture(**change)

    def test_owner_and_color_type_identity_are_exact(self):
        changes = (
            {'owner_name': 'WorldGenCopy'}, {'owner_ns': 'Original'},
            {'owner_flags': 0x100000}, {'duplicate_owner': True},
            {'owner_extends': 5},
            {'type_name': 'ColorCopy'}, {'type_ns': 'Original.Framework'},
            {'scope': 0}, {'scope': 4}, {'scope': 7},
        )
        for change in changes:
            with self.subTest(change=change):
                self.assert_unsupported_fixture(**change)

    def test_color_assembly_binding_is_exact(self):
        changes = (
            {'assembly_ref_name': 'Original.Framework'},
            {'assembly_ref_version': (4, 0, 0, 1)},
            {'assembly_ref_token': b'original'}, {'assembly_ref_token': b''},
            {'assembly_ref_culture': 'en'}, {'assembly_ref_flags': 1},
            {'assembly_ref_hash': b'original-hash'},
        )
        for change in changes:
            with self.subTest(change=change):
                self.assert_unsupported_fixture(**change)

    def test_exact_static_method_identity_and_signature(self):
        changes = (
            {'method_name': 'PaintColor'}, {'duplicate_method': True},
            {'method_flags': 0x86}, {'method_flags': 0x96 | 0x2000},
            {'impl_flags': 1}, {'no_body': True},
            {'method_signature': b'\x20\x01\x11\x05\x08'},  # instance
            {'method_signature': b'\x00\x01\x11\x05\x05'},  # byte, not int32
            {'method_signature': b'\x00\x01\x11\x05\x09'},  # uint32
            {'method_signature': b'\x00\x01\x12\x05\x08'},  # class return
            {'method_signature': b'\x00\x00\x11\x05'},
            {'method_signature': b'\x00\x01\x11\x05\x08\x00'},
            {'method_signature': b'\x10\x00\x01\x11\x05\x08'},
        )
        for change in changes:
            with self.subTest(change=change):
                self.assert_unsupported_fixture(**change)

    def test_member_owner_name_and_signatures_are_exact(self):
        names = ['get_White', 'set_R', 'set_G', 'set_B', 'set_A']
        signatures = [b'\x00\x00\x11\x05'] + [b'\x20\x01\x01\x05'] * 4
        for index in range(5):
            with self.subTest(index=index, change='owner'):
                parents = [9] * 5
                parents[index] = 8  # TypeDef, even with the same row number.
                self.assert_unsupported_fixture(member_parents=parents)
            with self.subTest(index=index, change='name'):
                renamed = list(names)
                renamed[index] += '_lookalike'
                self.assert_unsupported_fixture(member_names=renamed)
            with self.subTest(index=index, change='signature'):
                changed = list(signatures)
                changed[index] += b'\x00'
                self.assert_unsupported_fixture(member_signatures=changed)
        for white in (b'\x20\x00\x11\x05', b'\x00\x00\x12\x05', b'\x00\x00\x11\x09'):
            self.assert_unsupported_fixture(member_signatures=[white] + signatures[1:])
        for setter_sig in (b'\x00\x01\x01\x05', b'\x20\x01\x01\x08', b'\x20\x01\x08\x05'):
            self.assert_unsupported_fixture(member_signatures=[signatures[0]] + [setter_sig] * 4)

    def test_headers_exception_sections_and_local_signatures_fail_closed(self):
        changes = (
            {'header_flags': 0x301b},  # MoreSects/EH
            {'header_flags': 0x3003},  # no InitLocals
            {'header_flags': 0x2013}, {'header_flags': 2},
            {'max_stack': 0}, {'local_token': 0},
            {'local_token': 0x01000001},
            {'local_signature': b'\x07\x01\x11\x05'},
            {'local_signature': b'\x07\x02\x11\x05\x05'},
            {'local_signature': b'\x07\x02\x12\x05\x08'},
            {'local_signature': b'\x07\x02\x45\x11\x05\x08'},
            {'local_signature': b'\x07\x02\x11\x05\x08\x00'},
            {'code': b''},
        )
        for change in changes:
            with self.subTest(change=change):
                self.assert_unsupported_fixture(**change)

    def test_missing_or_out_of_range_metadata_rows_fail_fatally(self):
        for change in ({'scope': 10}, {'local_signature': None},
                       {'local_token': 0x11000000}, {'local_token': 0x11000002}):
            with self.subTest(change=change):
                with self.assertRaises(PipelineError):
                    extract_fixture(**change)

    def test_malformed_pe_and_metadata_are_never_optional_successes(self):
        data, offsets = paint_input_pe(with_offsets=True)
        variants = [b'', b'MZ', data[:100], data[:-1]]
        for position, value in ((0, b'NO'), (0x80, b'NOPE'),
                                (offsets['metadata'], b'BAD!'),
                                (offsets['streams']['#~'] + 6, b'\xff'),
                                (offsets['method_header'] + 4, struct.pack('<I', 999999))):
            changed = bytearray(data)
            changed[position:position + len(value)] = value
            variants.append(bytes(changed))
        for index, changed in enumerate(variants):
            with self.subTest(variant=index):
                with self.assertRaises(PipelineError):
                    extract_fixture(changed)


class PaintInputControlFlowTests(unittest.TestCase):
    def assert_bad_code(self, code, reason=None, **fixture):
        context = (self.assertRaisesRegex(paint.PaintInputUnsupported, reason) if reason else
                   self.assertRaises(paint.PaintInputUnsupported))
        with context:
            extract_fixture(code=code, **fixture)

    def test_unknown_unvisited_opcodes_and_calls_are_rejected(self):
        early_return = call() + b'\x2a'
        for suffix in (b'\xff', b'\x00', b'\xfe\x01', b'\x2b\x00',
                       call(0x06000001), call(0x2b000001)):
            with self.subTest(suffix=suffix.hex()):
                self.assert_bad_code(early_return + suffix)
        with self.assertRaises(PipelineError):
            extract_fixture(code=early_return + call(0x0a000063))
        self.assert_bad_code(
            early_return + call(0x0a000006),
            member_names=('get_White', 'set_R', 'set_G', 'set_B', 'set_A', 'original_unknown_call'),
            member_signatures=(b'\x00\x00\x11\x05',) + (b'\x20\x01\x01\x05',) * 5,
            member_parents=(9,) * 6)

    def test_truncated_operands_are_rejected_even_after_return(self):
        for suffix in (b'\x12', b'\x1f', b'\x20\x00', b'\x28\x01\x00',
                       b'\x2e', b'\x33', b'\xfe'):
            with self.subTest(suffix=suffix.hex()):
                self.assert_bad_code(call() + b'\x2a' + suffix)

    def test_branches_require_forward_instruction_boundaries(self):
        # Offsets: integers 0/1, branch 2..3, White call 4..8, ret 9.
        for displacement in (-4, -2, 1, 6, 127):
            with self.subTest(displacement=displacement):
                self.assert_bad_code(b'\x16\x16\x2e' + struct.pack('<b', displacement) + call() + b'\x2a')
        # A malformed branch is rejected even though an earlier return hides it.
        self.assert_bad_code(call() + b'\x2a\x16\x16\x2e\xfe')

    def test_branch_cannot_enter_constant_or_call_inside_setter(self):
        for target in ('value', 'setter'):
            with self.subTest(target=target):
                code = BranchEmitter().emit(call(), b'\x0a\x16\x16')
                code.branch('beq.s', target).emit(b'\x12\x00')
                code.label('value').emit(ldc(42)).label('setter')
                code.emit(call(SETTER_TOKENS['R']), b'\x06\x2a')
                self.assert_bad_code(code.finish(), 'BRANCH_INTO_SETTER')

    def test_uninitialized_locals_stack_underflow_and_invalid_returns(self):
        cases = (
            (b'\x2a', 'STACK_UNDERFLOW'), (b'\x0a', 'STACK_UNDERFLOW'),
            (b'\x06\x2a', 'UNINITIALIZED_LOCAL'),
            (b'\x07\x2a', 'UNINITIALIZED_LOCAL'),
            (b'\x16\x2a', 'INVALID_RETURN'),
            (call() + call() + b'\x2a', 'INVALID_RETURN'),
            (call(), 'MISSING_RETURN'),
            (b'\x16\x0a', 'LOCAL_VALUE_TYPE_MISMATCH'),
            (call() + b'\x0b', 'LOCAL_VALUE_TYPE_MISMATCH'),
            (call() + b'\x16\x2e\x00\x2a', 'COMPARE_TYPE_MISMATCH'),
            (call(SETTER_TOKENS['R']), 'STACK_UNDERFLOW'),
            (b'\x16\x17' + call(SETTER_TOKENS['R']), 'INVALID_OR_STALE_ADDRESS'),
        )
        for code, reason in cases:
            with self.subTest(reason=reason, code=code.hex()):
                self.assert_bad_code(code, reason)

    def test_declared_stack_limit_is_enforced_separately_from_global_cap(self):
        self.assert_bad_code(paint_input_code(), 'DECLARED_MAX_STACK_EXCEEDED', max_stack=1)

    def test_address_must_be_initialized_local_zero_and_short_lived(self):
        initialized = call() + b'\x0a'
        cases = (
            setter('R', 1) + b'\x06\x2a',
            initialized + setter('R', 1, local=1) + b'\x06\x2a',
            initialized + b'\x12\x00',
            initialized + b'\x12\x00\x06' + call(SETTER_TOKENS['R']),
            initialized + b'\x12\x00' + ldc(1) + call(),
            initialized + b'\x12\x00' + ldc(1) + b'\x0b\x06\x2a',
            initialized + b'\x12\x00' + call() + b'\x0a' + ldc(1) + call(SETTER_TOKENS['R']),
        )
        for code in cases:
            with self.subTest(code=code.hex()):
                self.assert_bad_code(code)

    def test_setters_never_truncate_out_of_range_or_signed_values(self):
        for encoded in (b'\x15', b'\x1f\xff', ldc(-1), ldc(256), ldc(0x7fffffff), ldc(-0x80000000)):
            code = call() + b'\x0a\x12\x00' + encoded + call(SETTER_TOKENS['R']) + b'\x06\x2a'
            with self.subTest(encoded=encoded.hex()):
                self.assert_bad_code(code, 'SETTER_BYTE_RANGE')

    def test_runtime_rejects_live_pointer_reassignment_and_branch_escape(self):
        prefix = call() + b'\x0a\x12\x00'
        # These bypass structural validation to exercise the second line of defense.
        with self.assertRaisesRegex(paint.PaintInputUnsupported, 'LIVE_ADDRESS_REASSIGNMENT'):
            runtime(prefix + call() + b'\x0a' + ldc(1) + call(SETTER_TOKENS['R']) + b'\x06\x2a')
        with self.assertRaisesRegex(paint.PaintInputUnsupported, 'ADDRESS_ACROSS_BRANCH'):
            runtime(prefix + b'\x16\x16\x2e\x00\x06\x2a')
        with self.assertRaisesRegex(paint.PaintInputUnsupported, 'INVALID_OR_STALE_ADDRESS'):
            runtime(prefix + ldc(1) + b'\x0b' + ldc(2) + call(SETTER_TOKENS['R']) + b'\x06\x2a')

    def test_runtime_argument_domain_rejects_bool_and_non_byte_integers(self):
        for argument in (-1, 256, True, False, 1.0, None):
            with self.subTest(argument=argument):
                with self.assertRaisesRegex(paint.PaintInputUnsupported, 'ARGUMENT_OUTSIDE_BYTE_DOMAIN'):
                    runtime(call() + b'\x2a', argument=argument)

    def test_a_failing_last_selector_prevents_partial_success(self):
        code = BranchEmitter().emit(b'\x02', ldc(255)).branch('beq.s', 'bad')
        code.emit(call(), b'\x2a').label('bad').emit(b'\x2a')
        visited = []
        original = paint._run_path

        def observe(instructions, argument, image, max_stack):
            visited.append(argument)
            return original(instructions, argument, image, max_stack)

        with mock.patch.object(paint, '_run_path', side_effect=observe):
            self.assert_bad_code(code.finish(), 'STACK_UNDERFLOW', current_ids=())
        self.assertEqual(list(range(256)), visited)


class PaintInputBudgetTests(unittest.TestCase):
    def test_limits_reject_invalid_integer_and_time_values(self):
        for name in vars(paint.PaintInputLimits()):
            values = (0, -1, True, '1', float('nan'), float('inf'))
            if name != 'wall_seconds':
                values += (1.5,)
            else:
                values += (121,)
            for value in values:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        replace(paint.PaintInputLimits(), **{name: value})

    def test_module_owned_caps_raise_budget_errors(self):
        data = paint_input_pe()
        cases = (
            ('input_bytes', len(data) - 1, 'INPUT_BYTES'),
            ('signature_bytes', 1, 'SIGNATURE_BYTES'),
            ('method_bytes', len(paint_input_code()) - 1, 'METHOD_BYTES'),
            ('total_method_bytes', len(paint_input_code()) - 1, 'TOTAL_METHOD_BYTES'),
            ('instructions', 1, 'INSTRUCTIONS'), ('path_steps', 1, 'PATH_STEPS'),
            ('total_steps', 1, 'TOTAL_STEPS'), ('work', 1, 'WORK'),
            ('stack', 1, 'STACK'), ('locals', 1, 'LOCALS'),
            ('evidence_bytes', 1, 'EVIDENCE_BYTES'),
        )
        for name, value, reason in cases:
            with self.subTest(name=name):
                limits = replace(paint.PaintInputLimits(), **{name: value})
                with self.assertRaisesRegex(paint.PaintInputBudgetExceeded, reason + '_LIMIT'):
                    extract_fixture(data, limits=limits)

    def test_shared_metadata_caps_remain_fatal(self):
        for name in ('metadata_bytes', 'metadata_rows', 'metadata_names_bytes'):
            with self.subTest(name=name):
                limits = replace(paint.PaintInputLimits(), **{name: 1})
                with self.assertRaises(PipelineError):
                    extract_fixture(limits=limits)

    def test_rejected_method_candidates_charge_cumulative_method_count(self):
        limits = replace(paint.PaintInputLimits(), methods=1)
        with self.assertRaisesRegex(paint.PaintInputBudgetExceeded, 'METHODS_LIMIT'):
            extract_fixture(limits=limits, duplicate_method=True)

    def test_method_byte_and_instruction_counts_do_not_reset_between_scans(self):
        data = paint_input_pe()
        code_size = len(paint_input_code())
        for name in ('total_method_bytes', 'instructions'):
            with self.subTest(name=name):
                count = code_size if name == 'total_method_bytes' else len(paint._decode(paint_input_code(), paint._Budget(paint.PaintInputLimits())))
                limits = replace(paint.PaintInputLimits(), **{name: count * 2 - 1})
                budget = paint._Budget(limits)
                image = paint._Image(data, budget, profile_for(data))
                image.method()
                with self.assertRaisesRegex(paint.PaintInputBudgetExceeded, name.upper() + '_LIMIT'):
                    image.method()

    def test_failed_decode_attempts_still_consume_instruction_and_work_budgets(self):
        budget = paint._Budget(replace(paint.PaintInputLimits(), instructions=2))
        for code in (b'\xff', b'\x20\x00'):
            before_work = budget.used['work']
            with self.assertRaises(paint.PaintInputUnsupported):
                paint._decode(code, budget)
            self.assertGreater(budget.used['work'], before_work)
        self.assertEqual(2, budget.used['instructions'])
        with self.assertRaisesRegex(paint.PaintInputBudgetExceeded, 'INSTRUCTIONS_LIMIT'):
            paint._decode(b'\x16', budget)

    def test_unsuccessful_runtime_paths_keep_step_cost(self):
        limits = replace(paint.PaintInputLimits(), total_steps=2)
        budget = paint._Budget(limits)
        instructions = paint._decode(b'\x2a', budget)
        image = SimpleNamespace(budget=budget, members={})
        for argument in (0, 1):
            with self.assertRaisesRegex(paint.PaintInputUnsupported, 'STACK_UNDERFLOW'):
                paint._run_path(instructions, argument, image, 2)
        self.assertEqual(2, budget.used['total_steps'])
        with self.assertRaisesRegex(paint.PaintInputBudgetExceeded, 'TOTAL_STEPS_LIMIT'):
            paint._run_path(instructions, 2, image, 2)

    def test_full_result_serialization_counts_against_cumulative_evidence(self):
        data = paint_input_pe()
        budget = paint._Budget(paint.PaintInputLimits())
        result = paint._extract_bytes(data, profile_for(data), budget)
        final_size = len(canonical_json(result))
        self.assertGreater(budget.used['evidence_bytes'], final_size)
        # Leave enough room for each retained entry or the final envelope alone,
        # but not both. The envelope also describes the configured limits, so
        # its size changes slightly when this limit changes.
        limit = (budget.used['evidence_bytes'] + final_size) // 2
        with self.assertRaisesRegex(paint.PaintInputBudgetExceeded, 'EVIDENCE_BYTES_LIMIT'):
            extract_fixture(data, limits=replace(paint.PaintInputLimits(), evidence_bytes=limit))

    def test_evidence_exact_boundary_includes_retained_and_final_json_bytes(self):
        retained = {'original': [1, 2, 3]}
        envelope = {'records': [retained]}
        expected = len(canonical_json(retained)) + len(canonical_json(envelope))
        for cap in (expected - 1, expected):
            with self.subTest(cap=cap):
                budget = paint._Budget(replace(paint.PaintInputLimits(), evidence_bytes=cap))
                budget.evidence(retained)
                if cap < expected:
                    with self.assertRaisesRegex(paint.PaintInputBudgetExceeded, 'EVIDENCE_BYTES_LIMIT'):
                        budget.final_json(envelope)
                else:
                    self.assertEqual(canonical_json(envelope), budget.final_json(envelope))
                    self.assertEqual(expected, budget.used['evidence_bytes'])

    def test_total_step_limit_is_cumulative_across_all_selectors(self):
        data = paint_input_pe()
        budget = paint._Budget(paint.PaintInputLimits())
        result = paint._extract_bytes(data, profile_for(data), budget)
        expected = sum(record['steps'] for record in result['records'])
        self.assertEqual(expected, budget.used['total_steps'])
        limits = replace(paint.PaintInputLimits(), total_steps=expected - 1)
        with self.assertRaisesRegex(paint.PaintInputBudgetExceeded, 'TOTAL_STEPS_LIMIT'):
            extract_fixture(data, limits=limits)

    def test_mock_clock_deadline_including_equality_is_fatal(self):
        now = [10.0]
        with mock.patch.object(paint.time, 'monotonic', side_effect=lambda: now[0]):
            budget = paint._Budget(replace(paint.PaintInputLimits(), wall_seconds=0.5))
            budget.check()
            now[0] = 10.5
            with self.assertRaisesRegex(paint.PaintInputBudgetExceeded, 'TIME_LIMIT'):
                budget.check()

    def test_time_is_rechecked_after_final_json_allocation(self):
        now = [0.0]
        original = paint.canonical_json

        def encode(value):
            encoded = original(value)
            now[0] = 1.0
            return encoded

        with mock.patch.object(paint.time, 'monotonic', side_effect=lambda: now[0]):
            budget = paint._Budget(replace(paint.PaintInputLimits(), wall_seconds=1))
            with mock.patch.object(paint, 'canonical_json', side_effect=encode):
                with self.assertRaisesRegex(paint.PaintInputBudgetExceeded, 'TIME_LIMIT'):
                    budget.final_json({'original': 'fixture'})

    def test_checkpoint_exception_identity_survives_each_processing_stage(self):
        sentinels = (RuntimeError('cancel'), ValueError('cancel'), PipelineError('cancel'),
                     ILUnsupported('CANCELED'), EvidenceSizeLimit('cancel'))
        for sentinel in sentinels:
            for stage in ('core', 'metadata', 'decode', 'runtime', 'evidence', 'final'):
                with self.subTest(exception=type(sentinel).__name__, stage=stage):
                    active = [False]

                    def checkpoint():
                        if active[0]:
                            raise sentinel

                    budget = paint._Budget(paint.PaintInputLimits(), checkpoint)
                    data = paint_input_pe()
                    image = SimpleNamespace(budget=budget, members={WHITE_TOKEN: 'White'})
                    instructions = paint._decode(call() + b'\x2a', budget)
                    active[0] = True
                    actions = {
                        'core': lambda: paint._extract_bytes(data, profile_for(data), budget),
                        'metadata': lambda: paint._Image(data, budget, profile_for(data)),
                        'decode': lambda: paint._decode(b'\x16', budget),
                        'runtime': lambda: paint._run_path(instructions, 0, image, 2),
                        'evidence': lambda: budget.evidence({'fixture': [1, 2, 3]}),
                        'final': lambda: budget.final_json({'fixture': [1, 2, 3]}),
                    }
                    with self.assertRaises(type(sentinel)) as caught:
                        actions[stage]()
                    self.assertIs(sentinel, caught.exception)


if __name__ == '__main__':
    unittest.main()
