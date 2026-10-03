"""Original tiny PE/CLI fixture and adversarial alias-order tests; no real IL/assets."""
from dataclasses import replace
import hashlib
import json
import struct
import unittest
from unittest.mock import patch

from resource_pipeline.item_texture_aliases import ItemTextureAliasLimits, extract_item_texture_aliases


def c(value):
    if value < 128:
        return bytes((value,))
    if value < 16384:
        return bytes((128 | value >> 8, value & 255))
    raise ValueError(value)


def token(op, value):
    return bytes((op,)) + struct.pack('<I', value)


def ldc(value):
    return b'\x20' + struct.pack('<i', value)


class IL:
    """Original synthetic assembler; labels describe control flow, not offsets."""
    def __init__(self):
        self.code = bytearray()
        self.labels = {}
        self.branches = []

    def emit(self, *opcodes):
        self.code.extend(opcodes)
        return self

    def tok(self, op, value):
        self.code.extend(token(op, value))
        return self

    def branch(self, op, label):
        self.branches.append((len(self.code), label))
        return self.emit(op, 0)

    def mark(self, label):
        self.labels[label] = len(self.code)
        return self

    def build(self):
        for pos, label in self.branches:
            self.code[pos + 1] = (self.labels[label] - pos - 2) & 255
        return bytes(self.code)


