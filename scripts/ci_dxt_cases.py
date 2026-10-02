"""Original independent DXT branch fixtures; no game assets."""
import struct,json,tempfile,subprocess,sys
from pathlib import Path
from PIL import Image
rd=b'Microsoft.Xna.Framework.Content.Texture2DReader'
def rgb(c):
 return tuple((v*255+(maxv//2))//maxv for v,maxv in [(c>>11,31),((c>>5)&63,63),(c&31,31)])
with tempfile.TemporaryDirectory() as d:
 p=Path(d);(p/'i').mkdir(); expects={}
 for fmt in [4,5,6]:
  for w,h in [(1,1),(3,2),(5,7)]:
   for reverse in [False,True]:
    c0,c1=(0x07e0,0xf800) if reverse else (0xf800,0x07e0)
    colors=[rgb(c0),rgb(c1)]
    if fmt==4 and c0<=c1:colors +=[tuple((a+b)//2 for a,b in zip(*colors)),(0,0,0)]
    else:colors +=[tuple((2*a+b)//3 for a,b in zip(*colors)),tuple((a+2*b)//3 for a,b in zip(*colors))]
    alpha0,alpha1=(32,224) if reverse else (224,32)
    alphas=[alpha0,alpha1]+([( (7-i)*alpha0+i*alpha1)//7 for i in range(1,7)] if alpha0>alpha1 else [((5-i)*alpha0+i*alpha1)//5 for i in range(1,5)]+[0,255])
    block=struct.pack('<HHI',c0,c1,sum((i%4)<<(2*i) for i in range(16)))
    if fmt==5:block=sum(i<<(4*i) for i in range(16)).to_bytes(8,'little')+block
    if fmt==6:block=bytes([alpha0,alpha1])+sum((i%8)<<(3*i) for i in range(16)).to_bytes(6,'little')+block
    data=block*((w+3)//4)*((h+3)//4)
    body=bytes([1,len(rd)])+rd+struct.pack('<I',0)+b'\0\1'+struct.pack('<iiiii',fmt,w,h,1,len(data))+data
    name=f'{fmt}-{w}-{h}-{reverse}'
    (p/'i'/f'{name}.xnb').write_bytes(b'XNBw\5\0'+struct.pack('<I',10+len(body))+body)
    exp=[]
    for y in range(h):
     for x in range(w):
      i=(y%4)*4+x%4; idx=i%4;a=255
      if fmt==4 and c0<=c1 and idx==3:a=0
      elif fmt==5:a=i*17
      elif fmt==6:a=alphas[i%8]
      exp+=list(colors[idx])+[a]
    expects[name]=bytes(exp)
 (p/'r').write_text(json.dumps({'input':str(p/'i')}))
 subprocess.run([sys.argv[1],sys.argv[2],'--request',str(p/'r'),'--output',str(p/'o')],check=True,timeout=30)
 for name,data in expects.items():assert Image.open(p/'o'/f'{name}.png').convert('RGBA').tobytes()==data,name
 print('Independent DXT palette/alpha/clipped edge checks: 18/18 passed')
