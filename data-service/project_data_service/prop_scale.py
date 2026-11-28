"""Deterministic nearest-neighbor derived placements from verified, static RGBA PNGs."""
import struct,zlib
from .prop_package_contract import need,sha,verify_media

PERCENTS=(5,10,20,30,40,50,60,70,80,90,100)

def percents(v):
    return type(v)is list and 1<=len(v)<=len(PERCENTS) and all(type(n)is int and n in PERCENTS for n in v) and len(set(v))==len(v)

def _pixels(raw,desc):
    verify_media(raw,desc,variant=True)
    w,h=desc['width'],desc['height']
    pieces=[];at=8
    while at<len(raw):
        n,k=struct.unpack_from('!I4s',raw,at)
        if k==b'IDAT':pieces.append(raw[at+8:at+8+n])
        at+=n+12
    scan=zlib.decompress(b''.join(pieces));stride=w*4
    lines=[];previous=bytearray(stride)
    for y in range(h):
        start=y*(stride+1);mode=scan[start];row=bytearray(scan[start+1:start+stride+1])
        if mode:
            for i in range(stride):
                a=row[i-4] if i>=4 else 0;b=previous[i];c=previous[i-4] if i>=4 else 0
                if mode==1:delta=a
                elif mode==2:delta=b
                elif mode==3:delta=(a+b)//2
                else:
                    p=a+b-c;da=abs(p-a);db=abs(p-b);dc=abs(p-c)
                    delta=a if da<=db and da<=dc else b if db<=dc else c
                row[i]=(row[i]+delta)&255
        previous=row;lines.append(row)
    return lines

def _write(kind,payload):
    return struct.pack('!I',len(payload))+kind+payload+struct.pack('!I',zlib.crc32(kind+payload))

def _resize(raw,desc,pixels,w,h):
    sw,sh=desc['width'],desc['height']
    if (w,h)==(sw,sh):return raw
    cols=[min(sw-1,(2*x+1)*sw//(2*w))*4 for x in range(w)]
    rows=[bytearray(b''.join(pixels[min(sh-1,(2*y+1)*sh//(2*h))][x:x+4] for x in cols)) for y in range(h)]
    if not any(any(row[3::4]) for row in rows):
        # A narrow visible feature can otherwise disappear entirely at 5%.
        first=next((x,y) for y,row in enumerate(pixels) for x,a in enumerate(row[3::4]) if a)
        x,y=first;dest=min(w-1,x*w//sw)*4
        rows[min(h-1,y*h//sh)][dest:dest+4]=pixels[y][x*4:x*4+4]
    return b'\x89PNG\r\n\x1a\n'+_write(b'IHDR',struct.pack('!IIBBBBB',w,h,8,6,0,0,0))+_write(b'IDAT',zlib.compress(b''.join(b'\0'+r for r in rows),6))+_write(b'IEND',b'')

def derive(detail,inputs,selection):
    need(percents(selection));variant=[];assets={}
    sources={lane:(raw,desc,_pixels(raw,desc)) for lane,(raw,desc) in inputs.items()}
    for n in sorted(selection):
        w,h=(max(1,(detail[d]*n+50)//100) for d in ('width','height'))
        r={'scalePercent':n}
        for lane,(raw,desc,pixels) in sources.items():
            baked=_resize(raw,desc,pixels,w,h)
            key=sha(baked);descriptor={'assetId':key+'.png','sha256':key,'bytes':len(baked),'width':w,'height':h,
                                        'mediaType':'image/png','frameDurationsMs':[]}
            r[lane]=descriptor;assets[descriptor['assetId']]=baked
        variant.append(r)
    return variant,assets