def fixture(**options):
    strings = bytearray(b'\0')
    blobs = bytearray(b'\0')
    users = bytearray(b'\0')
    def s(value):
        pos = len(strings); strings.extend(value.encode() + b'\0'); return pos
    def b(value):
        pos = len(blobs); blobs.extend(c(len(value)) + value); return pos
    def us(value):
        pos = len(users); raw = value.encode('utf-16le') + b'\0'; users.extend(c(len(raw)) + raw); return 0x70000000 | pos
    error, prefix = us('Synthetic pair length is odd'), us(options.get('prefix', 'Images/Item_'))
    tref_names = [('System','Object',1),('System','Int32',1),('System','Exception',1),('System','String',1),
                  ('System','Array',1),('System','RuntimeFieldHandle',1),('System.Runtime.CompilerServices','RuntimeHelpers',1),
                  ('ReLogic.Content','AssetRequestMode',2),('ReLogic.Content','IAssetRepository',2),
                  ('ReLogic.Content','Asset`1',2),('Microsoft.Xna.Framework.Graphics','Texture2D',3),('System','ValueType',1)]
    pooled = options.get('pooled', False)
    if pooled:
        tref_names += [('System.Threading','Monitor',1),('System.Collections.Generic','Queue`1',1)]
    typerefs = [struct.pack('<HHH', assembly << 2 | 2, s(name), s(ns)) for ns,name,assembly in tref_names]
    asset = b'\x15\x12' + c(10 << 2 | 1) + b'\x01\x12' + c(11 << 2 | 1)
    mode = b'\x11' + c(8 << 2 | 1)
    generic_asset = b'\x15\x12' + c(10 << 2 | 1) + b'\x01\x1e\x00'
    fields = [('Count', b'\x06\x08', 0x16),('Factory', b'\x06\x12'+c(4<<2),0x16),
              ('TextureCopyLoad',b'\x06\x1d\x08',0x16),('_size',b'\x06\x08',6),
              ('Item',b'\x06\x1d'+asset,0x16),('Assets',b'\x06\x12'+c(9<<2|1),0x16),
              ('OriginalPairData',b'\x06\x11'+c(9<<2),0x110)]
    queue_type = b'\x15\x12' + c(14 << 2 | 1) + b'\x01\x1d\x08'
    if pooled:
        fields[4:4] = [('_intBufferCache', b'\x06'+queue_type,6),('_queueLock',b'\x06\x1c',6)]
    field_rows = [struct.pack('<HHH', options.get('field_flags',{}).get(i,flags), s(name),
                              b(options.get('field_signatures',{}).get(i,sig))) for i,(name,sig,flags) in enumerate(fields,1)]
    count = options.get('count', 8)
    pairs = options.get('pairs', [(2,0),(4,2),(5,1)])
    data = b''.join(struct.pack('<ii',*pair) for pair in pairs)
    getter_eh = b''
    ctor = b'\x02\x03'+token(0x7d,0x04000004)+b'\x2a'
    getter = b'\x02'+token(0x7b,0x04000004)+token(0x8d,0x01000002)+b'\x2a'
    if pooled:
        g = IL().emit(2).tok(0x7b,0x04000006).emit(0x0a,0x16,0x0b).mark('try').emit(6,0x12,1).tok(0x28,0x0a000005)
        g.emit(2).tok(0x7b,0x04000005).tok(0x6f,0x0a000007).branch(0x2d,'reuse')
        g.emit(2).tok(0x7b,0x04000004).tok(0x8d,0x01000002).emit(0x0c).branch(0xde,'return')
        g.mark('reuse').emit(2).tok(0x7b,0x04000005).tok(0x6f,0x0a000008).emit(0x0c).branch(0xde,'return')
        g.mark('finally').emit(7).branch(0x2c,'endfinally').emit(6).tok(0x28,0x0a000006).mark('endfinally').emit(0xdc)
        g.mark('return').emit(8,0x2a)
        getter = g.build()
        getter_eh = b'\x01\x10\x00\x00'+struct.pack('<HHBHBI',2,g.labels['try'],g.labels['finally']-g.labels['try'],
                                                               g.labels['finally'],g.labels['return']-g.labels['finally'],0)
        getter_eh = options.get('getter_eh',lambda x:x)(getter_eh)
    f = IL().emit(4,0x8e,0x69,0x18,0x5d).branch(0x2c,'allocate')
    f.tok(0x72,error).tok(0x73,0x0a000001).emit(0x7a).mark('allocate').emit(2).tok(0x28,0x06000004)
    f.emit(0x0a,0x16,0x0b).branch(0x2b,'filltest').mark('fill').emit(6,7,3,0x9e,7,0x17,0x58,0x0b)
    f.mark('filltest').emit(7,6,0x8e,0x69).branch(0x32,'fill').emit(0x16,0x0c).branch(0x2b,'pairtest')
    f.mark('pair').emit(6,4,8,0x94,4,8,0x17,0x58,0x94,0x9e,8,0x18,0x58,0x0c)
    f.mark('pairtest').emit(8,4,0x8e,0x69).branch(0x32,'pair').emit(6,0x2a)
    consumer = IL().emit(0x16,0x0a).branch(0x2b,'test').mark('body')
    consumer.tok(0x7e,0x04000003).emit(6,0x94,0x0b,7,0x15).branch(0x2e,'direct')
    consumer.tok(0x7e,0x04000005).emit(6).tok(0x7e,0x04000005).emit(7,0x9a,0xa2).branch(0x2b,'next')
    consumer.mark('direct').tok(0x7e,0x04000005).emit(6).tok(0x72,prefix).emit(6).tok(0x8c,0x01000002)
    consumer.tok(0x28,0x0a000003).emit(0x16).tok(0x28,0x2b000001).emit(0xa2)
    consumer.mark('next').emit(6,0x17,0x58,0x0a).mark('test').emit(6).tok(0x7e,0x04000005)
    consumer.emit(0x8e,0x69).branch(0x32,'body').emit(0x2a)
    sets = token(0x7e,0x04000001)+token(0x73,0x06000003)+token(0x80,0x04000002)
    sets += token(0x7e,0x04000002)+b'\x15'+ldc(options.get('element_count',len(pairs)*2))+token(0x8d,0x01000002)
    sets += b'\x25'+token(0xd0,0x04000007)+token(0x28,0x0a000002)+token(0x6f,0x06000005)+token(0x80,0x04000003)+b'\x2a'
    codes = [ldc(count)+token(0x80,0x04000001)+b'\x2a',sets,ctor,getter,f.build(),
             token(0x7e,0x04000001)+token(0x8d,0x1b000001)+token(0x80,0x04000005)+b'\x2a',
             consumer.build(),token(0x7e,0x04000006)+b'\x02\x03'+token(0x6f,0x2b000002)+b'\x2a']
    if pooled:
        # Existing fixture fields move after two cache fields; the getter was
        # assembled with final field tokens and needs no remapping.
        from resource_pipeline.static_il import decode_il
        for code_index, code in enumerate(codes):
            if code_index == 3:
                continue
            remapped = bytearray(code)
            for ins in decode_il(code).values():
                if type(ins.operand) is int and ins.operand >> 24 == 4 and ins.operand & 0xffffff >= 5:
                    struct.pack_into('<I',remapped,ins.offset+1,ins.operand+2)
            codes[code_index] = bytes(remapped)
    for index, modifier in options.get('mutate_code',{}).items():
        codes[index-1] = modifier(codes[index-1])
    names = ['.cctor','.cctor','.ctor','GetIntBuffer','CreateIntSet','.cctor','LoadTextures','LoadAsset']
    signatures = [b'\x00\x00\x01',b'\x00\x00\x01',b'\x20\x01\x01\x08',b'\x20\x00\x1d\x08',
                  b'\x20\x02\x1d\x08\x08\x1d\x08',b'\x00\x00\x01',b'\x00\x01\x01'+mode,
                  b'\x10\x01\x02'+generic_asset+b'\x0e'+mode]
    methods = [[0,0,0x1886 if i==3 else 0x86 if i in (4,5) else 0x1891 if name=='.cctor' else 0x96,
                s(name),b(options.get('method_signatures',{}).get(i,sig)),1] for i,(name,sig) in enumerate(zip(names,signatures),1)]
    types = [('<Module>','',1,1,0,0),('ItemID','Terraria.ID',1,1,1,5),('Sets','',2,2,2,5),
             ('SetFactory','Terraria.ID',4,3,1,5),('TextureAssets','Terraria.GameContent',5,6,1,5),
             ('AssetInitializer','Terraria.Initializers',6,7,1,5),('Main','Terraria',6,9,1,5),
             ('OriginalData','Fixture',7,9,1,5),('OriginalLayout','Fixture',8,9,0x110,12<<2|1)]
    if pooled:
        types = [(name,ns,ff+2 if ff>=5 else ff,fm,flags,base) for name,ns,ff,fm,flags,base in types]
    member_specs = [(3<<3|1,'.ctor',b'\x20\x01\x01\x0e'),
                    (7<<3|1,'InitializeArray',b'\x00\x02\x01\x12'+c(5<<2|1)+b'\x11'+c(6<<2|1)),
                    (4<<3|1,'Concat',b'\x00\x02\x0e\x1c\x1c'),
                    (9<<3|1,'Request',b'\x30\x01\x02'+generic_asset+b'\x0e'+mode)]
    if pooled:
        member_specs += [(13<<3|1,'Enter',b'\x00\x02\x01\x1c\x10\x02'),
                         (13<<3|1,'Exit',b'\x00\x01\x01\x1c'),
                         (2<<3|4,'get_Count',b'\x20\x00\x08'),(2<<3|4,'Dequeue',b'\x20\x00\x13\x00')]
    rows = {0:[struct.pack('<HHHHH',0,s('OriginalAlias.dll'),1,0,0)],1:typerefs,
            2:[struct.pack('<IHHHHH',options.get('type_flags',{}).get(i,flags),s(name),s(ns),base,ff,fm)
               for i,(name,ns,ff,fm,flags,base) in enumerate(types,1)],
            4:field_rows,6:[],10:[struct.pack('<HHH',parent,s(options.get('member_names',{}).get(i,name)),
                                 b(options.get('member_signatures',{}).get(i,sig))) for i,(parent,name,sig) in enumerate(member_specs,1)],
            15:[struct.pack('<HIH',1,options.get('data_layout_size',len(data)),9)],
            17:[struct.pack('<H',b(b'\x07\x03\x1d\x08\x08\x08')),struct.pack('<H',b(b'\x07\x02\x08\x08'))],
            27:[struct.pack('<H',b(asset))],29:[b'\0'*6],
            32:[struct.pack('<IHHHHIHHH',0,1,0,0,0,0,0,s('OriginalAlias'),0)],
            35:[struct.pack('<HHHHIHHHH',4,0,0,0,0,b(options.get('core_key',bytes.fromhex('b77a5c561934e089'))),s('mscorlib'),0,0),
                struct.pack('<HHHHIHHHH',1,0,0,0,0,0,s('ReLogic'),0,0),
                struct.pack('<HHHHIHHHH',1,0,0,0,0,0,s('Microsoft.Xna.Framework.Graphics'),0,0)],
            41:[struct.pack('<HH',3,2)],
            42:[struct.pack('<HHHH',0,4,8<<1|1,s('T'))],
            43:[struct.pack('<HH',8<<1,b(b'\x0a\x01\x12'+c(11<<2|1))),struct.pack('<HH',4<<1|1,b(b'\x0a\x01\x1e\x00'))]}
    if options.get('generic_ordinary'):
        rows[42].append(struct.pack('<HHHH',0,0,5<<1|1,s('UnexpectedT')))
    if options.get('generic_backing'):
        rows[42].append(struct.pack('<HHHH',0,0,9<<1,s('UnexpectedT')))
    if pooled:
        rows[17].append(struct.pack('<H',b(b'\x07\x03\x1c\x02\x1d\x08')))
        rows[27].append(struct.pack('<H',b(queue_type)))
    def metadata():
        rows[6] = [struct.pack('<IHHHHH',*row) for row in methods]
        table = struct.pack('<IBBBBQQ',0,2,0,0,1,sum(1<<k for k in rows),0)
        table += b''.join(struct.pack('<I',len(rows[k])) for k in sorted(rows))
        table += b''.join(b''.join(rows[k]) for k in sorted(rows))
        streams = {'#~':table,'#Strings':bytes(strings),'#Blob':bytes(blobs),'#US':bytes(users),'#GUID':bytes(16)}
        root = bytearray(struct.pack('<IHHII',0x424a5342,1,1,0,12)+b'v4.0.30319\0\0'+struct.pack('<HH',0,len(streams)))
        at = len(root)+sum(8+((len(n)+4)&~3) for n in streams)
        for name,value in streams.items():
            root.extend(struct.pack('<II',at,len(value)));raw=name.encode()+b'\0';root.extend(raw+b'\0'*((-len(raw))%4));at+=len(value)
        for value in streams.values():root.extend(value)
        return bytes(root)
    meta = metadata(); start = (0x300+len(meta)+3)&~3; bodies=bytearray()
    for i,(row,code) in enumerate(zip(methods,codes),1):
        row[0]=0x2000+start+len(bodies)-0x200
        local = 0x11000001 if i==5 else 0x11000002 if i==7 else 0
        if pooled and i==4:local=0x11000003
        bodies.extend(struct.pack('<HHII',options.get('header_flags',{}).get(i,0x301b if pooled and i==4 else 0x3013),8,len(code),local)+code)
        if pooled and i==4:
            bodies.extend(b'\0'*((-len(bodies))%4));bodies.extend(getter_eh)
        bodies.extend(b'\0'*((-len(bodies))%4))
    data_start = start+len(bodies);rows[29]=[struct.pack('<IH',0x2000+data_start-0x200,9 if pooled else 7)]
    meta=metadata();size=((data_start+len(data)+511)//512)*512;out=bytearray(size)
    def put(offset,value):out[offset:offset+len(value)]=value
    put(0,b'MZ');put(0x3c,struct.pack('<I',0x80));put(0x80,b'PE\0\0')
    put(0x84,struct.pack('<HHIIIHH',0x14c,1,0,0,0,224,0x102));put(0x98,struct.pack('<H',0x10b))
    directory=0x98+96;put(directory-4,struct.pack('<I',16));put(directory+14*8,struct.pack('<II',0x2000,72))
    put(0x98+224,b'.text\0\0\0'+struct.pack('<IIII',size-0x200,0x2000,size-0x200,0x200))
    put(0x200,struct.pack('<IHHIIIIII',72,2,5,0x2100,len(meta),1,0,0,0));put(0x300,meta);put(start,bodies);put(data_start,data)
    return bytes(out)


def inventory(ids=(0,1,3,6,7), extra=()):
    paths = ['Images/Item_'+str(i)+'.xnb' for i in ids]+list(extra)
    return b''.join(json.dumps({'path':path,'gitBlobSha1':hashlib.sha1(path.encode()).hexdigest(),
                               'bytes':17,'mode':'100644'}).encode()+b'\n' for path in paths)


def proof(*, assets=None, limits=ItemTextureAliasLimits(), **options):
    return extract_item_texture_aliases(fixture(**options),inventory() if assets is None else assets,limits=limits)


class ItemTextureAliasTests(unittest.TestCase):
    def test_whole_synthetic_pe_binds_data_factory_and_ascending_consumer(self):
        result=proof();self.assertEqual('PROVEN_CONDITIONAL_ITEM_TEXTURE_ALIASES',result['status'])
        coverage=result['coverage'];self.assertEqual(8,coverage['logicalImagesResolved'])
        self.assertEqual(3,coverage['missingDirectResolvedByAlias']);self.assertEqual(2,coverage['maxChainEdges'])
        self.assertEqual(0,coverage['items'][4]['ultimateItem'])
        self.assertEqual(hashlib.sha256(fixture()).hexdigest(),result['inputSha256'])
        self.assertEqual(hashlib.sha256(inventory()).hexdigest(),result['inventorySha256'])
        for flag in ('complete','publicationAllowed','executedInput','runtimeStateCertified','imageBytesVerified'):
            self.assertFalse(result[flag])
        self.assertTrue(result['proof']['rva']['dataSha256'])

    def test_pooled_getter_has_closed_core_queue_monitor_and_finally(self):
        r=proof(pooled=True)
        self.assertEqual('PROVEN_CONDITIONAL_ITEM_TEXTURE_ALIASES',r['status'])
        self.assertTrue(any('exceptionSectionSha256' in e for e in r['evidence']))
        self.assert_unsupported(pooled=True,getter_eh=lambda x:x[:8]+b'\x01'+x[9:])
        self.assert_unsupported(pooled=True,member_names={8:'Enqueue'})
        self.assert_unsupported(pooled=True,mutate_code={4:lambda x:x.replace(b'\x12\x01',b'\x12\x00')})

    def test_duplicate_keys_use_last_write_and_direct_file_does_not_override_alias(self):
        r=proof(pairs=[(2,0),(2,1),(4,2),(5,1)],assets=inventory((0,1,2,3,6,7)))
        self.assertEqual([2],r['coverage']['duplicatePairKeys'])
        self.assertEqual(1,r['coverage']['items'][2]['ultimateItem'])
        self.assertEqual(1,r['coverage']['items'][4]['ultimateItem'])
        self.assertEqual('PRIOR_ITEM_COPY_RESOLVED',r['coverage']['items'][2]['status'])

    def test_forward_alias_rejected_despite_graph_reaching_present_file(self):
        r=proof(pairs=[(2,4),(4,0),(5,1)])
        self.assertEqual([2],r['coverage']['unsupportedNonpriorIds'])
        self.assertEqual('UNSUPPORTED_NONPRIOR_ALIAS',r['coverage']['items'][2]['status'])
        self.assertFalse(r['coverage']['logicalCoverageSatisfied'])
        self.assertEqual('PRIOR_ITEM_COPY_RESOLVED',r['coverage']['items'][4]['status'])

    def test_missing_base_and_alias_chain_stay_unresolved(self):
        r=proof(assets=inventory((1,3,6,7)))
        self.assertEqual([0,2,4],sorted(r['coverage']['unresolvedIds']))
        self.assertEqual('UNRESOLVED_PRIOR_ALIAS',r['coverage']['items'][4]['status'])

    def test_self_alias_does_not_resolve_from_its_direct_file(self):
        r=proof(pairs=[(2,2),(4,2),(5,1)],assets=inventory((0,1,2,3,6,7)))
        self.assertEqual('UNSUPPORTED_NONPRIOR_ALIAS',r['coverage']['items'][2]['status'])
        self.assertEqual('UNRESOLVED_PRIOR_ALIAS',r['coverage']['items'][4]['status'])

    def test_minus_one_pair_restores_direct_loading(self):
        r=proof(pairs=[(2,0),(2,-1),(4,2),(5,1)],assets=inventory((0,1,2,3,6,7)))
        self.assertEqual('DIRECT_IMAGE_PRESENT',r['coverage']['items'][2]['status'])
        self.assertEqual(2,r['coverage']['items'][4]['ultimateItem'])

    def test_only_exact_relative_content_image_paths_count(self):
        r=proof(assets=inventory((1,3,6,7),extra=('Sounds/Item_0.xnb','Nested/Images/Item_0.xnb',
                                              'Content/Images/Item_0.xnb','Images/Item_00.xnb')))
        self.assertEqual('MISSING_DIRECT_IMAGE',r['coverage']['items'][0]['status'])

    def assert_unsupported(self, **options):
        r=proof(**options);self.assertNotEqual('PROVEN_CONDITIONAL_ITEM_TEXTURE_ALIASES',r['status'])
        self.assertEqual([],r['aliases']);self.assertIsNone(r['coverage']);self.assertFalse(r['complete'])
        return r

    def test_rejects_unknown_factory_and_consumer_mutations(self):
        changes=[{5:lambda x:x.replace(b'\x08\x18\x58',b'\x08\x17\x58')},
                 {5:lambda x:x.replace(b'\x06\x07\x03\x9e',b'\x06\x07\x16\x9e')},
                 {7:lambda x:x.replace(b'\x06\x17\x58',b'\x06\x18\x58')},
                 {7:lambda x:b'\x17'+x[1:]},
                 {7:lambda x:x[:-1]+token(0x7e,0x04000005)+b'\x26\x2a'},
                 {7:lambda x:x[:-1]+b'\x38'+struct.pack('<i',-len(x)-4)+b'\x2a'},
                 {8:lambda x:x.replace(b'\x02\x03',b'\x03\x02')}]
        for change in changes:
            with self.subTest(change=change):self.assert_unsupported(mutate_code=change)

    def test_rejects_initializer_branch_and_duplicate_store(self):
        self.assert_unsupported(mutate_code={2:lambda x:b'\x2b\x00'+x})
        self.assert_unsupported(mutate_code={2:lambda x:x[:-1]+token(0x7e,0x04000003)+token(0x80,0x04000003)+b'\x2a'})

    def test_metadata_identity_signature_layout_and_path_guards(self):
        for options in ({'core_key':b'bad'}, {'generic_ordinary':True}, {'member_names':{2:'PretendInitializeArray'}},
                        {'generic_backing':True}, {'type_flags':{9:0x130}}, {'type_flags':{9:0x190}},
                        {'member_signatures':{3:b'\x00\x01\x0e\x1c'}}, {'data_layout_size':4},
                        {'element_count':5}, {'field_flags':{3:6}}, {'prefix':'Sounds/Item_'},
                        {'header_flags':{5:0x301b}}, {'method_signatures':{5:b'\x20\x01\x1d\x08\x1d\x08'}}):
            with self.subTest(options=options):self.assert_unsupported(**options)

    def test_domain_bounds(self):
        for pairs in ([(8,0)],[(1,8)],[(-1,0)],[(1,-2)]):
            self.assert_unsupported(pairs=pairs)

    def test_budgets_fail_without_partial_alias_table(self):
        for limits in (replace(ItemTextureAliasLimits(),input_bytes=16),replace(ItemTextureAliasLimits(),inventory_bytes=1),
                       replace(ItemTextureAliasLimits(),inventory_rows=1),replace(ItemTextureAliasLimits(),inventory_line_bytes=1),
                       replace(ItemTextureAliasLimits(),item_count=4),replace(ItemTextureAliasLimits(),pair_count=1),
                       replace(ItemTextureAliasLimits(),method_bytes=8),replace(ItemTextureAliasLimits(),total_method_bytes=32),
                       replace(ItemTextureAliasLimits(),steps=10),replace(ItemTextureAliasLimits(),instructions=4),
                       replace(ItemTextureAliasLimits(),evidence_bytes=4096)):
            with self.subTest(limits=limits):self.assert_unsupported(limits=limits)

    def test_hash_pin_and_invalid_inventory(self):
        r=extract_item_texture_aliases(fixture(),inventory(),expected_input_sha256='0'*64)
        self.assertEqual('ALIAS_INPUT_HASH_MISMATCH',r['status'])
        for value in (b'{}\n',b'{"path":"a","path":"b"}\n',b'['*2000+b']'*2000+b'\n',inventory((0,0)),inventory(extra=('../Images/Item_8.xnb',)),b'not json\n'):
            self.assert_unsupported(assets=value)

    def test_cancellation_and_wall_budget(self):
        class Cancelled(Exception):pass
        calls=0
        def cancel():
            nonlocal calls
            calls+=1
            if calls==25:raise Cancelled()
        with self.assertRaises(Cancelled):extract_item_texture_aliases(fixture(),inventory(),checkpoint=cancel)
        from resource_pipeline.security import PipelineError
        def cancel_with_pipeline_error():raise PipelineError('cancelled')
        with self.assertRaisesRegex(PipelineError,'cancelled'):
            extract_item_texture_aliases(fixture(),inventory(),checkpoint=cancel_with_pipeline_error)
        ticks = iter((0,100))
        with patch('resource_pipeline.item_texture_aliases.time.monotonic',side_effect=lambda:next(ticks,100)):
            self.assertEqual('ALIAS_TIME_LIMIT',proof()['status'])

    def test_final_serialization_checks_cancel_and_budget(self):
        from resource_pipeline import item_texture_aliases as module
        from resource_pipeline.static_il import ILUnsupported
        original = module.json_evidence_size
        class Cancelled(Exception): pass
        for mode in ('budget','cancel'):
            calls = 0; final = False
            def checkpoint():
                if mode == 'cancel' and final: raise Cancelled()
            def measured(value, maximum, checkpoint=None):
                nonlocal calls, final
                calls += 1
                if calls == 2:
                    final = True
                    if mode == 'budget': raise ILUnsupported('ALIAS_TIME_LIMIT')
                return original(value, maximum, checkpoint)
            with self.subTest(mode=mode), patch.object(module,'json_evidence_size',measured):
                if mode == 'cancel':
                    with self.assertRaises(Cancelled):
                        extract_item_texture_aliases(fixture(),inventory(),checkpoint=checkpoint)
                else:
                    result = extract_item_texture_aliases(fixture(),inventory())
                    self.assertEqual('ALIAS_TIME_LIMIT',result['status'])
                    self.assertEqual([],result['aliases']); self.assertIsNone(result['coverage'])

    def test_work_receipt_bytes_are_inside_exact_output_limit(self):
        from resource_pipeline.static_il import json_evidence_size
        rows = [json.loads(line) for line in inventory().splitlines()]
        for row in rows: row['bytes'] = 10 ** 3000
        assets = b''.join(json.dumps(row).encode()+b'\n' for row in rows)
        baseline = proof(assets=assets)
        without_work = {key:value for key,value in baseline.items() if key != 'work'}
        ceiling = json_evidence_size(without_work,10_000_000)
        result = proof(assets=assets,limits=replace(ItemTextureAliasLimits(),evidence_bytes=ceiling))
        self.assertEqual('ALIAS_EVIDENCE_BYTE_LIMIT',result['status'])
        self.assertEqual([],result['aliases']); self.assertIsNone(result['coverage'])

    def test_limits_reject_nan_and_boolean(self):
        for value in (float('nan'),float('inf'),True,-1):
            with self.assertRaises(ValueError):ItemTextureAliasLimits(wall_seconds=value)


if __name__=='__main__':unittest.main()
