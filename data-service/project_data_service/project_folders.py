"""Opt-in owner-local reads of two named canonical project files. No writes."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from contextlib import contextmanager

PREFIX='/v1/project-folders'
PROJECT=re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
SHA=re.compile(r'[a-f0-9]{64}')
LIMITS={'project.json':64*1024,'icon.png':2*1024*1024}
TYPES={'project.json':'application/json','icon.png':'image/png'}
MAX_GROUPS=64
MAX_COMPARE_BODY=32*1024
DIR_FLAGS=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
FILE_FLAGS=os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK


class FolderError(ValueError):
    def __init__(self,status,code):
        self.status=status
        super().__init__(code)


class ProjectFolders:
    def __init__(self,root,*,validate_mount=True):
        path=Path(root)
        if not path.is_absolute() or path.name!='canonical-files' or path.resolve()!=path:
            raise ValueError('PROJECT_FOLDERS_ROOT_INVALID')
        self.root=path
        if validate_mount:
            with self._root():pass

    @contextmanager
    def _root(self):
        try:
            # Resolve all components without following a link at any level.
            fd=os.open('/',DIR_FLAGS)
            try:
                for part in self.root.parts[1:]:
                    child=os.open(part,DIR_FLAGS,dir_fd=fd)
                    os.close(fd);fd=child
                yield fd
            finally:os.close(fd)
        except OSError as exc:
            raise FolderError(503,'PROJECT_FOLDERS_ROOT_UNAVAILABLE') from exc

    @contextmanager
    def _project(self,root,project):
        if not isinstance(project,str) or not PROJECT.fullmatch(project):
            raise FolderError(400,'PROJECT_ID_INVALID')
        try:fd=os.open(project,DIR_FLAGS,dir_fd=root)
        except FileNotFoundError as exc:raise FolderError(404,'PROJECT_FOLDER_NOT_FOUND') from exc
        except OSError as exc:raise FolderError(409,'PROJECT_FOLDER_UNSAFE') from exc
        try:
            yield fd
            try:current=os.stat(project,dir_fd=root,follow_symlinks=False)
            except FileNotFoundError as exc:raise FolderError(409,'PROJECT_FOLDER_CHANGED') from exc
            before=os.fstat(fd)
            if (before.st_dev,before.st_ino)!=(current.st_dev,current.st_ino):
                raise FolderError(409,'PROJECT_FOLDER_CHANGED')
        finally:os.close(fd)

    def projects(self):
        with self._root() as root:
            found=[]
            for project in sorted(os.listdir(root)):
                if not PROJECT.fullmatch(project):continue
                try:
                    with self._project(root,project) as folder:
                        s=os.stat('project.json',dir_fd=folder,follow_symlinks=False)
                        if stat.S_ISREG(s.st_mode) and s.st_nlink==1 and 0<s.st_size<=LIMITS['project.json']:
                            found.append(project)
                except FileNotFoundError:continue
                except FolderError as exc:
                    if exc.status not in (404,409):raise
            return found

    @staticmethod
    def _identity(s):
        return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_nlink)

    def _file(self,fd,name):
        if name not in LIMITS:raise FolderError(400,'PROJECT_FILE_NAME_INVALID')
        try:raw_fd=os.open(name,FILE_FLAGS,dir_fd=fd)
        except FileNotFoundError:return None
        except OSError as exc:raise FolderError(409,'PROJECT_FILE_UNSAFE') from exc
        with os.fdopen(raw_fd,'rb')as f:
            before=os.fstat(f.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1
                    or not 0<before.st_size<=LIMITS[name]):
                raise FolderError(409,'PROJECT_FILE_UNSAFE')
            raw=f.read(LIMITS[name]+1)
            try:current=os.stat(name,dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError as exc:raise FolderError(409,'PROJECT_FILE_CHANGED') from exc
            if len(raw)!=before.st_size or self._identity(before)!=self._identity(current) or self._identity(before)!=self._identity(os.fstat(f.fileno())):
                raise FolderError(409,'PROJECT_FILE_CHANGED')
        try:
            if name=='project.json':
                if not isinstance(json.loads(raw.decode('utf-8')),dict):raise ValueError()
            else:
                from .media_validation import png
                png(raw)
        except (ValueError,UnicodeError,OverflowError,RecursionError) as exc:
            raise FolderError(409,'PROJECT_FILE_INVALID') from exc
        return raw,hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _files(files):
        if type(files) is not list or len(files)>len(LIMITS):
            raise FolderError(400,'PROJECT_COMPARE_INVALID')
        present={}
        for item in files:
            if (type(item) is not dict or set(item)!={'name','sha256'} or not isinstance(item['name'],str)
                    or item['name'] not in LIMITS or item['name'] in present
                    or not isinstance(item['sha256'],str) or not SHA.fullmatch(item['sha256'])):
                raise FolderError(400,'PROJECT_COMPARE_INVALID')
            present[item['name']]=item['sha256']
        return present

    def _compare(self,root,project,present):
        with self._project(root,project) as fd:
            result=[]
            for name in LIMITS:
                value=self._file(fd,name)
                if name=='project.json' and value is None:
                    raise FolderError(404,'PROJECT_FOLDER_NOT_FOUND')
                if value is not None and value[1]!=present.get(name):
                    result.append({'name':name,'sha256':value[1]})
            return result

    def compare(self,value):
        if (set(value)!={'groups'} or type(value['groups']) is not list
                or len(value['groups'])>MAX_GROUPS):
            raise FolderError(400,'PROJECT_COMPARE_INVALID')
        requests={}
        for group in value['groups']:
            if (type(group) is not dict or set(group)!={'projectId','files'}
                    or not isinstance(group['projectId'],str) or not PROJECT.fullmatch(group['projectId'])
                    or group['projectId'] in requests):
                raise FolderError(400,'PROJECT_COMPARE_INVALID')
            requests[group['projectId']]=self._files(group['files'])
        changed=[];unavailable=[]
        if requests:
            try:
                with self._root() as root:
                    for project,present in requests.items():
                        try:files=self._compare(root,project,present)
                        except (FolderError,OSError):unavailable.append(project);continue
                        if files:changed.append({'projectId':project,'files':files})
            except FolderError:
                # Canonical mount unavailable, so none can be declared unchanged.
                return {'groups':[],'unavailable':list(requests)}
        return {'groups':changed,'unavailable':unavailable}

    def fetch(self,project,name,expected):
        with self._root() as root,self._project(root,project) as fd:
            value=self._file(fd,name)
        if value is None or value[1]!=expected:raise FolderError(409,'PROJECT_FILE_CHANGED')
        return value[0]


def configured(root,host,*,validate_mount=True):
    if host!='127.0.0.1':raise ValueError('PROJECT_FOLDERS_LOOPBACK_REQUIRED')
    return ProjectFolders(root,validate_mount=validate_mount)


def route(handler,parsed,folders):
    if (handler.server.server_address[0]!='127.0.0.1' or handler.client_address[0]!='127.0.0.1'
            or handler.headers.get('Host')!='127.0.0.1:'+str(handler.server.server_port)
            or handler.headers.get('Origin') is not None or handler.headers.get('Transfer-Encoding') is not None):
        return handler.respond(403,{'error':'PROJECT_FOLDERS_LOOPBACK_REQUIRED'})
    if folders is None:return handler.respond(404,{'error':'not_found'})
    method=handler.command
    try:
        if parsed.path==PREFIX:
            if method!='GET' or parsed.query:raise FolderError(405,'PROJECT_FOLDERS_READ_ONLY')
            return handler.respond(200,{'projects':folders.projects()})
        if parsed.path==PREFIX+'/compare' and method=='POST' and not parsed.query:
            handler.connection.settimeout(5)
            try:value=handler.read_json(max_body=MAX_COMPARE_BODY)
            except ValueError:raise FolderError(400,'PROJECT_COMPARE_INVALID') from None
            return handler.respond(200,folders.compare(value))
        parts=parsed.path.split('/')
        if len(parts)==6 and parts[4]=='files' and method=='GET':
            name=parts[5];sha=re.fullmatch(r'sha256=([a-f0-9]{64})',parsed.query)
            if sha is None:raise FolderError(400,'PROJECT_FILE_HASH_REQUIRED')
            raw=folders.fetch(parts[3],name,sha[1])
            handler.send_response(200)
            handler.send_header('Content-Type',TYPES[name])
            handler.send_header('Content-Length',str(len(raw)))
            handler.send_header('X-Content-Type-Options','nosniff')
            handler.send_header('Cache-Control','no-store')
            handler.end_headers();handler.wfile.write(raw)
            return
        return handler.respond(404,{'error':'not_found'})
    except FolderError as exc:return handler.respond(exc.status,{'error':str(exc)})
    except OSError:return handler.respond(503,{'error':'PROJECT_FOLDERS_UNAVAILABLE'})
