"""Canonical prop package storage boundary; shared browser contract mirrored by tests."""
import hashlib,json,re,struct,unicodedata,zlib

ROOT='canonical-files/binari/props/'
MAX_MEDIA=16*1024*1024
MAX_MANIFEST=512*1024
MAX_REVISION=2**53-2
LANES=('detail','pixel')
KEYS={'schemaVersion','projectId','propId','revision','officialRequestId','requestSha256','designContextSha256',
      'title','parentSha256','media','interaction','animations'}

def need(value):
    if not value: raise ValueError('PROP_PACKAGE_INVALID')
def wire(v): return json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
def sha(raw): return hashlib.sha256(raw).hexdigest()
def digest(v): return sha(wire(v))
def keys(v,names): return type(v) is dict and set(v)==set(names)
def match(v,p): return type(v) is str and re.fullmatch(p,v) is not None
def hex64(v): return match(v,r'[a-f0-9]{64}')
def official(v): return match(v,r'request\.official\.[a-f0-9]{64}')
def prop_id(v): return type(v) is str and 2<=len(v)<=80 and match(v,r'[a-z0-9]+(?:[-._][a-z0-9]+)*')
def number(v,lo,hi):return type(v) is int and lo<=v<=hi
def title(v):return type(v) is str and 1<=len(v)<=160 and v==v.strip().strip('\ufeff').strip() and not any(unicodedata.category(c) in ('Cc','Cs','Zl','Zp') for c in v)

