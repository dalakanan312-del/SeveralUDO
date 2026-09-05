"""Create the SeveralUDO Sims 3 automatic Clock Sync .package from local game DLLs."""
from __future__ import annotations
import argparse, math, struct, subprocess, tempfile
from pathlib import Path

S3SA=0x073FAA07
XML=0x0333406C
WANT={
 "gameplay.package":{"Sims3StoreObjects.dll","Sims3GameplayObjects.dll","Sims3GameplaySystems.dll","UI.dll"},
 "scripts.package":{"SimIFace.dll","ScriptCore.dll","Sims3Metadata.dll"},
 "simcore.package":{"System.Xml.dll","System.dll","mscorlib.dll"},
}
def u(d,p): return struct.unpack_from("<I",d,p)[0]
def fnv(s):
 h=0xCBF29CE484222325
 for b in s.lower().encode("ascii"):
  h=(h*0x100000001B3)&0xffffffffffffffff;h^=b
 return h
def qfs(d,n):
 if len(d)<5 or d[:2] not in (b"\x10\xfb",b"\x10\xfa"):raise ValueError("Bad QFS header")
 z=(4 if d[0]&128 else 3)*(2 if d[0]&1 else 1);p=2+z
 if n and int.from_bytes(d[2:p],"big")!=n:raise ValueError("QFS size mismatch")
 o=bytearray()
 while p<len(d):
  c=d[p];p+=1;copy=off=0
  if c<0x80:
   x=d[p];p+=1;lit=c&3;copy=((c>>2)&7)+3;off=(((c<<3)&0x300)|x)+1
  elif c<0xc0:
   x,y=d[p],d[p+1];p+=2;lit=(x>>6)&3;copy=(c&0x3f)+4;off=(((x<<8)&0x3f00)|y)+1
  elif c<0xe0:
   x,y,z=d[p],d[p+1],d[p+2];p+=3;lit=c&3;copy=(((c<<6)&0x300)|z)+5;off=(((c<<12)&0x10000)|(x<<8)|y)+1
  elif c<0xfc:lit=((c&0x1f)+1)<<2
  else:lit=c&3
  o+=d[p:p+lit];p+=lit
  if copy:
   if off>len(o):raise ValueError("Bad QFS copy offset")
   for _ in range(copy):o.append(o[-off])
 if n and len(o)!=n:raise ValueError("QFS output size mismatch")
 return bytes(o)
def entries(path):
 d=path.read_bytes()
 if d[:4]!=b"DBPF":raise ValueError(f"{path} is not DBPF")
 count,ipos=u(d,36),u(d,64);itype=u(d,ipos);p=ipos+4;common=[None]*3
 for i,b in enumerate((1,2,4)):
  if itype&b:common[i]=u(d,p);p+=4
 out=[]
 for _ in range(count):
  v=[]
  for i,b in enumerate((1,2,4)):
   if itype&b:v.append(common[i])
   else:v.append(u(d,p));p+=4
  low,off,stored,mem,flags=u(d,p),u(d,p+4),u(d,p+8),u(d,p+12),u(d,p+16);p+=20
  raw=d[off:off+(stored&0x7fffffff)]
  if flags&0xffff==0xffff:raw=qfs(raw,mem)
  out.append((v[0],v[1],(v[2]<<32)|low,raw))
 return out
def decode_s3sa(r):
 p=1
 if r[0]>1:
  n=u(r,p);p+=4+2*n
 p+=68;n=struct.unpack_from("<H",r,p)[0];p+=2
 t=r[p:p+8*n];p+=8*n;encrypted=r[p:]
 if len(t)!=n*8:raise ValueError("Truncated S3SA table")
 seed=sum(struct.unpack_from("<Q",t,i)[0] for i in range(0,len(t),8));seed=(len(t)-1)&seed
 out=bytearray();cursor=0
 for block in range(n):
  buffer=bytearray(512)
  if not t[block*8]&1:
   buffer[:]=encrypted[cursor:cursor+512].ljust(512,b"\0");cursor+=512
   for j in range(512):
    old=buffer[j];buffer[j]^=t[seed];seed=(seed+old)%len(t)
  out+=buffer
 return bytes(out)
