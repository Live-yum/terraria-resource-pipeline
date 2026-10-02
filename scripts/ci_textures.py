"""Original synthetic raw-XNB tests. Never downloads or executes game content."""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import zipfile
from io import BytesIO

from PIL import Image
from resource_pipeline.adapters import tree_digest
from resource_pipeline.pipeline import Pipeline
from resource_pipeline.preflight import RawInputPreflight
from resource_pipeline.textures import TextureExtractor, TextureTool


def xnb(fmt=0, compressed=False, w=4, h=4):
    reader=b'Microsoft.Xna.Framework.Content.Texture2DReader'
    if fmt==0: data=bytes([255,0,0,255])*w*h
    else:
        color=struct.pack('<HHI',0xf800,0,0)
        data=({4:b'',5:b'\xff'*8,6:bytes([255,0])+b'\0'*6}[fmt])+color
    body=bytes([1,len(reader)])+reader+struct.pack('<I',0)+b'\0\1'+struct.pack('<iiiii',fmt,w,h,1,len(data))+data
    if not compressed:return b'XNBw\5\0'+struct.pack('<I',10+len(body))+body
    # Genuine LZX frame containing a stored block (no game assets/copyright data).
    bits='0'+'011'+format(len(body),'024b')+'0000'
    block=b''.join(struct.pack('<H',int(bits[i:i+16],2)) for i in range(0,len(bits),16))
    block+=struct.pack('<III',1,1,1)+body
    framed=b'\xff'+struct.pack('>HH',len(body),len(block))+block
    return b'XNBw\5\x80'+struct.pack('<II',14+len(framed),len(body))+framed


def compressed_huffman_xnb(*, bad_tree=False, overrun=False):
    raw=xnb(w=64,h=64)
    body=raw[10:]
    # Full canonical 512-symbol main tree, nine-bit codes. Pretree uses
    # symbols 0 and 8, each one bit; delta 8 changes zero lengths to 9.
    pretree=''.join('0001' if i in (0,8) else '0000' for i in range(20))
    if bad_tree: pretree='0'*80
    bits='0'+'001'+format(len(body),'024b')
    bits+=pretree+'1'*256+pretree+'1'*256+pretree+'0'*249
    first=len(body)-64*64*4+4
    bits+=''.join(format(b,'09b') for b in body[:first])
    remaining=len(body)-first
    # First match sets repeated offset R0=4 using position slot5, then
    # slot0 reuses it. Length8 => low length code6; no length footer.
    fresh=True
    while remaining>=8 or (overrun and remaining>0):
        symbol=256+((5 if fresh else 0)<<3)+6
        bits+=format(symbol,'09b')+('0' if fresh else '')
        fresh=False;remaining-=8
    bits+=''.join(format(b,'09b') for b in body[len(body)-remaining:]) if remaining else ''
    bits+='0'*32
    bits+='0'*((-len(bits))%16)
    block=b''.join(struct.pack('<H',int(bits[i:i+16],2)) for i in range(0,len(bits),16))
    assert len(block)<len(body)//2
    framed=b'\xff'+struct.pack('>HH',len(body),len(block))+block
    return b'XNBw\5\x80'+struct.pack('<II',14+len(framed),len(body))+framed


def with_lzx_trailer(raw, trailer=b'\0'*5):
    data=bytearray(raw+trailer)
    struct.pack_into('<I',data,6,len(data))
    return bytes(data)


def fixture_zip():
    stream=BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        z.writestr('TerrariaServer.exe',b'original synthetic inert bytes')
        z.writestr('Content/Images/Huffman.xnb',compressed_huffman_xnb())
        z.writestr('Content/Images/Terminated.xnb',with_lzx_trailer(compressed_huffman_xnb()))
        for fmt in (0,4,5,6):
            for compressed in (False,True):
                z.writestr(f'Content/Images/Test_{fmt}_{int(compressed)}.xnb',xnb(fmt,compressed))
    return stream.getvalue()


def check_pixels(output):
    for path in output.rglob('*.png'):
        with Image.open(path) as image:
            assert image.size in ((4,4),(64,64))
            assert image.convert('RGBA').tobytes()==bytes([255,0,0,255])*image.width*image.height, path


