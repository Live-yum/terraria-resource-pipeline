#!/usr/bin/env python3
"""Opt-in experiment planner; requires measured per-variant linker/stack proof.

Does not install tools, alter defaults, deploy, or claim device measurements.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import re
import subprocess

MIB = 1 << 20

def wasm_bounds(data):
    """Read exported constant linker bounds from the actual Wasm module.

    Diagnostic proof builds must export __data_end, __stack_low, __stack_high,
    and __heap_base. Imported or dynamic globals cannot serve as static proof.
    """
    if data[:8] != b'\x00asm\x01\x00\x00\x00':
        raise ValueError('static proof is not a Wasm module')
    def integer(buf, at, signed=False):
        value=0;shift=0
        for _ in range(5):
            b=buf[at];at+=1;value|=(b&127)<<shift;shift+=7
            if not b&128:
                if signed and b&64:value-=1<<shift
                return value,at
        raise ValueError('invalid Wasm integer')
    def name(buf, at):
        size,at=integer(buf,at);return buf[at:at+size].decode(),at+size
    def limits(buf, at):
        flags,at=integer(buf,at);_,at=integer(buf,at)
        if flags&1:_,at=integer(buf,at)
        if flags&~3:raise ValueError('unsupported Wasm limits')
        return at
    globals=[];exports={};at=8
    while at<len(data):
        kind=data[at];size,at=integer(data,at+1);section=data[at:at+size];at+=size
        if len(section)!=size:raise ValueError('truncated Wasm section')
        if kind==2:
            count,pos=integer(section,0)
            for _ in range(count):
                _,pos=name(section,pos);_,pos=name(section,pos);imp=section[pos];pos+=1
                if imp==0:_,pos=integer(section,pos)
                elif imp==1:pos=limits(section,pos+1)
                elif imp==2:pos=limits(section,pos)
                elif imp==3:pos+=2;globals.append(None)
                else:raise ValueError('unsupported Wasm import')
        elif kind==6:
            count,pos=integer(section,0)
            for _ in range(count):
                typ,mutable,opcode=section[pos:pos+3];pos+=3
                if typ!=0x7f or opcode!=0x41:raise ValueError('proof globals must be constant i32')
                value,pos=integer(section,pos,True)
                if section[pos]!=0x0b:raise ValueError('invalid global initializer')
                pos+=1;globals.append(None if mutable else value)
        elif kind==7:
            count,pos=integer(section,0)
            for _ in range(count):
                key,pos=name(section,pos);typ=section[pos];index,pos=integer(section,pos+1)
                if typ==3:exports[key]=index
    result={}
    for key in ('__data_end','__stack_low','__stack_high','__heap_base'):
        index=exports.get(key)
        if index is None or index>=len(globals) or globals[index] is None or globals[index]<0:
            raise ValueError('missing immutable linker bound '+key)
        result[key]=globals[index]
    return result

def bounded_stack(analysis):
    if analysis.get('schema')!=1 or analysis.get('unresolvedIndirectCalls')!=0:
        raise ValueError('incomplete static stack analysis')
    nodes=analysis['functions'];visiting=set();cache={}
    def depth(name):
        if name in visiting:raise ValueError('recursive stack graph is not bounded')
        if name in cache:return cache[name]
        node=nodes[name];frame=node['frameBytes']
        if type(frame) is not int or frame<0 or node.get('dynamicStack') is not False:
            raise ValueError('unbounded stack frame')
        visiting.add(name)
        value=frame+max((depth(child) for child in node['calls']),default=0)
        visiting.remove(name);cache[name]=value;return value
    entries=analysis['entries']
    if not entries:raise ValueError('stack analysis entry points missing')
    return max(depth(name) for name in entries)

def plans(proof, root, evidence_root):
    if proof.get('schema') != 1 or not re.fullmatch('[0-9a-f]{40}', proof.get('engineSourceCommit', '')):
        raise ValueError('invalid source proof')
    result = []
    variants = {(p['optimization'], p['simd']): p for p in proof['variants']}
    if len(variants) != len(proof['variants']):
        raise ValueError('duplicate variant proof')
    for optimize, simd in itertools.product(('-O3', '-Oz'), (False, True)):
        p = variants.get((optimize, simd))
        if not p or p.get('method') != 'linked-symbols-and-bounded-stack-analysis':
            raise ValueError('missing static/stack proof for variant')
        for name in ('staticDataEnd', 'stackBytes', 'heapBase', 'minimumWorkspaceBytes', 'guardBytes'):
            if type(p.get(name)) is not int or p[name] < 0:
                raise ValueError('invalid proof byte count: ' + name)
        if p['stackBytes'] == 0 or p['guardBytes'] < 65536 or p['heapBase'] < p['staticDataEnd'] + p['stackBytes']:
            raise ValueError('heap base does not contain static data and proven stack')
        for kind in ('wasm', 'linkMap', 'stackAnalysis'):
            path = (evidence_root / p[kind + 'Path']).resolve()
            if not path.is_relative_to(evidence_root.resolve()) or not path.is_file():
                raise ValueError('proof path missing/escapes evidence root')
            if hashlib.sha256(path.read_bytes()).hexdigest() != p[kind + 'Sha256']:
                raise ValueError('proof bytes changed: ' + kind)
        bounds=wasm_bounds((evidence_root/p['wasmPath']).read_bytes())
        if p['staticDataEnd']!=bounds['__data_end'] or p['heapBase']!=bounds['__heap_base'] or p['stackBytes']!=bounds['__stack_high']-bounds['__stack_low']:
            raise ValueError('claimed bounds differ from linked Wasm globals')
        stack=json.loads((evidence_root/p['stackAnalysisPath']).read_bytes())
        if bounded_stack(stack)>p['stackBytes'] or bounds['__stack_low']<p['staticDataEnd']:
            raise ValueError('analyzed stack does not fit linked stack reservation')
        required = p['heapBase'] + p['minimumWorkspaceBytes'] + p['guardBytes']
        for memory in (16, 32, 64):
            entry = {'initialMiB': memory, 'optimization': optimize, 'simd': simd,
                     'requiredBytes': required, 'status': 'eligible' if memory*MIB >= required else 'blocked-static-stack-workspace'}
            if entry['status'] == 'eligible':
                build = str(root / ('build-experiment-%d-%s-%s' % (memory, optimize[1:], 'simd' if simd else 'scalar')))
                flags = '-msimd128' if simd else ''
                entry['configure'] = ['emcmake', 'cmake', '-S', str(root), '-B', build,
                    '-DTERRAWASM_FEATURE_SET=wld', '-DTERRAX_VIEWER_WEB_PROFILE=ON',
                    '-DTERRAX_WEB_INITIAL_MEMORY=%d' % (memory*MIB),
                    '-DTERRAX_OPTIMIZE_FLAG=' + optimize, '-DCMAKE_C_FLAGS=' + flags,
                    '-DCMAKE_EXE_LINKER_FLAGS=' + flags]
                entry['build'] = ['cmake', '--build', build, '--target', 'terrax_world_wasm_web', '-j2']
            result.append(entry)
    return {'schema': 1, 'engineSourceCommit': proof['engineSourceCommit'], 'experiments': result,
            'defaultChanged': False, 'acceptance': ['byte-exact differential suite', 'peak memory', 'time to first result',
                'p95 step time', 'p95 input latency', 'growth count', 'Android/iOS/Web real-device measurements']}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--proof', type=Path, required=True)
    p.add_argument('--evidence-root', type=Path, required=True)
    p.add_argument('--engine-root', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    proof = json.loads(args.proof.read_bytes())
    actual = subprocess.check_output(['git', '-C', str(args.engine_root), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != proof['engineSourceCommit']:
        raise ValueError('proof is for another engine commit')
    if subprocess.check_output(['git', '-C', str(args.engine_root), 'status', '--porcelain'], text=True).strip():
        raise ValueError('experiments require a clean source checkout')
    plan = plans(proof, args.engine_root.resolve(), args.evidence_root)
    print(json.dumps(plan, indent=2))
    if args.execute:
        for entry in plan['experiments']:
            if entry['status'] == 'eligible':
                subprocess.run(entry['configure'], check=True)
                subprocess.run(entry['build'], check=True)

if __name__ == '__main__':
    main()
