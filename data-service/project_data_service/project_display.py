"""No-model, separately revisioned project display owned by Data Service."""
import base64, hashlib, json, os, struct, tempfile, zlib
from .database import DataError, RevisionConflict, utc_now

def validate_png(b):
    if not 8 < len(b) <= 2097152 or b[:8] != b'\x89PNG\r\n\x1a\n': raise DataError('invalid icon PNG')
    offset, width, height, channels, compressed, ended = 8, 0, 0, 0, bytearray(), False
    while offset+12 <= len(b):
        n = struct.unpack('>I',b[offset:offset+4])[0]; end=offset+n+12
        if end>len(b): raise DataError('invalid PNG chunk')
        kind=b[offset+4:offset+8]; data=b[offset+8:offset+8+n]
        if zlib.crc32(kind+data)&0xffffffff != struct.unpack('>I',b[offset+8+n:end])[0]: raise DataError('invalid PNG CRC')
        if offset==8 and kind!=b'IHDR': raise DataError('invalid PNG header')
        if kind==b'IHDR':
            if width or n!=13: raise DataError('invalid PNG header')
            width,height,depth,color,compression,filter_,interlace=struct.unpack('>IIBBBBB',data)
            channels={2:3,6:4}.get(color,0)
            if not 16<=width<=512 or height!=width or depth!=8 or not channels or compression or filter_ or interlace: raise DataError('invalid PNG dimensions')
        elif kind==b'IDAT': compressed.extend(data)
        elif kind==b'IEND':
            if n or end!=len(b): raise DataError('invalid PNG end')
            ended=True; break
        elif kind in (b'acTL',b'fcTL',b'fdAT') or kind[:1].isupper(): raise DataError('unsupported PNG chunk')
        offset=end
    if not ended or not compressed: raise DataError('incomplete PNG')
    maximum=(width*channels+1)*height; decoder=zlib.decompressobj(); raw=decoder.decompress(compressed,maximum+1)
    if len(raw)!=maximum or not decoder.eof or decoder.unused_data or any(raw[i*(width*channels+1)]>4 for i in range(height)): raise DataError('invalid PNG pixels')