def check_surface_formats(receipt):
    expected = {f'Test_{fmt}_{int(compressed)}.xnb': fmt for fmt in (0,4,5,6) for compressed in (False,True)}
    expected['Huffman.xnb'] = 0
    expected['Terminated.xnb'] = 0
    assert len(receipt['images']) == len(expected)
    for row in receipt['images']:
        assert row['surfaceFormat'] == expected[Path(row['input']).name]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--dotnet',required=True,type=Path);parser.add_argument('--tool',required=True,type=Path);parser.add_argument('--sandbox',action='store_true');args=parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        if args.sandbox:
            from unittest.mock import patch
            from fastapi.testclient import TestClient
            from resource_pipeline.api import create_app
            with patch.dict(os.environ, {'RESOURCE_TEXTURE_TOOL_ROOT':str(args.tool),
                'RESOURCE_TEXTURE_TOOL_SHA256':tree_digest(args.tool),
                'RESOURCE_DOTNET_PATH':str(args.dotnet)}):
                app=create_app(root/'service',synchronous=True)
            pipeline=app.state.pipeline
            preflight=app.state.raw_preflight
            # Synthetic-only bounded diagnostics; production never exposes subprocess logs.
            original_popen=subprocess.Popen
            with tempfile.TemporaryFile() as diagnostics:
                def capture(*positional, **keywords):
                    keywords['stderr']=diagnostics
                    return original_popen(*positional, **keywords)
                with patch('resource_pipeline.adapters.subprocess.Popen',side_effect=capture):
                    with TestClient(app) as client:
                        response=client.post('/api/raw-jobs',files={'server_file':('combined-game.zip',fixture_zip(),'application/zip')})
                        assert response.status_code==202,response.text
                        result=response.json()
                if result.get('textures',{}).get('server',{}).get('status')!='TEXTURES_DECODED':
                    diagnostics.seek(0)
                    print('Synthetic sandbox stderr:',diagnostics.read(8192).decode('utf-8','replace'))
            job=result
            receipt=result['textures']['server']
            assert receipt['status']=='TEXTURES_DECODED',receipt
            assert receipt['imageCount']==10
            check_surface_formats(receipt)
            assert receipt['surfaceFormatCounts']=={'0':4,'4':2,'5':2,'6':2}
            assert receipt['isolation']=='bubblewrap-unshare-all'
            assert result['state']=='BLOCKED' and not result['extractionComplete']
            assert pipeline.current() is None
            check_pixels(pipeline.root/'jobs'/job['id']/'server-texture-job/output')
            e8=bytearray(xnb(compressed=True));e8[20]|=128
            for payload in (bytes(e8),compressed_huffman_xnb(bad_tree=True),compressed_huffman_xnb(overrun=True),with_lzx_trailer(xnb(compressed=True),b'\0'*4+b'X'),with_lzx_trailer(xnb(compressed=True),b'\0'*6)):
                stream=BytesIO()
                with zipfile.ZipFile(stream,'w') as package:package.writestr('Content/Images/Bad.xnb',payload)
                failed=preflight.submit((BytesIO(stream.getvalue()),'malformed.zip'))
                result=preflight.process(failed['id'])
                assert result['textures']['server']['status']=='BLOCKED',result
                assert pipeline.current() is None
            bad=TextureExtractor(TextureTool(args.tool,'0'*64,args.dotnet))
            try: bad.extract(pipeline.root/'jobs'/job['id']/'server-files',root/'bad')
            except ValueError: pass
            else: raise AssertionError('Unpinned tool accepted')
        else:
            # Test harness only, using generated fixtures; production always uses sandbox.
            inp=root/'input';inp.mkdir()
            with zipfile.ZipFile(BytesIO(fixture_zip())) as z:
                for name in z.namelist():
                    if name.endswith('.xnb'):(inp/Path(name).name).write_bytes(z.read(name))
            request=root/'request.json';request.write_text(json.dumps({'input':str(inp)}))
            command=[str(args.dotnet),str(args.tool/'TextureExtractor.dll'),'--request',str(request),'--output',str(root/'output')]
            subprocess.run(command,check=True,timeout=30)
            check_pixels(root/'output')
            receipt=json.loads((root/'output/texture-report.json').read_text());assert receipt['imageCount']==10
            check_surface_formats(receipt)
            e8=bytearray(xnb(compressed=True));e8[20]|=128
            badcases=[('nonzero-terminator',with_lzx_trailer(xnb(compressed=True),b'\0'*4+b'X')),('extra-trailing-byte',with_lzx_trailer(xnb(compressed=True),b'\0'*6)),('huffman-invalid-tree',compressed_huffman_xnb(bad_tree=True)),('match-overrun',compressed_huffman_xnb(overrun=True)),('e8-unsupported',bytes(e8))]
            for name,data in badcases+[('truncated',b'XNB'),('huge',xnb(w=4000000,h=1)[:100]),('invalidflags',xnb()[:5]+b'\x40'+xnb()[6:])]:
                for path in inp.iterdir():path.unlink()
                (inp/'bad.xnb').write_bytes(data)
                command[-1]=str(root/name)
                assert subprocess.run(command,timeout=10).returncode!=0,name
        print(json.dumps({'passed':True,'cases':10,'isolation':args.sandbox,'source':'original-synthetic-XNB','lzxFixture':'stored-and-verbatim-Huffman-with-LZ-matches','gameAssetsVerified':False}))

if __name__=='__main__':main()
