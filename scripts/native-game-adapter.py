#!/usr/bin/env python3
"""Native half of the real-game differential probe, built from a clean checkout.
Requires existing GCC/zlib and a real TMRT snapshot bound to the resource release.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

HERE=Path(__file__).resolve().parent

def sha(data): return hashlib.sha256(data).hexdigest()
def run(argv): return subprocess.run(argv,check=True,stdout=subprocess.PIPE,stderr=sys.stderr)

def build(engine, build_dir, commit):
    helper=HERE/'native-game-probe.c'
    cmake=(engine/'CMakeLists.txt').read_text();names=[]
    for section in ('COMMON_SOURCES','WLD_SOURCES'):
        part=cmake.split('set('+section,1)[1].split('\n)',1)[0]
        names+=re.findall(r'\$\{SRC_DIR\}/([^"\s]+)',part)
    names+=['terra_zlib_bridge.c']
    source_digest=sha(b''.join((engine/'src'/name).read_bytes() for name in names)+helper.read_bytes()+cmake.encode()+
        b''.join(p.read_bytes() for p in sorted((engine/'include').glob('*')) if p.is_file()))
    stamp=build_dir/'build.json';binary=build_dir/'native-game-probe'
    if stamp.exists() and binary.exists():
        old=json.loads(stamp.read_bytes())
        if old=={'sourceCommit':commit,'sourceDigest':source_digest,'binarySha256':sha(binary.read_bytes())}:return binary
    build_dir.mkdir(parents=True,exist_ok=True);objects=[]
    common=['gcc','-std=gnu17','-O1','-g','-fno-pie','-DTERRAX_STATIC','-DTERRAX_TESTING',
        '-DTERRAWASM_FEATURE_WLD=1','-DTERRAWASM_FEATURE_PLR=0','-I'+str(engine/'include')]
    for name in names:
        obj=build_dir/(name+'.o');args=common+['-c',str(engine/'src'/name),'-o',str(obj)]
        if name=='terra_wld.c':args+=['-Dparse_header=terra_parse_header_unchecked']
        if name=='terra_txci.c':args+=['-DinflateInit2_=terra_inflateInit2_','-Dinflate=terra_inflate','-DinflateEnd=terra_inflateEnd']
        if name=='terra_mem.c':args+=['-fno-tree-loop-distribute-patterns']
        run(args);objects.append(str(obj))
    run(common+['-no-pie',str(helper),*objects,'-lz','-lm','-o',str(binary)])
    stamp.write_text(json.dumps({'sourceCommit':commit,'sourceDigest':source_digest,'binarySha256':sha(binary.read_bytes())}))
    return binary

def build_player(engine, build_dir, commit):
    helper=HERE/'native-player-probe.c'
    names=['terra_mem.c','terra_reader.c','terra_abi.c','terra_plr.c']
    source_digest=sha(b''.join((engine/'src'/name).read_bytes() for name in names)+helper.read_bytes()+
        b''.join(p.read_bytes() for p in sorted((engine/'include').glob('*')) if p.is_file()))
    stamp=build_dir/'player-build.json';binary=build_dir/'native-player-probe'
    if stamp.exists() and binary.exists():
        if json.loads(stamp.read_bytes())=={'sourceCommit':commit,'sourceDigest':source_digest,'binarySha256':sha(binary.read_bytes())}:return binary
    build_dir.mkdir(parents=True,exist_ok=True);objects=[]
    common=['gcc','-std=gnu17','-O1','-g','-fno-pie','-DTERRAX_STATIC','-DTERRAX_TESTING',
        '-DTERRAWASM_FEATURE_WLD=0','-DTERRAWASM_FEATURE_PLR=1','-I'+str(engine/'include')]
    for name in names:
        obj=build_dir/('player-'+name+'.o');args=common+['-c',str(engine/'src'/name),'-o',str(obj)]
        if name=='terra_mem.c':args+=['-fno-tree-loop-distribute-patterns']
        run(args);objects.append(str(obj))
    run(common+['-no-pie',str(helper),*objects,'-lm','-o',str(binary)])
    stamp.write_text(json.dumps({'sourceCommit':commit,'sourceDigest':source_digest,'binarySha256':sha(binary.read_bytes())}))
    return binary

def player_fields(binary, path):
    model=json.loads(subprocess.check_output([str(binary),str(path)]))
    names=('name','difficulty','hair','hairDye','skinVariant','statLife','statLifeMax','statMana','statManaMax','extraAccessory','taxMoney')
    value={name:model[name] for name in names}
    for name in ('inventory','armor','dyes','miscEquips','miscDyes','piggyBank','safe','defendersForge','voidVault'):
        value[name]=[{field:item[field] for field in ('itemType','stack','prefix','favorited')} for item in model[name]]
    return value

def header(raw):
    at=24
    # Both serializers write the same semantic fields after format + metadata.
    n=0;shift=0
    for _ in range(5):
        b=raw[at];at+=1;n|=(b&127)<<shift
        if not b&128:break
        shift+=7
    else: raise ValueError('invalid header string length')
    name=raw[at:at+n].decode();at+=n
    world_id,height,width=struct.unpack_from('<iii',raw,at);at+=12
    tiles,walls,*gradients=struct.unpack_from('<6H',raw,at);at+=12
    bitsets=[]
    for count in (tiles,walls):
        size=(count+7)//8;bitsets.append(raw[at:at+size]);at+=size
    counts=[]
    for count,bits in zip((tiles,walls),bitsets):
        values=[]
        for i in range(count):
            if bits[i//8]&(1<<(i%8)):values.append(raw[at]);at+=1
            else:values.append(1)
        counts.append(values)
    if at!=len(raw):raise ValueError('unexpected native header bytes')
    return dict(worldName=name,worldId=world_id,width=width,height=height,gradients=gradients,tileOptions=counts[0],wallOptions=counts[1])

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--engine-root',type=Path,required=True)
    p.add_argument('--build-dir',type=Path,required=True)
    p.add_argument('--category',required=True)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--assembly',type=Path,required=True)
    p.add_argument('--source-commit',required=True)
    p.add_argument('--scratch',type=Path,required=True)
    a=p.parse_args();engine=a.engine_root.resolve()
    commit=subprocess.check_output(['git','-C',str(engine),'rev-parse','HEAD'],text=True).strip()
    if commit!=a.source_commit or subprocess.check_output(['git','-C',str(engine),'status','--porcelain','--untracked-files=no'],text=True).strip():
        raise ValueError('native adapter requires exact clean engine commit')
    source=a.input.read_bytes();value=json.loads(source)
    if a.category=='player-conversion':
        binary=build_player(engine,a.build_dir.resolve(),commit)
        result={}
        for key in ('original','converted'):
            path=(a.input.parent/value[key]).resolve()
            if not path.is_relative_to(a.input.parent.resolve()):raise ValueError('player fixture escapes root')
            result[key]=player_fields(binary,path)
        a.scratch.mkdir(parents=True,exist_ok=False)
        (a.scratch/'native-fields.json').write_text(json.dumps(result))
        print(json.dumps(dict(side='native',inputSha256=sha(source),engineSourceCommit=commit,entryPoint='terra_plr_open_from_buffer+get_json',result=result)))
        return
    palette=(a.input.parent/value['tmrt']).resolve()
    if not palette.is_relative_to(a.input.parent.resolve()) or sha(palette.read_bytes())!=value['tmrtSha256']:
        raise ValueError('TMRT fixture mismatch')
    # The runtime snapshot header binds it to the exact resource manifest.
    if palette.read_bytes()[64:96].hex()!=value['resourceManifestSha256']:
        raise ValueError('TMRT belongs to another resource release')
    binary=build(engine,a.build_dir.resolve(),commit)
    args=[str(binary),a.category,str(palette),str(value.get('width',128)),str(value.get('height',512)),str(value.get('ground',120)),
        str(value.get('rock',240)),str(value.get('worldId',123)),value.get('worldName','Oracle')]
    fields=('y','active','type','frameX','frameY','wall','liquidAmount','liquidType','tileColor','wallColor','invisibleBlock','invisibleWall','fullbrightBlock','fullbrightWall')
    rows=value.get('rows',[])
    if any(row.get('nullTile') for row in rows):raise ValueError('null tile has no native world record; use a missing palette entry')
    stdin=(str(len(rows))+'\n'+'\n'.join(' '.join(str(int(row.get(k,0))) for k in fields) for row in rows)+'\n').encode()
    result=subprocess.run(args,input=stdin,stdout=subprocess.PIPE,stderr=sys.stderr,check=True).stdout
    a.scratch.mkdir(parents=True,exist_ok=False)
    (a.scratch/'native-output.bin').write_bytes(result)
    if a.category=='map-header':
        print('Actual native MAP format='+str(struct.unpack_from('<I',result)[0]),file=sys.stderr)
        result=header(result)
    else:result=json.loads(result)
    print(json.dumps(dict(side='native',inputSha256=sha(source),engineSourceCommit=commit,entryPoint=('txw_test_map_runtime_write_header' if a.category=='map-header' else 'write_tile+txw_test_render_preview_rows_to' if a.category=='background-rle' else 'txw_test_map_runtime_render_color'),result=result)))
if __name__=='__main__':main()
