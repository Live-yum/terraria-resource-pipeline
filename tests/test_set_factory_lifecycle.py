"""Original synthetic metadata only; shared-state prefix and refusal tests."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from dispatch_fixture import tok, ldc
from test_set_factory_callsite import raw, HEAD, RECEIVER, CALL
from test_set_factory_empty_custom import fixture
from resource_pipeline.item_texture_aliases import _Program, _Budget, ItemTextureAliasLimits, _CheckpointCancelled
from resource_pipeline.item_dispatch_sets import _bool_factory, extract_item_dispatch_sets, ItemDispatchSetLimits
from resource_pipeline.set_factory_constructor import prove_set_factory_constructor
from resource_pipeline.set_factory_empty_custom import prove_empty_custom_set, _empty_calls
from resource_pipeline.set_factory_callsite import prove_first_custom_call
from resource_pipeline.set_factory_lifecycle import prove_factory_lifecycle_prefix, _bind_getter
from resource_pipeline.static_il import ILUnsupported


LIST = tok(0x73,0x0a000004) + b'\x25' + ldc(5) + tok(0x6f,0x0a000005) + tok(0x80,0x04000003)
BOOL = (RECEIVER + ldc(3) + tok(0x8d,0x01000002)
        + b'\x25' + ldc(1) + ldc(2) + b'\x9e'
        + tok(0x6f,0x06000004) + tok(0x80,0x04000004))


def program(code=None, **opts):
    data = raw(**opts) if code is None else fixture(sets_code=HEAD+RECEIVER+b'\x16'+CALL+code+b'\x2a', **opts)
    return _Program(data, _Budget(ItemDispatchSetLimits(), None))


def proof(p):
    constructor = prove_set_factory_constructor(p)
    custom = prove_empty_custom_set(p); custom['emptyArgumentCalls'] = _empty_calls(p, custom)
    first = prove_first_custom_call(p, constructor, custom)
    methods = _bool_factory(p)[:3]
    return prove_factory_lifecycle_prefix(p,constructor,first,methods)


class FactoryLifecycleTests(unittest.TestCase):
    def test_shared_bool_state_and_argument_overwrite(self):
        p = proof(program(BOOL+BOOL))
        self.assertEqual('PROVEN_FRESH_FACTORY_NORMAL_RETURN_PREFIX',p['status'])
        self.assertEqual(2,len(p['allocationsAndStores']))
        a,b = p['allocationsAndStores']
        self.assertEqual([[0,1],[2,1],[0,1]],a['orderedOverrides'])
        self.assertNotEqual(a['allocationIdentity'],b['allocationIdentity'])
        self.assertEqual('empty',a['queueBefore']);self.assertEqual('empty',b['queueAfter'])
        self.assertFalse(p['wholeInitializerProven']);self.assertFalse(p['runtimeSnapshotUsable'])
        self.assertFalse(p['numericSizeProven']);self.assertFalse(p['normalReturnGuaranteed'])

    def test_fresh_pooled_getter_stays_empty_for_every_supported_call(self):
        p=proof(program(None,pooled=True,fresh_constructor=True))
        self.assertEqual(4,len(p['allocationsAndStores']))
        self.assertEqual([{'fieldToken':'0x04000008','stateAtBoundary':'fresh empty queue',
                          'enqueues':0,'dequeues':0}],p['queues'])
        self.assertTrue(all(a['queueFieldToken']=='0x04000008' for a in p['allocationsAndStores']))

    def test_getter_must_bind_exact_constructor_queue_lock_and_size(self):
        p=program(None,pooled=True,fresh_constructor=True)
        constructor=prove_set_factory_constructor(p)
        getter=_bool_factory(p)[2]
        import copy
        for change in ('queue','lock','size'):
            changed=copy.deepcopy(constructor)
            if change=='queue': changed['caches'][0]['fieldToken']='0x04000007'
            elif change=='lock': changed['lock']['fieldToken']='0x04000007'
            else: changed['sizeFieldToken']='0x04000008'
            with self.assertRaises(ILUnsupported):_bind_getter(p,getter,changed,2)

    def test_list_intrinsic_closes_multiple_calls_without_aliases(self):
        p = proof(program(LIST+BOOL,lifecycle_list=True))
        self.assertEqual(2,len(p['allocationsAndStores']))
        self.assertEqual(3,len(p['dischargedCalls']))
        self.assertEqual([5],p['allocationsAndStores'][0]['values'])
        self.assertFalse(p['allocationsAndStores'][0]['factoryOrPoolReferences'])

    def test_list_name_or_element_is_not_sufficient(self):
        for opts in ({'list_type_name':'FakeList`1'}, {'list_element':0x1c}, {'list_add_name':'Append'}):
            p=proof(program(LIST+BOOL,lifecycle_list=True,**opts))
            self.assertEqual([],p['dischargedCalls'])
            self.assertEqual([],p['allocationsAndStores'])

    def test_unknown_call_stops_and_later_known_call_stays_residual(self):
        unknown = tok(0x28,0x06000008)
        p=proof(program(BOOL+unknown+BOOL))
        self.assertEqual(1,len(p['dischargedCalls']))
        self.assertEqual(1,len(p['allocationsAndStores']))
        self.assertEqual('LIFECYCLE_UNSUPPORTED_EFFECT',p['stop']['reason'])
        self.assertTrue(p['unknownCallsInvalidateContinuation'])

    def test_receiver_mutation_address_and_wrong_publication_stop(self):
        for bad in (RECEIVER+b'\x16'+tok(0x7d,0x04000007), tok(0x7f,0x04000002)+b'\x26',
                    BOOL.replace(tok(0x80,0x04000004),tok(0x80,0x04000001)),
                    BOOL.replace(tok(0x7e,0x04000002),tok(0x7e,0x04000003))):
            p=proof(program(bad+BOOL))
            self.assertEqual([],p['dischargedCalls'])
            self.assertEqual([],p['allocationsAndStores'])

    def test_failed_slice_cannot_discharge_earlier_call_inside_slice(self):
        bad_list=LIST.replace(tok(0x80,0x04000003),tok(0x80,0x04000001))
        p=proof(program(bad_list+BOOL,lifecycle_list=True))
        self.assertEqual([],p['dischargedCalls'])

    def test_rva_original_fixture_composes_but_later_custom_does_not(self):
        p=proof(program())
        self.assertEqual(4,len(p['allocationsAndStores']))
        self.assertEqual(8,len(p['dischargedCalls']))
        self.assertTrue(all(a['argumentArray']['rvaEvidence'] for a in p['allocationsAndStores']))
        self.assertNotEqual('end of supported initializer',p['stop']['reason'])

    def test_invalid_array_index_negative_length_and_changed_callee_stop(self):
        for bad in (BOOL.replace(ldc(1),ldc(3)), BOOL.replace(ldc(3),ldc(-1)),
                    BOOL.replace(tok(0x6f,0x06000004),tok(0x6f,0x06000008))):
            p=proof(program(bad)) if ldc(-1) not in bad else None
            if p is not None:self.assertEqual([],p['dischargedCalls'])
            else:
                with self.assertRaises(ILUnsupported):proof(program(bad))

    def test_conditional_recipes_do_not_become_runtime_snapshots(self):
        result=extract_item_dispatch_sets(raw())
        self.assertEqual('PROVEN_FRESH_FACTORY_NORMAL_RETURN_PREFIX',result['factory']['lifecyclePrefix']['status'])
        for key in ('complete','publishable','runtimeSnapshotUsable','cctorNormalReturnSnapshotUsable'):
            self.assertFalse(result[key])
        self.assertEqual('UNPROVEN_AT_CALL_SITE',result['factory']['bufferLengthStatus'])

    def test_budget_and_cancellation_fail_closed(self):
        p=program(BOOL*100)
        p.budget.limits=replace(p.budget.limits,evidence_bytes=16384)
        with self.assertRaises(ILUnsupported):proof(p)
        p=program(BOOL)
        error=RuntimeError('cancel lifecycle')
        with patch.object(p.budget,'external',side_effect=error):
            with self.assertRaises(_CheckpointCancelled) as caught:proof(p)
        self.assertIs(error,caught.exception.original)
        p=program(BOOL)
        with patch('resource_pipeline.item_texture_aliases.time.monotonic',return_value=p.budget.deadline+1):
            with self.assertRaises(ILUnsupported) as caught:proof(p)
        self.assertEqual('ALIAS_TIME_LIMIT',caught.exception.code)
