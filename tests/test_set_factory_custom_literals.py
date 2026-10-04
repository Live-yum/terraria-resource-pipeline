"""Original synthetic generic conversion IL; no proprietary input bytes."""
import struct
import unittest
from dataclasses import replace
from unittest.mock import patch

from dispatch_fixture import dispatch_pe, tok, ldc
from test_set_factory_lifecycle import proof
from resource_pipeline.item_texture_aliases import _Program, _Budget, _CheckpointCancelled
from resource_pipeline.item_dispatch_sets import ItemDispatchSetLimits
from resource_pipeline.set_factory_custom_literals import INTEGRAL
from resource_pipeline.static_il import ILUnsupported

NULLABLE = b'\x15\x11\x2d\x01\x02'
TYPEOF = tok(0x28,0x0a000004)
GENERIC = 0x1b000001
RECEIVER = tok(0x7e,0x04000002)


def method_code():
    # Independent tiny assembler, with labels and explicit branch widths.
    parts=[]; labels={}; fixups=[]
    def raw(b): parts.extend(b)
    def op(n): raw(bytes((n,)))
    def token(n,t): raw(tok(n,t))
    def mark(s): labels[s]=len(parts)
    def branch(n,s):
        op(n); fixups.append((len(parts),1 if n in (0x2b,0x2c,0x32) else 4,s))
        raw(bytes(fixups[-1][1]))
    raw(b'\x04\x8e\x69\x18\x5d'); branch(0x2c,'alloc')
    token(0x72,0x70000001);token(0x73,0x0a000003);op(0x7a)
    mark('alloc');op(2);token(0x7b,0x04000007);token(0x8d,GENERIC);raw(b'\x0a\x16\x0b');branch(0x2b,'fillcheck')
    mark('fill');raw(b'\x06\x07\x03');token(0xa4,GENERIC);raw(b'\x07\x17\x58\x0b')
    mark('fillcheck');raw(b'\x07\x06\x8e\x69');branch(0x32,'fill');op(4);branch(0x39,'ret')
    raw(b'\x16\x0c');branch(0x38,'check')
    mark('pair');token(0xd0,GENERIC);raw(TYPEOF);token(0x28,0x0a000005);branch(0x2c,'generic')
    def payload():
        raw(b'\x04\x08\x17\x58\x9a');token(0xa5,GENERIC);op(0x0d);branch(0x2b,'key')
    payload();mark('generic');token(0xd0,GENERIC);raw(TYPEOF);token(0x6f,0x0a000006);branch(0x2c,'class')
    token(0xd0,GENERIC);raw(TYPEOF);token(0x6f,0x0a000007);token(0xd0,0x0100000b);raw(TYPEOF)
    token(0x28,0x0a000008);branch(0x2c,'class');payload()
    mark('class');token(0xd0,GENERIC);raw(TYPEOF);token(0x28,0x0a000009);branch(0x2c,'convert');payload()
    mark('convert');raw(b'\x04\x08\x17\x58\x9a');token(0xd0,GENERIC);raw(TYPEOF)
    token(0x28,0x0a00000a);token(0xa5,GENERIC);op(0x0d)
    mark('key');raw(b'\x04\x08\x9a');token(0x75,0x0100000e);branch(0x2c,'int')
    def store(box):
        raw(b'\x06\x04\x08\x9a');token(0xa5,box);op(9);token(0xa4,GENERIC)
    store(0x0100000e);branch(0x2b,'next')
    mark('int');raw(b'\x04\x08\x9a');token(0x75,0x01000002);branch(0x2c,'short')
    store(0x01000002);branch(0x2b,'next');mark('short');store(0x0100000d)
    mark('next');raw(b'\x08\x18\x58\x0c');mark('check');raw(b'\x08\x04\x8e\x69');branch(0x3f,'pair')
    mark('ret');raw(b'\x06\x2a')
    result=bytearray(parts)
    for at,size,label in fixups:result[at:at+size]=int(labels[label]-at-size).to_bytes(size,'little',signed=True)
    return bytes(result)


def caller(pairs=((3,1),(7,0)), *, box=0x01000003, keybox=0x0100000d, nullable=True, entries=None):
    default=b'\x12\x01\xfe\x15'+struct.pack('<I',0x1b000002)+b'\x07' if nullable else ldc(0)
    result=RECEIVER+default+ldc(len(pairs)*2)+tok(0x8d,0x01000001)
    entries = [(i*2,key,keybox) for i,(key,val) in enumerate(pairs)] + [(i*2+1,val,box) for i,(key,val) in enumerate(pairs)] if entries is None else entries
    for index,value,typ in entries: result+=b'\x25'+ldc(index)+ldc(value)+tok(0x8c,typ)+b'\xa2'
    return result+tok(0x6f,0x2b000002)+tok(0x80,0x04000003)


