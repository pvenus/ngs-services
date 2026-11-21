"""Shared strict media byte contract. Keep both service copies identical."""
import base64, hashlib, io, json, re, struct, wave, zlib
MAX_BYTES=32*1024*1024
SCOPE=('requestId','attemptId','fencingToken','capability','projectId','targetId','expectedRevision','requestHash')
def wire(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def sha(v):return hashlib.sha256(v).hexdigest()
def require(v,message):
 if not v:raise ValueError(message)
def _validate(p):
 require(isinstance(p,dict)and set(p)==set(SCOPE)|{'artifact','provenance','claim'},'media schema')
 for k in ('requestId','attemptId','projectId','targetId'):
  require(isinstance(p[k],str)and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}',p[k]),'media identifier')
 require(isinstance(p['fencingToken'],str)and re.fullmatch('[A-Za-z0-9._:-]{1,200}',p['fencingToken']),'media fence')
 require(isinstance(p['expectedRevision'],str)and re.fullmatch('[A-Za-z0-9._:-]{1,200}',p['expectedRevision']),'media revision')
 require(isinstance(p['requestHash'],str)and re.fullmatch('[a-f0-9]{64}',p['requestHash']),'media request hash')
 a=p['artifact'];require(isinstance(a,dict)and set(a)=={'sha256','bytes','mimeType','base64'},'media artifact')
 require(type(a['bytes'])is int and 0<a['bytes']<=MAX_BYTES,'media size')
 require(isinstance(a['base64'],str)and len(a['base64'])<=4*((MAX_BYTES+2)//3),'media encoding size')
 data=base64.b64decode(a['base64'],validate=True)
 require(len(data)==a['bytes']and sha(data)==a['sha256'],'media content hash')
 require(isinstance(p['provenance'],dict)and set(p['provenance'])=={'model'}and isinstance(p['provenance']['model'],str)and re.fullmatch('[A-Za-z0-9._:/-]{1,120}',p['provenance']['model']),'media provenance')
 if p['capability']=='music.generate':
  require(a['mimeType']=='audio/wav','media mime')
  with wave.open(io.BytesIO(data),'rb')as f:
   require(f.getnchannels()==2 and f.getsampwidth()==2 and f.getframerate()==48000 and f.getcomptype()=='NONE'and 480000<=f.getnframes()<=1440000,'media wav profile')
   require(len(f.readframes(f.getnframes()))==f.getnframes()*4,'media truncated wav')
  ext='wav'
 else:
  require(p['capability']in ('image.generate','image.edit')and a['mimeType']=='image/png','media capability')
  png(data)
  ext='png'
 return data,ext


def validate(p):
 try:return _validate(p)
 except (wave.Error,EOFError,zlib.error,struct.error)as exc:raise ValueError("media malformed bytes")from exc

def _png(data):
 require(data[:8]==b'\x89PNG\r\n\x1a\n','media png');offset=8;types=[];compressed=[]
 while offset<len(data):
  require(offset+12<=len(data),'media png chunk');n=struct.unpack('>I',data[offset:offset+4])[0];kind=data[offset+4:offset+8];chunk=data[offset+8:offset+8+n]
  require(offset+12+n<=len(data)and zlib.crc32(kind+chunk)&0xffffffff==struct.unpack('>I',data[offset+8+n:offset+12+n])[0],'media png crc')
  require(re.fullmatch(b'[A-Za-z]{4}',kind)and (kind[0]&32 or kind in (b'IHDR',b'PLTE',b'IDAT',b'IEND')),'media png chunk type')
  if kind==b'PLTE':require(0<n<=768 and n%3==0 and b'PLTE'not in types and b'IDAT'not in types,'media png palette')
  if kind==b'IDAT'and b'IDAT'in types:require(types[-1]==b'IDAT','media png idat order')
  types.append(kind)
  if kind==b'IHDR':
   require(len(types)==1 and n==13,'media png header');w,h,depth,color,compression,filtering,interlace=struct.unpack('>IIBBBBB',chunk)
   require(0<w<=8192 and 0<h<=8192 and w*h<=16777216,'media png dimensions')
   require(color in (2,6)and depth in ({0:(1,2,4,8,16),2:(8,16),3:(1,2,4,8),4:(8,16),6:(8,16)}[color])and compression==filtering==interlace==0,'media png profile')
   rowbytes=(w*{0:1,2:3,3:1,4:2,6:4}[color]*depth+7)//8;expected=(rowbytes+1)*h
   require(expected<=128*1024*1024,'media png decoded size')
  if kind==b'IDAT':compressed.append(chunk)
  offset+=12+n
  if kind==b'IEND':require(n==0 and offset==len(data),'media png end');break
 require(types and types[0]==b'IHDR'and types[-1]==b'IEND'and compressed,'media png structure')
 require(color!=3 or b'PLTE'in types,'media png palette')
 dec=zlib.decompressobj();raw=dec.decompress(b''.join(compressed),expected+1)
 require(len(raw)==expected and dec.eof and not dec.unused_data and not dec.unconsumed_tail,'media png compression')
 require(all(raw[i]<=4 for i in range(0,len(raw),rowbytes+1)),'media png scanline filter')

def png(data):
 try:return _png(data)
 except (zlib.error,struct.error,UnboundLocalError)as exc:raise ValueError("media malformed png")from exc