def pair(v,animated,small=False):
    has_spirit=not animated and keys(v,(*LANES,'spirit'))
    need(has_spirit or keys(v,LANES))
    for lane,m in v.items():
        limit=2048 if lane=='detail' else 512
        lo=2 if lane!='detail' and (small or has_spirit) else 12
        need(keys(m,('assetId','sha256','bytes','width','height','mediaType','frameDurationsMs')) and
             hex64(m['sha256']) and m['assetId']==m['sha256']+'.png' and number(m['bytes'],57,MAX_MEDIA) and
             number(m['width'],lo,limit) and number(m['height'],lo,limit) and type(m['frameDurationsMs']) is list)
        t=m['frameDurationsMs']
        need((m['mediaType']=='image/apng' and 2<=len(t)<=96 and all(number(n,10,1000) for n in t) and 100<=sum(t)<=10000)
             if animated else (m['mediaType']=='image/png' and not t))
        need(m['width']*m['height']*max(1,len(t))<=30_000_000)
    a,b=v['detail'],v['pixel']
    if has_spirit:
        need(all(v[lane][d]==(a[d]+2)//5 for lane in ('pixel','spirit') for d in ('width','height')))
    aw,ah,bw,bh=a['width'],a['height'],b['width'],b['height']
    need(bw<=aw and bh<=ah and bw*bh<aw*ah and abs(bw*ah-bh*aw)<=max(aw,ah) and a['frameDurationsMs']==b['frameDurationsMs'])

def manifest(v):
    need(keys(v,KEYS) and v['schemaVersion']=='ngs.prop-package.v1' and v['projectId']=='binari' and
         prop_id(v['propId']) and number(v['revision'],1,MAX_REVISION) and official(v['officialRequestId']) and
         hex64(v['requestSha256']) and hex64(v['designContextSha256']) and title(v['title']) and
         (v['parentSha256'] is None if v['revision']==1 else hex64(v['parentSha256'])) and
         type(v['animations']) is list and len(v['animations'])<=1024 and keys(v['interaction'],('pixelOpen','detailTrigger')))
    media=v['media'];pair(media,False)
    for key,action,lane in (('pixelOpen','open','pixel'),('detailTrigger','activate','detail')):
        r=v['interaction'][key];m=media[lane]
        if r is None:continue
        need(keys(r,('action','x','y','width','height')) and r['action']==action and
             number(r['x'],0,m['width']-1) and number(r['y'],0,m['height']-1) and
             number(r['width'],1,m['width']-r['x']) and number(r['height'],1,m['height']-r['y']))
    ids=set()
    for clip in v['animations']:
        need(keys(clip,('id','title','officialRequestId','requestSha256','media')) and prop_id(clip['id']) and
             clip['id'] not in ids and title(clip['title']) and official(clip['officialRequestId']) and hex64(clip['requestSha256']))
        ids.add(clip['id']);pair(clip['media'],True,'spirit' in media)
        for lane in LANES:
            need(all(clip['media'][lane][dimension]==media[lane][dimension] for dimension in ('width','height')))
    result=wire(v);need(len(result)<=MAX_MANIFEST);return result

def all_media(m):
    out={}
    for group in (m['media'],*(clip['media'] for clip in m['animations'])):
        for v in group.values():
            key=v['assetId'];need(key not in out or out[key]==v);out[key]=v
    return out

def _rgba(parts,w,h):
    size=h*(1+w*4);d=zlib.decompressobj()
    raw=d.decompress(b''.join(parts),size+1)
    need(len(raw)==size and d.eof and not d.unused_data and not d.unconsumed_tail)
    prior=bytearray(w);zero=visible=False;stride=1+w*4
    for y in range(h):
        row=raw[y*stride:(y+1)*stride];f=row[0];need(f<=4);cur=bytearray(w)
        for i,v in enumerate(row[4::4]):
            a=cur[i-1] if i else 0;b=prior[i];c=prior[i-1] if i else 0
            if f==1:v+=a
            if f==2:v+=b
            if f==3:v+=(a+b)//2
            if f==4:
                p=a+b-c;da=abs(p-a);db=abs(p-b);dc=abs(p-c);v+=a if da<=db and da<=dc else b if db<=dc else c
            cur[i]=x=v&255
            zero|=x==0;visible|=x>0
        prior=cur
    need(zero and visible)

def verify_media(raw,m):
    need(type(raw) is bytes and len(raw)==m['bytes'] and sha(raw)==m['sha256'] and raw[:8]==b'\x89PNG\r\n\x1a\n')
    times=m['frameDurationsMs'];w,h=m['width'],m['height']
    at=8;seen_data=False;end=False;apng=False;frames=[];seq=0
    while at<len(raw):
        need(at+12<=len(raw));n,kind=struct.unpack_from('!I4s',raw,at);finish=at+n+12;need(finish<=len(raw))
        p=raw[at+8:finish-4];need(zlib.crc32(raw[at+4:finish-4])==struct.unpack_from('!I',raw,finish-4)[0])
        if at==8:
            need(kind==b'IHDR' and n==13 and struct.unpack('!IIBBBBB',p)==(w,h,8,6,0,0,0))
            if not times:frames.append([])
        elif kind==b'acTL':
            need(times and not apng and not seen_data and not frames and n==8 and struct.unpack('!II',p)==(len(times),1));apng=True
        elif kind==b'fcTL':
            need(apng and n==26 and len(frames)<len(times) and (not frames or frames[-1]))
            c,fw,fh,x,y,dn,dd,dispose,blend=struct.unpack('!IIIIIHHBB',p)
            need((c,fw,fh,x,y,dispose,blend)==(seq,w,h,0,0,0,0) and dn*1000==times[len(frames)]*(dd or 100))
            seq+=1;frames.append([])
        elif kind==b'IDAT':
            need(len(frames)==1 and n>0);seen_data=True;frames[0].append(p)
        elif kind==b'fdAT':
            need(apng and seen_data and len(frames)>=2 and n>4 and struct.unpack_from('!I',p)[0]==seq)
            seq+=1;frames[-1].append(p[4:])
        elif kind==b'IEND':
            need(not n and finish==len(raw) and seen_data and len(frames)==(len(times) or 1) and all(frames));end=True
        else:need(not seen_data and kind in (b'sRGB',b'gAMA',b'cHRM',b'pHYs'))
        at=finish
    need(end)
    for chunks in frames:_rgba(chunks,w,h)