def fixture(code=None, *, nullable=True, mutate=lambda b:b, members=None, **opts):
    t=b'\x12\x25'
    refs=[(9<<3|1,'GetTypeFromHandle',b'\x00\x01'+t+b'\x11\x29'),
          (9<<3|1,'get_IsPrimitive',b'\x20\x00\x02'),(9<<3|1,'get_IsGenericType',b'\x20\x00\x02'),
          (9<<3|1,'GetGenericTypeDefinition',b'\x20\x00'+t),
          (9<<3|1,'op_Equality',b'\x00\x02\x02'+t*2),
          (9<<3|1,'get_IsClass',b'\x20\x00\x02'),(12<<3|1,'ChangeType',b'\x00\x02\x1c\x1c'+t)]
    typ=NULLABLE if nullable else b'\x02'
    options=dict(custom_code=mutate(method_code()),custom_spec=b'\x0a\x01\x08',
        extra_type_refs=['Type','RuntimeTypeHandle','Nullable`1','Convert','Int16','UInt16'],
        extra_members=refs if members is None else members,
        extra_type_specs=[NULLABLE],extra_method_specs=[(14,b'\x0a\x01'+typ)],
        sets_locals=b'\x07\x02\x08'+NULLABLE,field_signatures={3:b'\x06\x1d'+typ})
    options.update(opts)
    head=tok(0x7e,0x04000001)+tok(0x73,0x06000003)+tok(0x80,0x04000002)
    first=RECEIVER+ldc(0)+ldc(0)+tok(0x8d,0x01000001)+tok(0x6f,0x2b000001)+b'\x26'
    options['sets_code']=head+first+(caller(nullable=nullable) if code is None else code)+b'\x2a'
    return dispatch_pe(**options)


def program(data):return _Program(data,_Budget(ItemDispatchSetLimits(),None))


