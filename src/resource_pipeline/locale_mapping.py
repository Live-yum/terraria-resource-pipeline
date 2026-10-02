"""Bounded fixed-input localization-key evidence; never game/CLR execution.

The reviewed profile covers static Lang cache/key rules only. Effective values
remain references into the separate, explicitly unverified locale-loader model.
Hashes bind the manually inspected IL paths, signatures and reference source;
they do not by themselves establish arbitrary source/binary equivalence.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import time

from .locale_semantics import locale_copy_passes
from .security import PipelineError
from .server_semantics import SemanticLimits, _Metadata, _constant, _types
from .static_il import ILUnsupported, StaticILLimits, _compressed, decode_il, json_evidence_size


_INPUT_SHA256 = 'd87e3faf08637f6be8882c63e7f11fb7e792b0230006309618473ece0f863e1e'
REFERENCE = {'repository':'Live-yum/TerrariaDecompiledSource',
    'commit':'8255d34616c780af12079425ac92a0a7aed87d71',
    'langGitBlob':'724e5a505198cc7be0af3e06c10449169e861675',
    'languageGitBlob':'2cf12c0dbb8be8a32de5bd1ca3981c7d515669a5',
    'languageManagerGitBlob':'94181f9660eeb7cdc2f37edc66d0d6231cd9d56f',
    'itemTooltipGitBlob':'e63694343bc5204da57e036ba63e783f527b025a',
    'localizedTextGitBlob':'aaf1380ff232ce5bf8e50ad2a1c0a41ca8a1e65f',
    'itemIdGitBlob':'391d0ba08cb7a47bdd4456e48deca6c847ff0f69'}

# Inserted below from the reviewed private method-digest inventory. No original
# method bytes, language strings or decompiled source are distributed here.
_METHOD_PROFILE = {'Terraria.Lang::GetNPCName': ('cb024a13d7c0007e0f0d87add007bf98de9040a7cd65d0ab6c0299ddfcf46e6a',
                               '279fa48ac001ff382d5f37479ef8816db3ad2e3b8adb37838687796aa8e1adb3'),
 'Terraria.Lang::GetItemName': ('a25b216028a2d0d9082222d254bacb292b067a0c506eb35f629a17c1d5a09406',
                                '279fa48ac001ff382d5f37479ef8816db3ad2e3b8adb37838687796aa8e1adb3'),
 'Terraria.Lang::GetBuffName': ('b0e589a1a45090e4d724c5cb1a06b9aed6c16a70757c223a47c85c79735baef0',
                                '1e0acf33ca250a1c401a2fd9399e8a2da22fecb9d85445b2a5091bbf32627ed7'),
 'Terraria.Lang::GetBuffDescription': ('2f9915eb53c98ae5a96306cdd71637ed956feb7fa08a21a014cbf9bac87c9737',
                                       '1e0acf33ca250a1c401a2fd9399e8a2da22fecb9d85445b2a5091bbf32627ed7'),
 'Terraria.Lang::FillNameCacheArray': ('cbcca18fd59755c3f442909e6b1c35d1b125d6aaf793f6cc4d82477b3eba7eab',
                                       '1fa53dcba3e37928ff839d5f74023b6be23720e48a7e1ac3dbdedbd7a29ba101'),
 'Terraria.Lang::InitializeLegacyLocalization': ('f542bd68b060c29e226527d7b70ea1ee9bec6cd4db25f317d6d0fe549ea06dd5',
                                                 'cf7605ed1bc735f6c825554154627467e1cac9df54cee8699218ed434603c568'),
 'Terraria.Lang::.cctor': ('7cc2e6d6ef1943b3badc191cc371986aca68137714f706ed1a07050ed65352e9',
                           'cf7605ed1bc735f6c825554154627467e1cac9df54cee8699218ed434603c568'),
 'Terraria.UI.ItemTooltip::FromLanguageKey': ('d3f74d741eb5b9c87fc2615148368d6ce55808b7428788d91fcf0763f96cf13c',
                                              '536f586e31981fd45124985a7ad2ff8c2ebaa69bbc4d90b4ec66a6dfc62fbd13'),
 'Terraria.Localization.Language::GetText': ('fa8888696ac12bfd9506673f89072406151c4de2c72b1e6550cff749fdd68bd1',
                                             'e2032242947557257c3930a8ae2fa3302772e377d49cd14a4b0ea8475769067b'),
 'Terraria.Localization.Language::Exists': ('d276813fa10b244b109880f8227ca52750a680fd33056a7265f05eea606d2cfd',
                                            'c6804a6b6167553b78bfc424ea29f72681c1c3a2b46bb746dbb1bbe467c68bb5'),
 'Terraria.Localization.LanguageManager::Exists': ('d8ef5554f7d1f0809821ae797ef51d8375a534f4b963370a5c2550ae1b0de16c',
                                                   '61bbd491345d80bcde08286d395defead9e181d82e76ce39bf961ebefbf660be'),
 'Terraria.Localization.LanguageManager::GetText': ('b8a7d1f90773b99843c222f4ce2695872fab93e18aedc04a36d3070378edd4b6',
                                                    'c33307b243ee27e59b29e175f5322761e40fbfc1f6a99d94aceab85b27bfb0da'),
 'Terraria.ID.PrefixID::.cctor': ('74a520d0d9f3cf4a07b3d811c0d91ce88281969e159aacd312c9337045d1cfea',
                                  'cf7605ed1bc735f6c825554154627467e1cac9df54cee8699218ed434603c568'),
 'Terraria.ID.BuffID::.cctor': ('db7fd5740e3dbaf8744f8490dcbd49a0cf870e05edf0e219f70b3735e5ecdf7b',
                                'cf7605ed1bc735f6c825554154627467e1cac9df54cee8699218ed434603c568'),
 'Terraria.ID.ItemID::FromNetId': ('7624bfe91550eaaacb065d90d6a2778d48446defde7a469539863f7db6fbc9b8',
                                   'acb973c288f138f2b60bb0b81819481510b78af385652fffccb82d04a6068729'),
 'Terraria.ID.ItemID::.cctor': ('4f7fec29de602e0798eeb5caca3a0dd2896eb68a22c34633181afe38b67d2fbe',
                                'cf7605ed1bc735f6c825554154627467e1cac9df54cee8699218ed434603c568'),
 'Terraria.ID.NPCID::FromNetId': ('20e7b2bf577d4ea89586071b0c113261d3973dc1717dc7158576b3ac51d47081',
                                  '5346e8a2fc184cdb143883499d1134f781d12a6789d53f0a1df6ed535638d82b'),
 'Terraria.ID.NPCID::.cctor': ('8fcb002b917ce4bd00da63aa52bde92722af83f5726d429268a0147dfdecaeea',
                               'cf7605ed1bc735f6c825554154627467e1cac9df54cee8699218ed434603c568'),
 'Terraria.Lang+<>c::<InitializeLegacyLocalization>b__55_0': ('9283f216a0bdc84366ce9721c3bbcfd07b052c1414d0cfef5774dfa400598cff',
                                                              '808e40cbf19fa04aaf6484443a8f67889f5f113db8354e39baa743bb1095eaa9'),
 'Terraria.Lang+<>c::<InitializeLegacyLocalization>b__55_1': ('c81576816377b53f1b18d02ff1f99444771617b4ab2723ba380874b0ab38bfd0',
                                                              '90ecef36e4f5566c38555f120b2f42bbddc079f8b9788d83a0d82d5a592146c4'),
 'Terraria.Lang+<>c__DisplayClass54_0`2::<FillNameCacheArray>b__1': ('552ffef8e12b45b433ebadc396f353bb2d2c4278e8deb0db2de8497156fa1c24',
                                                                     '90ecef36e4f5566c38555f120b2f42bbddc079f8b9788d83a0d82d5a592146c4'),
 'Terraria.Lang+<>c__54`2::<FillNameCacheArray>b__54_0': ('4496d2fb0a1a9d55689d2beabbe5d9e50acb257e5420b90ab2cddf16c442dbb6',
                                                          '808e40cbf19fa04aaf6484443a8f67889f5f113db8354e39baa743bb1095eaa9')}


@dataclass(frozen=True)
class MappingLimits:
    input_bytes: int = 128*1024*1024
    fields: int = 20_000
    methods: int = 60_000
    constants: int = 100_000
    output_bytes: int = 16*1024*1024
    locales: int = 32
    wall_seconds: float = 30

    def __post_init__(self):
        for name,value in vars(self).items():
            if name=='wall_seconds':
                if type(value) not in (int,float) or not 0<value<=120:raise ValueError('Invalid mapping time budget')
            elif type(value) is not int or value<=0:raise ValueError('Invalid mapping integer budget')
        if self.output_bytes<16*1024:raise ValueError('Mapping evidence budget needs a diagnostic envelope')


# family, sub-capability, category, exact ID declaration type, CIL element type,
# and the cache field loaded at the verified InitializeLegacyLocalization call.
SPECS = (
    ('items','localizedNames','ItemName','Terraria.ID.ItemID',6,'_itemNameCache'),
    ('items','tooltipBaseStrings','ItemTooltip','Terraria.ID.ItemID',6,'_itemTooltipCache'),
    ('buffs','localizedNames','BuffName','Terraria.ID.BuffID',8,'_buffNameCache'),
    ('buffs','localizedDescriptions','BuffDescription','Terraria.ID.BuffID',8,'_buffDescriptionCache'),
    ('prefixes','localizedNames','Prefix','Terraria.ID.PrefixID',8,'prefix'),
    ('npcs','localizedNames','NPCName','Terraria.ID.NPCID',6,'_npcNameCache'),
)


class MappingBound(PipelineError):
    pass


def _read_input(path,limits,checkpoint):
    path=Path(path)
    if any(part.is_symlink() for part in (path,*path.parents)):raise PipelineError('Mapping input cannot traverse symlinks')
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    with os.fdopen(descriptor,'rb') as source:
        before=os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=limits.input_bytes:raise PipelineError('Mapping input must be a bounded regular file')
        chunks=[];size=0
        while True:
            checkpoint();chunk=source.read(min(1024*1024,limits.input_bytes-size+1))
            if not chunk:break
            size+=len(chunk)
            if size>limits.input_bytes:raise PipelineError('Mapping source exceeds input limit')
            chunks.append(chunk)
        after=os.fstat(source.fileno())
    if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns) or size!=before.st_size:
        raise PipelineError('Mapping source changed during inspection')
    return b''.join(chunks)


class _Image:
    def __init__(self,data,limits,checkpoint):
        self.meta=_Metadata(data,SemanticLimits(),checkpoint);self.types=_types(self.meta)
        self.checkpoint=checkpoint;self.methods={};self.fields={};self.bodies={};self.code_bytes=0
        if self.meta.rows[6]>limits.methods or self.meta.rows[11]>limits.constants:raise MappingBound('MAPPING_METADATA_LIMIT')
        for type_id,typedef in self.types.items():
            checkpoint()
            for rid in range(typedef['firstMethod'],typedef['lastMethod']):self.methods[rid]=typedef['fullName']
            for rid in range(typedef['firstField'],typedef['lastField']):self.fields[rid]=typedef['fullName']
        self.by_name={}
        for rid,owner in self.methods.items():
            row,_=self.meta.row(6,rid)
            if owner=='Terraria.Lang' or owner.startswith('Terraria.Lang+') or owner in {spec[3] for spec in SPECS}|{'Terraria.Localization.Language','Terraria.Localization.LanguageManager','Terraria.UI.ItemTooltip'}:
                self.by_name.setdefault(owner+'::'+self.meta.string(row[3]),[]).append(0x06000000|rid)

    def method(self,name):
        tokens=self.by_name.get(name,[])
        if len(tokens)!=1:raise MappingBound('MAPPING_METHOD_MISSING_OR_AMBIGUOUS')
        token=tokens[0]
        if token in self.bodies:return self.bodies[token]
        row,metadata_offset=self.meta.row(6,token&0xffffff)
        if row[1]&7 or row[2]&0x2000 or not row[0]:raise MappingBound('MAPPING_METHOD_NOT_MANAGED_IL')
        body=self.meta.rva(row[0],1);first=self.meta.reader.uint(body,1)
        if first&3==2:header,size=1,first>>2
        elif first&3==3:
            flags=self.meta.reader.uint(body,2);header=(flags>>12)*4
            if flags&8 or not 12<=header<=60:raise MappingBound('MAPPING_METHOD_UNSUPPORTED_HEADER')
            size=self.meta.reader.uint(body+4,4)
        else:raise MappingBound('MAPPING_METHOD_UNSUPPORTED_HEADER')
        if size>1024*1024 or self.code_bytes+size>8*1024*1024:raise MappingBound('MAPPING_METHOD_BYTE_LIMIT')
        code_offset=self.meta.rva(row[0]+header,size);code=self.meta.reader.take(code_offset,size)
        signature,signature_offset=self.meta.blob(row[4])
        instructions=list(decode_il(code,StaticILLimits(),self.checkpoint).values())
        evidence={'method':name,'methodToken':f'0x{token:08x}','metadataOffset':metadata_offset,
                  'bodyOffset':body,'codeOffset':code_offset,'codeBytes':size,
                  'ilSha256':hashlib.sha256(code).hexdigest(),'signatureSha256':hashlib.sha256(signature).hexdigest(),
                  'signatureOffset':signature_offset}
        result=(instructions,evidence);self.bodies[token]=result;self.code_bytes+=size
        return result

    def field_name(self,token):
        if token>>24!=4:raise MappingBound('MAPPING_FIELD_TOKEN_INVALID')
        row,_=self.meta.row(4,token&0xffffff)
        return self.fields[token&0xffffff]+'::'+self.meta.string(row[1])

    def user_string(self,token):
        if token>>24!=0x70 or '#US' not in self.meta.streams:raise MappingBound('MAPPING_STRING_TOKEN_INVALID')
        heap=self.meta.streams['#US'];start=heap.start+(token&0xffffff)
        size,prefix=_compressed(heap.take(start,min(4,heap.end-start)),0)
        if not 1<=size<=8193 or size%2!=1:raise MappingBound('MAPPING_STRING_LIMIT')
        return heap.take(start+prefix,size-1).decode('utf-16-le')

    def generic_binding(self,token):
        if token>>24!=43:raise MappingBound('MAPPING_GENERIC_CALL_INVALID')
        row,offset=self.meta.row(43,token&0xffffff)
        if row[0]&1 or (0x06000000|(row[0]>>1))!=self.by_name['Terraria.Lang::FillNameCacheArray'][0]:
            return None
        blob,_=self.meta.blob(row[1])
        if blob[:3]!=b'\x0a\x02\x12':raise MappingBound('MAPPING_GENERIC_SIGNATURE_INVALID')
        coded,pos=_compressed(blob,3)
        if coded&3 or coded>>2 not in self.types or pos+1!=len(blob):raise MappingBound('MAPPING_GENERIC_SIGNATURE_INVALID')
        return {'idClass':self.types[coded>>2]['fullName'],'elementType':blob[pos],
                'methodSpecToken':f'0x{token:08x}','metadataOffset':offset,'signatureSha256':hashlib.sha256(blob).hexdigest()}


def _constant_number(ins):
    if ins.opcode==0x15:return -1
    if 0x16<=ins.opcode<=0x1e:return ins.opcode-0x16
    if ins.opcode in (0x1f,0x20):return ins.operand
    raise MappingBound('MAPPING_COUNT_NOT_IMMEDIATE_CONSTANT')


def _inspect_rules(image):
    if not _METHOD_PROFILE:raise MappingBound('MAPPING_REVIEW_PROFILE_MISSING')
    evidence=[]
    for name,(code_hash,signature_hash) in _METHOD_PROFILE.items():
        _,method=image.method(name)
        if method['ilSha256']!=code_hash or method['signatureSha256']!=signature_hash:
            raise MappingBound('REVIEWED_MAPPING_METHOD_MISMATCH')
        evidence.append(method)
    initial,_=image.method('Terraria.Lang::InitializeLegacyLocalization')
    bindings={}
    for n,ins in enumerate(initial):
        if ins.opcode!=0x28 or ins.operand>>24!=43:continue
        binding=image.generic_binding(ins.operand)
        if binding is None:continue
        if n<3 or [row.opcode for row in initial[n-3:n]]!=[0x72,0x7e,0x16] and [row.opcode for row in initial[n-3:n]]!=[0x72,0x7e,0x17]:
            raise MappingBound('MAPPING_CALL_ARGUMENT_PATTERN_MISMATCH')
        category=image.user_string(initial[n-3].operand)
        bindings[category]={**binding,'cache':image.field_name(initial[n-2].operand),
                            'leaveMissingBlank':initial[n-1].opcode==0x17,'ilOffset':ins.offset}
    initializer,_=image.method('Terraria.Lang::.cctor')
    arrays={}
    for n,ins in enumerate(initializer):
        if n>=2 and ins.opcode==0x80 and initializer[n-1].opcode==0x8d and initializer[n-2].opcode==0x7e:
            arrays[image.field_name(ins.operand)]={'countField':image.field_name(initializer[n-2].operand),
                'countFieldToken':f'0x{initializer[n-2].operand:08x}','allocationIlOffset':initializer[n-1].offset}
    counts={}
    for _,_,_,owner,_,_ in SPECS:
        if owner in counts:continue
        code,proof=image.method(owner+'::.cctor')
        # Bounded straight-line initializer recognition, conditional on success.
        if any(0x2b<=ins.opcode<=0x45 or ins.opcode in (0x27,0x29,0xdd,0xde) for ins in code):
            raise MappingBound('MAPPING_COUNT_INITIALIZER_CONTROL_FLOW')
        if not code or code[-1].opcode!=0x2a or sum(ins.opcode==0x2a for ins in code)!=1:
            raise MappingBound('MAPPING_COUNT_INITIALIZER_EARLY_RETURN')
        stores=[(n,ins) for n,ins in enumerate(code) if ins.opcode==0x80 and image.field_name(ins.operand)==owner+'::Count']
        if len(stores)!=1 or stores[0][0]<1:raise MappingBound('MAPPING_COUNT_ASSIGNMENT_AMBIGUOUS')
        n,store=stores[0];number=_constant_number(code[n-1])
        if not 0<number<=100_000:raise MappingBound('MAPPING_COUNT_VALUE_LIMIT')
        counts[owner]={'value':number,'fieldToken':f'0x{store.operand:08x}','storeIlOffset':store.offset,
                       'constantIlOffset':code[n-1].offset,'methodToken':proof['methodToken'],'ilSha256':proof['ilSha256'],
                       'conditionalOnSuccessfulInitialization':True}
    for _,_,category,owner,element,cache in SPECS:
        target='Terraria.Lang::'+cache
        if target not in arrays or arrays[target]['countField']!=owner+'::Count':raise MappingBound('MAPPING_CACHE_EXTENT_MISMATCH')
        if category=='ItemTooltip':continue # reviewed short-field lambda, not the generic name helper
        bound=bindings.get(category)
        if bound is None or (bound['idClass'],bound['elementType'],bound['cache'],bound['leaveMissingBlank'])!=(owner,element,target,False):
            raise MappingBound('MAPPING_CATEGORY_BINDING_MISMATCH')
    return {'status':'REVIEWED_FIXED_INPUT_STATIC_RULES','profileId':'terraria-1458-windows-lang-static-v1',
            'reference':REFERENCE,'methods':evidence,
            'cacheBindings':bindings,'cacheAllocations':arrays,'counts':counts,
            'reviewedRules':['exact public/static reflection mask and exact IdType predicate',
                'empty-cache initialization, bounds guard, category-dot-field-name key construction',
                'generic category/type/cache bindings and readonly Count assignments',
                'missing GetText key inserts LocalizedText(key,key)',
                'GetItemName/GetNPCName exclude zero and normalize negative IDs separately',
                'tooltip short-ID predicate, positive bounds, ItemTooltip.None initialization and FromLanguageKey binding'],
            'runtimeInitializationVerified':False,'binaryLocaleLoaderEquivalenceVerified':False,
            'assumptions':['successful static initialization and successful cache setup',
                           'standard CLI primitive and reflection semantics; alias iteration order is not assumed']}


def _eligible_fields(image,limits):
    constants={}
    for rid in range(1,image.meta.rows[11]+1):
        row,offset=image.meta.row(11,rid)
        if row[1]&3==0:constants[row[1]>>2]=(row[0]&0xff,row[2],offset)
    groups={};unresolved={};total=0
    for family,_,_,owner,element,_ in SPECS:
        if family in groups:continue
        matches=[row for row in image.types.values() if row['fullName']==owner]
        if len(matches)!=1:raise MappingBound('MAPPING_ID_TYPE_AMBIGUOUS')
        records=[];unknown=[]
        for rid in range(matches[0]['firstField'],matches[0]['lastField']):
            image.checkpoint();total+=1
            if total>limits.fields:raise MappingBound('MAPPING_FIELD_LIMIT')
            row,offset=image.meta.row(4,rid);signature,_=image.meta.blob(row[2])
            if row[0]&7!=6 or not row[0]&0x10 or signature!=bytes((6,element)):continue
            name=image.meta.string(row[1]);base={'name':name,'fieldToken':f'0x{0x04000000|rid:08x}','metadataOffset':offset,'flags':row[0],'elementType':element}
            if rid not in constants or not row[0]&0x40:
                if name!='Count':unknown.append({**base,'status':'PENDING_NONLITERAL_PUBLIC_STATIC_FIELD'})
                continue
            kind,index,constant_offset=constants[rid]
            value,_,value_offset,_=_constant(image.meta,kind,index)
            if kind!=element or type(value) is not int:raise MappingBound('MAPPING_CONSTANT_TYPE_MISMATCH')
            records.append({**base,'id':value,'constantMetadataOffset':constant_offset,'valueBlobOffset':value_offset})
        groups[family]=records;unresolved[family]=unknown
    return groups,unresolved


def _locale_gaps(server,locale,checkpoint):
    value=server['localization'][locale];baseline=value['baseline'];bad=set();references={};global_error=False
    for copy in locale_copy_passes(server,locale):
        for row in copy['errors']+copy['missingReferences']:
            checkpoint()
            if 'key' in row:bad.add(row['key'])
            else:global_error=True
        for key,row in copy['copyEvidence'].items():
            references.setdefault(key,set()).update(row['references'])
            if not row['stabilized']:bad.add(key)
    # Propagate reference uncertainty without recursive traversal or unbounded loops.
    reverse={}
    for key,refs in references.items():
        for reference in refs:reverse.setdefault(reference,set()).add(key)
    todo=list(bad)
    while todo:
        checkpoint();current=todo.pop()
        for dependent in reverse.get(current,()):
            if dependent not in bad:bad.add(dependent);todo.append(dependent)
    return bad,global_error or bool(baseline['loadErrors'])


def _map_fields(server,groups,unresolved,rules,limits,checkpoint):
    locales=sorted(server.get('localization',{}))
    if len(locales)>limits.locales:raise MappingBound('MAPPING_LOCALE_LIMIT')
    gaps={locale:_locale_gaps(server,locale,checkpoint) for locale in locales}
    channels={};used=0;stopped=False
    for family,capability,category,owner,_,_ in SPECS:
        checkpoint();fields=groups[family];by_id={}
        for index,field in enumerate(fields):by_id.setdefault(field['id'],[]).append(index)
        channel={'family':family,'capability':capability,'category':category,'status':'PARTIAL','complete':False,
                 'cacheLength':rules['counts'][owner]['value'],'records':[],'observedIds':len(by_id),
                 'provenStaticKeyMappings':0,'provenEmptyResults':0,'pendingMappings':0,
                 'localeStatusCounts':{},'pending':['binary locale-loader equivalence','runtime substitutions and culture state']}
        channel['unpopulatedCacheIds']=[identity for identity in range(channel['cacheLength']) if identity not in by_id]
        channel['unpopulatedCacheOutcome']='PENDING_NONLITERAL_FIELDS' if unresolved[family] else 'INITIAL_EMPTY_RETAINED'
        invalid_none=any(field['name']=='None' and not 0<=field['id']<channel['cacheLength'] for field in fields)
        if category=='ItemTooltip':channel['pending']+=['HasValue/EnglishValue initialization history','tooltip rendering and dynamic modifiers']
        for identity,indices in sorted(by_id.items()):
            checkpoint()
            if stopped:break
            row={'id':identity,'fieldRefs':indices,'locales':{}}
            candidates=[category+'.'+fields[index]['name'] for index in indices]
            if invalid_none:row.update(status='PENDING_INVALID_NONE_FIELD',keyCandidates=candidates)
            elif len(indices)>1:row.update(status='PENDING_REFLECTION_ALIAS_ORDER',keyCandidates=candidates)
            elif unresolved[family]:row.update(status='PENDING_NONLITERAL_ID_COLLISION',key=candidates[0])
            elif identity<0:row.update(status='PENDING_NEGATIVE_ID_RULE' if category in ('ItemName','NPCName') else 'OUTSIDE_CACHE_DOMAIN',keyCandidate=candidates[0])
            elif identity>=channel['cacheLength']:row.update(status='OUTSIDE_CACHE_DOMAIN',keyCandidate=candidates[0])
            elif identity==0 and family in ('items','npcs'):
                row.update(status='PROVEN_TOOLTIP_NONE' if category=='ItemTooltip' else 'PROVEN_EMPTY_NAME')
            else:
                key=candidates[0];row.update(status='PROVEN_STATIC_KEY_MAPPING',key=key)
                for locale in locales:
                    checkpoint();strings=server['localization'][locale]['strings'];bad,global_error=gaps[locale]
                    if global_error:code='PENDING_LOCALE_LOAD'
                    elif key in bad:code='PENDING_COPY_EVIDENCE'
                    elif key not in strings:code='MISSING_TOOLTIP_BASE' if category=='ItemTooltip' else 'KEY_TEXT_FALLBACK'
                    elif '{$' in strings[key]:code='PENDING_COPY_REFERENCE'
                    elif '{' in strings[key] or '}' in strings[key]:code='PENDING_DYNAMIC_TEMPLATE'
                    else:code='TOOLTIP_BASE_REFERENCE' if category=='ItemTooltip' else 'BASELINE_TEXT_REFERENCE'
                    row['locales'][locale]=code
            try:size=json_evidence_size(row,max(0,limits.output_bytes//2-used),checkpoint)
            except PipelineError as exc:
                # Never treat cancellation as a size failure.
                from .static_il import EvidenceSizeLimit
                if not isinstance(exc,EvidenceSizeLimit):raise
                channel['diagnostic']='MAPPING_OUTPUT_BUDGET';stopped=True;break
            used+=size+1;channel['records'].append(row)
            if row['status']=='PROVEN_STATIC_KEY_MAPPING':channel['provenStaticKeyMappings']+=1
            elif row['status'].startswith('PROVEN_'):channel['provenEmptyResults']+=1
            else:channel['pendingMappings']+=1
            for locale,code in row['locales'].items():
                counts=channel['localeStatusCounts'].setdefault(locale,{})
                counts[code]=counts.get(code,0)+1
        attempted={row['id'] for row in channel['records']}
        channel['unattemptedIds']=[identity for identity in sorted(by_id) if identity not in attempted]
        channel['unattemptedCount']=len(channel['unattemptedIds'])
        channels[category]=channel
    return channels


def extract_locale_mapping(path,server_evidence,*,server_evidence_sha256=None,limits=MappingLimits(),checkpoint=None):
    """Read original metadata and bind references; uploaded trust flags are ignored."""
    if server_evidence_sha256 is not None and (not isinstance(server_evidence_sha256,str) or not re.fullmatch(r'[a-f0-9]{64}',server_evidence_sha256)):
        raise PipelineError('Invalid mapping evidence artifact digest')
    checkpoint=checkpoint or (lambda:None);deadline=time.monotonic()+limits.wall_seconds
    def bounded():
        checkpoint()
        if time.monotonic()>=deadline:raise MappingBound('MAPPING_TIME_LIMIT')
    result={'schemaVersion':1,'status':'PARTIAL','executedInput':False,'complete':False,'publishable':False,
            'channels':{},'idFields':{},'unresolvedIdFields':{},'reference':REFERENCE,
            'valueReferences':{'artifact':'server evidence JSON','schemaVersion':2,
                'effective':'localization[locale].strings[record.key]',
                'source':'localization[locale].baseline.keyEvidence[record.key]',
                'KEY_TEXT_FALLBACK':'literal record.key, per GetText; not a localized string',
                'MISSING_TOOLTIP_BASE':'no baseline entry; tooltip rendering remains unresolved'},
            'pending':['negative ID normalization and negative NPC overrides','full family semantics','runtime locale-loader equivalence']}
    try:
        data=_read_input(path,limits,bounded);digest=hashlib.sha256(data).hexdigest()
        result['inputSha256']=digest;result['serverEvidenceSha256']=server_evidence_sha256
        if server_evidence.get('input',{}).get('sha256')!=digest:raise MappingBound('MAPPING_SOURCE_BINDING_MISMATCH')
        if digest!=_INPUT_SHA256:
            result['ruleBinding']={'status':'PENDING_UNREVIEWED_INPUT','reference':REFERENCE}
            return result
        if server_evidence.get('schemaVersion')!=2 or server_evidence.get('localeEvidence',{}).get('schemaVersion')!=2:
            raise MappingBound('MAPPING_REQUIRES_LOCALE_EVIDENCE_V2')
        image=_Image(data,limits,bounded);rules=_inspect_rules(image)
        fields,unresolved=_eligible_fields(image,limits)
        result.update(ruleBinding=rules,idFields=fields,unresolvedIdFields=unresolved,
                      analyzedLocales=sorted(server_evidence.get('localization',{})))
        result['channels']=_map_fields(server_evidence,fields,unresolved,rules,limits,bounded)
    except (MappingBound,ILUnsupported) as exc:
        result['diagnostic']=str(exc);result.setdefault('ruleBinding',{'status':'PENDING_RULE_EVIDENCE'})
    json_evidence_size(result,limits.output_bytes,checkpoint)
    return result