def asm_name(path):
 command='[Reflection.AssemblyName]::GetAssemblyName("'+str(path).replace('"','""')+'").Name'
 x=subprocess.run([r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe","-NoProfile","-NonInteractive","-Command",command],capture_output=True,text=True,check=True)
 return x.stdout.strip()+".dll"
def libraries(bin,dest):
 dest.mkdir(parents=True,exist_ok=True);found={};candidate=0
 for package,wanted in WANT.items():
  for typ,_g,_i,r in entries(bin/package):
   if typ!=S3SA:continue
   c=dest/f"candidate-{candidate}.dll";candidate+=1;c.write_bytes(decode_s3sa(r));name=asm_name(c)
   if name in wanted:c.replace(dest/name);found[name]=dest/name
   else:c.unlink()
 missing=set().union(*WANT.values())-set(found)
 if missing:raise RuntimeError("Missing local Sims 3 libraries: "+", ".join(sorted(missing)))
 return found
def s3sa(assembly):
 n=math.ceil(len(assembly)/512)
 return b"\1"+struct.pack("<I",0x2bc4f79f)+bytes(64)+struct.pack("<H",n)+bytes(n*8)+assembly.ljust(n*512,b"\0")
def pack(resources):
 h=bytearray(96);h[:4]=b"DBPF";struct.pack_into("<II",h,4,2,0);struct.pack_into("<I",h,36,len(resources));struct.pack_into("<I",h,44,4+32*len(resources));struct.pack_into("<I",h,60,3)
 p=bytearray(h);idx=[]
 for typ,group,inst,r in resources:
  off=len(p);p+=r;idx.append((typ,group,inst>>32,inst&0xffffffff,off,len(r)))
 struct.pack_into("<I",p,64,len(p));p+=struct.pack("<I",0)
 for e in idx:p+=struct.pack("<IIIIIIII",e[0],e[1],e[2],e[3],e[4],e[5]|0x80000000,e[5],0x00010000)
 return bytes(p)
def compile(source,libs,out):
 c=Path(r"C:\Windows\Microsoft.NET\Framework\v3.5\csc.exe")
 if not c.is_file():c=Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe")
 x=subprocess.run([str(c),"/target:library","/optimize+",f"/out:{out}",*[f"/reference:{v}" for k,v in libs.items() if k not in {"System.Xml.dll","System.dll","mscorlib.dll"}],str(source)],capture_output=True,text=True)
 if x.returncode:raise RuntimeError(x.stdout+x.stderr)
def main():
 root=Path(__file__).resolve().parents[1];pa=argparse.ArgumentParser(description=__doc__)
 pa.add_argument("--game-bin",type=Path,default=Path(r"C:\Program Files\EA Games\The Sims 3\Game\Bin"))
 pa.add_argument("--source",type=Path,default=root/"clock_bridge_sims3"/"SeveralUDOClockSync-Sims3-Source.cs")
 pa.add_argument("--output",type=Path,default=root/"clock_bridge_sims3"/"SeveralUDOSims3ClockSync.package")
 a=pa.parse_args()
 with tempfile.TemporaryDirectory(prefix="severaludo-sims3-") as temp:
  temp=Path(temp);dll=temp/"SeveralUDOSims3ClockSync.dll";compile(a.source,libraries(a.game_bin,temp/"libraries"),dll)
  xml=b'<?xml version="1.0" encoding="utf-8"?>\n<base>\n  <Current_Tuning>\n    <kInstantiator value="True" />\n  </Current_Tuning>\n</base>\n'
  a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(pack([(S3SA,0,fnv("SeveralUDO.Sims3.ClockSync.Assembly"),s3sa(dll.read_bytes())),(XML,0,fnv("SeveralUDO.Sims3.ClockSync"),xml)]))
 print(a.output)
if __name__=="__main__":main()