class LiteralCustomTests(unittest.TestCase):
    def test_nullable_bool_defaults_pairs_and_ordered_duplicates(self):
        result=proof(program(fixture(caller(((3,1),(3,0))))))
        self.assertEqual('end of supported initializer',result['stop']['reason'])
        self.assertEqual(1,len(result['dischargedCalls']))
        record=result['allocationsAndStores'][0]
        self.assertEqual(None,record['defaultValue'])
        self.assertEqual([[3,1],[3,0]],record['orderedOverrides'])
        self.assertTrue(record['genericInstantiationValidityProven'])
        self.assertFalse(record['factoryOrPoolWrites']);self.assertFalse(result['runtimeSnapshotUsable'])

    def test_supported_integral_and_nullable_extrema(self):
        refs=['Type','RuntimeTypeHandle','Nullable`1','Convert','Int16','UInt16','Char','SByte','Byte']
        tokens={2:0x01000003,3:0x0100000f,4:0x01000010,5:0x01000011,
                6:0x0100000d,7:0x0100000e,8:0x01000002}
        for kind,(name,lo,hi) in INTEGRAL.items():
            for nullable in (False,True):
                typ=NULLABLE[:-1]+bytes([kind]) if nullable else bytes([kind])
                for value in {lo,hi}:
                    with self.subTest(kind=name,nullable=nullable,value=value):
                        data=fixture(caller(((3,value),),nullable=nullable,box=tokens[kind]),nullable=nullable,
                            extra_type_refs=refs,extra_type_specs=[typ if nullable else NULLABLE],
                            extra_method_specs=[(14,b'\x0a\x01'+typ)],sets_locals=b'\x07\x02\x08'+typ,
                            field_signatures={3:b'\x06\x1d'+typ})
                        result=proof(program(data))
                        self.assertEqual('end of supported initializer',result['stop']['reason'])
                        self.assertEqual([[3,value]],result['allocationsAndStores'][0]['orderedOverrides'])

    def test_exact_primitive_and_all_three_key_boxes(self):
        for keybox in (0x0100000d,0x0100000e,0x01000002):
            result=proof(program(fixture(caller(nullable=False,keybox=keybox),nullable=False)))
            self.assertEqual('end of supported initializer',result['stop']['reason'])
            self.assertEqual(0,result['allocationsAndStores'][0]['defaultValue'])

    def reject(self,data):
        try:r=proof(program(data))
        except ILUnsupported:return
        self.assertEqual([],r['dischargedCalls']);self.assertEqual([],r['allocationsAndStores'])

    def test_wrong_box_range_and_payload_are_not_converted(self):
        for code in (caller(box=0x01000002),caller(keybox=0x01000003),caller(((3,2),)),
                     caller(((-1,1),)),caller(((32768,1),))):
            self.reject(fixture(code))

    def test_unknown_nullable_enum_class_and_generic_constraint_refused(self):
        for typ in (b'\x1e\x00',b'\x12\x25',b'\x11\x2c',NULLABLE[:-1]+b'\x1c',
                    NULLABLE+b'\x02',b'\x15\x11\x2d\x02\x02\x02'):
            self.reject(fixture(extra_method_specs=[(14,b'\x0a\x01'+typ)]))
        self.reject(fixture(custom_generic=(0,4,15)))
        self.reject(fixture(extra_method_specs=[(12,b'\x0a\x01'+NULLABLE)]))

    def test_initobj_local_and_publication_bind_exact_type(self):
        good=caller()
        for code in (good.replace(b'\x12\x01',b'\x12\x00'),
                     good.replace(b'\xfe\x15\x02\x00\x00\x1b',b'\xfe\x15\x01\x00\x00\x1b'),
                     good.replace(b'\x07'+ldc(4),b'\x06'+ldc(4))):self.reject(fixture(code))
        self.reject(fixture(sets_locals=b'\x07\x02\x08\x10'+NULLABLE))
        self.reject(fixture(field_signatures={3:b'\x06\x1d\x02'}))

    def test_missing_alias_and_invalid_array_store_refused(self):
        for entries in ([(0,3,0x0100000d)],[(4,3,0x0100000d)],
                        [(0,3,0x0100000d),(0,1,0x01000003)]):
            self.reject(fixture(caller(((3,1),),entries=entries)))
        self.reject(fixture(caller().replace(b'\xa2',b'\x26',1)))

    def test_changed_branch_generic_token_and_extra_effect_refused(self):
        for mutate in (lambda b:b.replace(tok(0xa5,GENERIC),tok(0xa5,0x01000003),1),
                       lambda b:b.replace(b'\x2c\x0d',b'\x2c\x00',1),
                       lambda b:b[:-1]+tok(0x28,0x06000008)+b'\x2a',
                       lambda b:b.replace(tok(0xa4,GENERIC),tok(0xa4,0x01000003),1)):
            self.reject(fixture(mutate=mutate))

    def test_core_identity_embedded_signature_and_name_binding(self):
        for names in (['Type','Object','Nullable`1','Convert','Int16','UInt16'],
                      ['Type','RuntimeTypeHandle','FakeNullable`1','Convert','Int16','UInt16']):
            self.reject(fixture(extra_type_refs=names))
        self.reject(fixture(key=b'bad key!'))
        self.reject(fixture(mutate=lambda b:b.replace(TYPEOF,tok(0x28,0x0a000005),1)))

    def test_unknown_call_boundary_keeps_later_calls_residual(self):
        result=proof(program(fixture(caller()+tok(0x28,0x06000008)+caller())))
        self.assertEqual(1,len(result['dischargedCalls']))
        self.assertEqual(1,len(result['allocationsAndStores']))

    def test_repeated_argument_slot_stores_use_final_value(self):
        entries=[(0,3,0x0100000d),(1,1,0x01000003),(1,0,0x01000003)]
        r=proof(program(fixture(caller(((3,1),),entries=entries))))
        self.assertEqual([[3,0]],r['allocationsAndStores'][0]['orderedOverrides'])

    def test_empty_bool_array_after_custom_slice_has_no_call(self):
        array=ldc(0)+tok(0x8d,0x01000003)+tok(0x80,0x04000004)
        r=proof(program(fixture(caller()+array)))
        self.assertEqual(2,len(r['allocationsAndStores']))
        self.assertEqual(1,len(r['dischargedCalls']))
        self.assertEqual('end of supported initializer',r['stop']['reason'])
        r=proof(program(fixture(caller()+array.replace(ldc(0),ldc(1)))))
        self.assertEqual(1,len(r['allocationsAndStores']))
        for bad in (array.replace(tok(0x8d,0x01000003),tok(0x8d,0x01000001)),
                    array.replace(tok(0x80,0x04000004),tok(0x80,0x04000003))):
            r=proof(program(fixture(caller()+bad)))
            self.assertEqual(1,len(r['allocationsAndStores']))

    def test_empty_pairs_still_bind_generic_default(self):
        result=proof(program(fixture(caller(()))))
        self.assertEqual([],result['allocationsAndStores'][0]['orderedOverrides'])

    def test_budget_and_cancellation(self):
        p=program(fixture(caller(((3,1),)*100)))
        p.budget.limits=replace(p.budget.limits,literal_ids=8)
        with self.assertRaises(ILUnsupported):proof(p)
        p=program(fixture());error=RuntimeError('cancel custom')
        with patch.object(p.budget,'external',side_effect=error):
            with self.assertRaises(_CheckpointCancelled) as caught:proof(p)
        self.assertIs(error,caught.exception.original)