class ProjectDisplayMixin:
    def _display_init(self):
        with self.store.lock,self.store.connection:
            self.store.connection.execute('CREATE TABLE IF NOT EXISTS project_display(project_id TEXT PRIMARY KEY REFERENCES projects(id),revision INTEGER NOT NULL,icon_json TEXT,request_id TEXT NOT NULL)')
    def _display_import_pending(self):
        packet=self.storage.root/'database'/'project-display-import.v1.json'
        if packet.exists():
            if packet.is_symlink() or packet.stat().st_mode & 0o077 or packet.stat().st_size>8*1024*1024: raise DataError('unsafe display import packet')
            self.absorb_project_display(json.loads(packet.read_text()))
    def absorb_project_display(self,p):
        """Owner-only startup import. No network import route and no mutation replay."""
        if set(p)!={'schemaVersion','display','assets','receipts','audit','requests'} or p['schemaVersion']!='pds-project-display-absorb.v1': raise DataError('invalid display import schema')
        expected={'display':{'project_id','revision','icon_json','request_id'},'assets':{'id','project_id','kind','relative_path','sha256','size','mime_type','original_name','origin','revision','status','created_at','updated_at','base64'},'receipts':{'request_id','operation','request_hash','status_code','response_json','created_at'},'audit':{'sequence','request_id','actor','operation','resource_type','resource_id','before_revision','after_revision','reason','details_json','created_at'},'requests':{'id','actor','digest','payload','state','receipt','error','fence','lease'}}
        for name,keys in expected.items():
            for item in ([p[name]] if name=='display' else p[name]):
                if set(item)!=keys: raise DataError('invalid import columns')
        d=p['display']; receipts={r['request_id']:r for r in p['receipts']}; requests={r['id']:r for r in p['requests']}
        if d['project_id']!='ProjectBS' or d['revision']!=11 or len(receipts)!=11 or len(requests)!=11 or len(p['audit'])!=11 or len(p['assets'])!=3: raise DataError('invalid import scope')
        assets={}; binary={}
        for item in p['assets']:
            a=dict(item); b=base64.b64decode(a.pop('base64'),validate=True); validate_png(b); h=hashlib.sha256(b).hexdigest()
            if a['id']!='project-icon.'+h or a['sha256']!=h or a['relative_path']!='assets/project-display/'+h+'.png' or a['size']!=len(b) or a['project_id']!='ProjectBS' or a['kind']!='project-icon' or a['mime_type']!='image/png': raise DataError('invalid imported asset')
            assets[a['id']]=a; binary[h]=b
        if len(assets)!=3: raise DataError('duplicate imported asset')
        previous=0
        for a in p['audit']:
            r=receipts.get(a['request_id']); q=requests.get(a['request_id'])
            if not r or not q or a['operation']!='project.display' or a['resource_id']!='ProjectBS' or a['resource_type']!='project-display' or a['before_revision']!=previous or a['after_revision']!=previous+1: raise DataError('invalid imported audit chain')
            receipt=json.loads(r['response_json']); icon=receipt['icon']
            if r['operation']!='project.display' or r['status_code']!=200 or receipt['projectId']!='binari' or receipt['requestId']!=a['request_id'] or receipt['revision']!=previous+1 or q['state']!='applied' or q['lease'] or q['error'] or json.loads(q['receipt'])!=receipt or q['actor']!=a['actor']: raise DataError('invalid imported receipt')
            if icon and (icon['assetId'] not in assets or icon['sha256']!=assets[icon['assetId']]['sha256'] or icon['size']!=assets[icon['assetId']]['size']): raise DataError('unbound imported icon')
            payload=json.loads(q['payload'])
            if payload['projectId']!='binari' or payload['expectedRevision']!=previous or hashlib.sha256(q['payload'].encode()).hexdigest()!=q['digest'] or 'icon.'+hashlib.sha256((q['actor']+'\n'+payload['idempotencyKey']).encode()).hexdigest()!=q['id']: raise DataError('invalid imported request identity')
            payload.update(requestId=q['id'],actor=q['actor'])
            if payload['icon']: payload['icon']['base64']=base64.b64encode(binary[payload['icon']['sha256']]).decode()
            if self._request_hash(payload)!=r['request_hash']: raise DataError('invalid imported request hash')
            previous+=1
        last=json.loads(receipts[d['request_id']]['response_json'])
        if last['revision']!=11 or last['icon']!=json.loads(d['icon_json']): raise DataError('invalid current imported display')
        digest=self._request_hash(p); key='display.absorb.5280.rev11.20261011'
        self._display_init()
        with self.store.lock:
            old=self.store.connection.execute('SELECT request_hash FROM idempotency_keys WHERE request_id=?',(key,)).fetchone()
            if old and old[0]!=digest: raise DataError('import packet changed')
            if old: return {'replayed':True,'revision':self.get_project_display()['revision']}
            if self.store.connection.execute('SELECT 1 FROM project_display').fetchone(): raise DataError('destination display already exists')
            for table,items,pk in [('assets',list(assets.values()),'id'),('idempotency_keys',p['receipts'],'request_id')]:
                for item in items:
                    row=self.store.connection.execute('SELECT * FROM '+table+' WHERE '+pk+'=?',(item[pk],)).fetchone()
                    if row and dict(row)!=item: raise DataError('import destination collision')
        parent=self.storage.root/'assets'/'project-display'
        if any(x.is_symlink() for x in (self.storage.root,self.storage.root/'assets',parent)): raise DataError('symlink rejected')
        parent.mkdir(exist_ok=True)
        for h,b in binary.items():
            path=parent/(h+'.png')
            if path.is_symlink(): raise DataError('symlink rejected')
            if path.exists():
                if path.read_bytes()!=b: raise DataError('import asset collision')
            else:
                fd,tmp=tempfile.mkstemp(prefix='.import-',dir=parent)
                try:
                    with os.fdopen(fd,'wb') as stream: stream.write(b); stream.flush(); os.fsync(stream.fileno())
                    os.link(tmp,path)
                finally: os.unlink(tmp)
        fd=os.open(parent,os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
        def work(c):
            if not c.execute("SELECT 1 FROM projects WHERE id='ProjectBS'").fetchone() or c.execute('SELECT 1 FROM project_display').fetchone(): raise DataError('destination display conflict')
            c.execute('CREATE TABLE IF NOT EXISTS project_display_import_lineage(source_sequence INTEGER PRIMARY KEY, destination_sequence INTEGER NOT NULL, request_id TEXT NOT NULL, manifest_sha256 TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS project_display_requests(id TEXT PRIMARY KEY,actor TEXT NOT NULL,digest TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,receipt TEXT,error TEXT,fence INTEGER NOT NULL DEFAULT 0,lease INTEGER NOT NULL DEFAULT 0)')
            for table,items in [('assets',list(assets.values())),('idempotency_keys',p['receipts']),('project_display_requests',p['requests'])]:
                for item in items:
                    columns=list(item); c.execute('INSERT OR IGNORE INTO '+table+' ('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(item[x] for x in columns))
            for item in p['audit']:
                values=dict(item); source=values.pop('sequence'); columns=list(values)
                cursor=c.execute('INSERT INTO audit_events ('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(values[x] for x in columns))
                c.execute('INSERT INTO project_display_import_lineage VALUES(?,?,?,?)',(source,cursor.lastrowid,values['request_id'],digest))
            c.execute('INSERT INTO project_display VALUES(?,?,?,?)',tuple(d[x] for x in ('project_id','revision','icon_json','request_id')))
            self.store.audit(c,request_id=key,actor='local.pds-display-integration',operation='project.display.absorb',resource_type='project-display',resource_id='ProjectBS',before_revision=None,after_revision=11,reason='Preserve PIS display history in PDS',details={'manifestSha256':digest,'sourcePort':5280,'receipts':11,'assets':3})
            return 200,{'revision':11,'receipts':11,'assets':3,'manifestSha256':digest}
        return self.store.idempotent_write(key,'project.display.absorb',digest,'local.pds-display-integration','Preserve PIS display history in PDS',work)
    def get_project_display_history(self):
        self._display_init()
        with self.store.lock:
            return [dict(r) for r in self.store.connection.execute("SELECT request_id,actor,before_revision,after_revision,created_at FROM audit_events WHERE operation='project.display' AND resource_id='ProjectBS' ORDER BY after_revision")]
    def get_project_display(self):
        self._display_init()
        with self.store.lock:
            r=self.store.connection.execute("SELECT * FROM project_display WHERE project_id='ProjectBS'").fetchone()
            return {'projectId':'binari','revision':r['revision'] if r else 0,'icon':json.loads(r['icon_json']) if r and r['icon_json'] else None,'requestId':r['request_id'] if r else None}
    def get_project_display_receipt(self,request_id):
        with self.store.lock:
            r=self.store.connection.execute("SELECT response_json FROM idempotency_keys WHERE request_id=? AND operation='project.display'",(request_id,)).fetchone()
            return json.loads(r[0]) if r else None
    def get_project_display_icon(self,digest):
        import re
        if not re.fullmatch('[a-f0-9]{64}',digest): raise DataError('invalid icon hash')
        with self.store.lock:
            r=self.store.connection.execute("SELECT size FROM assets WHERE id=? AND project_id='ProjectBS' AND kind='project-icon'",('project-icon.'+digest,)).fetchone()
            if not r: return None
            parent=self.storage.root/'assets'/'project-display'; path=parent/(digest+'.png')
            if any(p.is_symlink() for p in (self.storage.root,self.storage.root/'assets',parent,path)): raise DataError('symlink rejected')
            try:
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
                with os.fdopen(fd,'rb') as f: b=f.read(2097153)
            except OSError: raise DataError('canonical icon asset unavailable')
            if len(b)!=r['size'] or hashlib.sha256(b).hexdigest()!=digest: raise DataError('canonical icon asset corrupt')
            validate_png(b)
            return {'sha256':digest,'size':len(b),'mime':'image/png','base64':base64.b64encode(b).decode('ascii')}
    def put_project_display(self,p):
        if set(p)!={'projectId','action','expectedVersion','expectedRevision','idempotencyKey','icon','requestId','actor'} or p['projectId']!='binari' or p['action'] not in ('replace','reset') or type(p['expectedRevision']) is not int or p['expectedRevision']<0: raise DataError('invalid display request')
        self._display_init(); icon=None
        if p['action']=='replace':
            v=p['icon']
            if set(v)!={'base64','sha256','size','mime'} or v['mime']!='image/png': raise DataError('invalid icon')
            b=base64.b64decode(v['base64'],validate=True); validate_png(b); digest=hashlib.sha256(b).hexdigest()
            if digest!=v['sha256'] or len(b)!=v['size']: raise DataError('icon hash mismatch')
            parent=self.storage.root/'assets'/'project-display'
            for ancestor in [self.storage.root,self.storage.root/'assets',parent]:
                if ancestor.is_symlink(): raise DataError('symlink rejected')
            parent.mkdir(exist_ok=True); path=parent/(digest+'.png')
            if path.is_symlink(): raise DataError('symlink rejected')
            if path.exists():
                if path.read_bytes()!=b: raise DataError('icon asset corrupt')
            else:
                fd,tmp=tempfile.mkstemp(prefix='.icon-',dir=parent)
                try:
                    with os.fdopen(fd,'wb') as f: f.write(b); f.flush(); os.fsync(f.fileno())
                    os.link(tmp,path)
                except FileExistsError:
                    if path.is_symlink() or path.read_bytes()!=b: raise DataError('icon asset corrupt')
                finally: os.unlink(tmp)
                fd=os.open(parent,os.O_RDONLY)
                try: os.fsync(fd)
                finally: os.close(fd)
            icon={'sha256':digest,'size':len(b),'mime':'image/png','assetId':'project-icon.'+digest}
        elif p['icon'] is not None: raise DataError('reset icon must be null')
        def work(c):
            if not c.execute("SELECT 1 FROM projects WHERE id='ProjectBS'").fetchone(): raise DataError('canonical project missing')
            r=c.execute("SELECT revision FROM project_display WHERE project_id='ProjectBS'").fetchone(); revision=r[0] if r else 0
            if revision!=p['expectedRevision']: raise RevisionConflict('display revision conflict')
            next_revision=revision+1
            if icon:
                now=utc_now(); c.execute('INSERT OR IGNORE INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(icon['assetId'],'ProjectBS','project-icon','assets/project-display/'+icon['sha256']+'.png',icon['sha256'],icon['size'],'image/png','project-icon.png','project-display-request',1,'active',now,now))
            c.execute("INSERT INTO project_display VALUES('ProjectBS',?,?,?) ON CONFLICT(project_id) DO UPDATE SET revision=excluded.revision,icon_json=excluded.icon_json,request_id=excluded.request_id",(next_revision,json.dumps(icon) if icon else None,p['requestId']))
            self.store.audit(c,request_id=p['requestId'],actor=p['actor'],operation='project.display',resource_type='project-display',resource_id='ProjectBS',before_revision=revision,after_revision=next_revision,reason='Project icon request',details={'action':p['action'],'icon':icon})
            receipt={'projectId':'binari','requestId':p['requestId'],'revision':next_revision,'icon':icon}
            c.execute('CREATE TABLE IF NOT EXISTS project_display_requests(id TEXT PRIMARY KEY,actor TEXT NOT NULL,digest TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,receipt TEXT,error TEXT,fence INTEGER NOT NULL DEFAULT 0,lease INTEGER NOT NULL DEFAULT 0)')
            normalized={k:v for k,v in p.items() if k not in ('requestId','actor')}
            if normalized['icon']: normalized['icon']={k:v for k,v in normalized['icon'].items() if k!='base64'}
            c.execute('INSERT INTO project_display_requests VALUES(?,?,?,?,?,?,NULL,0,0)',(p['requestId'],p['actor'],self._request_hash(p),json.dumps(normalized,ensure_ascii=False),'source_applied',json.dumps(receipt,ensure_ascii=False)))
            return 200,receipt
        return self.store.idempotent_write(p['requestId'],'project.display',self._request_hash(p),p['actor'],'Project icon request',work)
