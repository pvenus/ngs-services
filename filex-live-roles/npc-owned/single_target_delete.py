"""One recoverable storage target, no recursive deletion or domain interpretation."""
from contextlib import ExitStack
import hashlib
import json
import os
import re
import stat

from server import Conflict

SEGMENT=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',re.ASCII)
OPERATION=re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,179}',re.ASCII)
NOFOLLOW=os.O_NOFOLLOW
DIRFD=os.O_RDONLY|os.O_DIRECTORY|NOFOLLOW

def path_parts(value):
    if type(value)is not str or not 0<len(value)<=1024:raise ValueError('storage target path')
    parts=value.split('/')
    if not all(SEGMENT.fullmatch(x) for x in parts):raise ValueError('storage target segment')
    return parts

def validate_target(value):
    if type(value)is not dict or set(value)!={'root','path','kind'} or value['kind'] not in ('file','directory'):
        raise ValueError('storage target shape')
    path_parts(value['root']);path_parts(value['path'])
    return value

def entry(fd,name):
    try:return os.stat(name,dir_fd=fd,follow_symlinks=False)
    except FileNotFoundError:return None

def identity(s):
    return [s.st_dev,s.st_ino,stat.S_IFMT(s.st_mode)]

def open_dir(stack,fd,name,dev,*,private=False):
    d=os.open(name,DIRFD,dir_fd=fd)
    stack.callback(os.close,d)
    st=os.fstat(d)
    if st.st_dev!=dev or (private and (st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700)):
        raise Conflict('storage target volume or quarantine privacy')
    return d

def private_mkdir(fd,name):
    try:os.mkdir(name,mode=0o700,dir_fd=fd)
    except FileExistsError:pass
    else:os.fsync(fd)

def read_record(fd):
    try:f=os.open('operation.json',os.O_RDONLY|NOFOLLOW,dir_fd=fd)
    except FileNotFoundError:return None
    with os.fdopen(f,'rb') as file:
        s=os.fstat(file.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_uid!=os.getuid() or s.st_mode&0o077:
            raise Conflict('storage quarantine record permissions')
        raw=file.read(4097)
        if len(raw)>4096:raise Conflict('storage quarantine record length')
        return json.loads(raw)

def put_record(fd,record):
    # Exclusive temp and durable atomic publication before moving the target.
    # A previous interrupted temp contains no authority and is not overwritten.
    tmp='record-'+os.urandom(8).hex()
    f=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|NOFOLLOW,0o600,dir_fd=fd)
    with os.fdopen(f,'wb') as file:
        file.write(json.dumps(record,separators=(',',':'),sort_keys=True).encode())
        file.flush();os.fsync(file.fileno())
    os.rename(tmp,'operation.json',src_dir_fd=fd,dst_dir_fd=fd)
    os.fsync(fd)

class SingleTargetDelete:
    def __init__(self,store,root):
        path_parts(root)
        self.store,self.root=store,root

    def verify_recovery_locked(self,command,recovery_id):
        """Verify the quarantined original without disturbing a new live target."""
        target=validate_target(command.get('target'));oid=command.get('operationId')
        if target['root']!=self.root or type(oid)is not str or not OPERATION.fullmatch(oid):
            raise ValueError('storage recovery binding')
        expected=hashlib.sha256(json.dumps([self.root,oid],separators=(',',':')).encode()).hexdigest()
        if recovery_id!=expected:raise Conflict('storage recovery ID changed')
        with ExitStack() as stack:
            root=os.open(self.store.root,DIRFD);stack.callback(os.close,root);dev=os.fstat(root).st_dev
            trash=open_dir(stack,root,'.storage-quarantine',dev,private=True)
            slot=open_dir(stack,trash,recovery_id,dev,private=True)
            record=read_record(slot);saved=entry(slot,'target')
            if (not record or record.get('state')!='moved' or
                (record.get('operationId'),record.get('target'))!=(oid,target) or
                saved is None or identity(saved)!=record.get('identity')):
                raise Conflict('storage recovery not verified')
        return True

    def remove(self,command):
        with self.store.lock():
            return self.remove_locked(command)

    def remove_locked(self,command):
        """For staged storage integration already holding the same writer lock."""
        if type(command)is not dict or set(command)!={'operationId','target'}:
            raise ValueError('storage delete command')
        target=validate_target(command['target']);oid=command['operationId']
        if target['root']!=self.root or type(oid)is not str or not OPERATION.fullmatch(oid):
            raise ValueError('storage delete binding')
        parts=path_parts(target['path']);kind=target['kind'];leaf=parts[-1]
        rid=hashlib.sha256(json.dumps([self.root,oid],separators=(',',':')).encode()).hexdigest()
        with ExitStack() as stack:
            root=os.open(self.store.root,DIRFD);stack.callback(os.close,root)
            dev=os.fstat(root).st_dev
            q='.storage-quarantine'
            # Do not create a recovery record or quarantine until a new target
            # was already validated, or a recorded operation exists.
            trash=slot=None;prior=None
            if entry(root,q) is not None:
                trash=open_dir(stack,root,q,dev,private=True)
                if entry(trash,rid) is not None:
                    slot=open_dir(stack,trash,rid,dev,private=True)
                    prior=read_record(slot)
            if prior is not None and (prior.get('operationId'),prior.get('target'),prior.get('state')) not in ((oid,target,'prepared'),(oid,target,'moved')):
                raise Conflict('storage delete operation reused')
            parent=root
            try:
                for component in parts[:-1]:parent=open_dir(stack,parent,component,dev)
                source=entry(parent,leaf)
            except FileNotFoundError:source=None
            saved=entry(slot,'target') if slot is not None else None
            if saved is not None:
                if prior is None or identity(saved)!=prior.get('identity') or source is not None:
                    raise Conflict('storage delete ambiguous replay')
                os.fsync(slot)
                os.fsync(parent)
                if prior['state']!='moved':put_record(slot,{**prior,'state':'moved'})
                return {'ok':True,'operationId':oid,'targetAbsent':True,'recoveryId':rid}
            if prior is not None and prior['state']=='moved':
                raise Conflict('storage delete recovery object missing')
            expected=stat.S_IFREG if kind=='file' else stat.S_IFDIR
            if source is None:raise FileNotFoundError('storage delete target absent')
            if stat.S_IFMT(source.st_mode)!=expected or source.st_dev!=dev or kind=='file' and source.st_nlink!=1:
                raise Conflict('storage delete target type')
            key=identity(source)
            if prior is not None and prior.get('identity')!=key:
                raise Conflict('storage delete target changed since prepare')
            if trash is None:
                private_mkdir(root,q);trash=open_dir(stack,root,q,dev,private=True)
            if slot is None:
                private_mkdir(trash,rid);slot=open_dir(stack,trash,rid,dev,private=True)
            record={'operationId':oid,'target':target,'identity':key,'state':'prepared'}
            if prior is None:put_record(slot,record)
            current=entry(parent,leaf)
            if current is None or identity(current)!=key or entry(slot,'target') is not None:
                raise Conflict('storage delete target changed before move')
            os.rename(leaf,'target',src_dir_fd=parent,dst_dir_fd=slot)
            os.fsync(slot);os.fsync(parent)
            moved=entry(slot,'target')
            if moved is None or identity(moved)!=key or entry(parent,leaf) is not None:
                raise Conflict('storage delete move unconfirmed')
            put_record(slot,{**record,'state':'moved'})
            return {'ok':True,'operationId':oid,'targetAbsent':True,'recoveryId':rid}
