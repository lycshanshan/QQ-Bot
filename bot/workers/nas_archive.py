from __future__ import annotations

from datetime import UTC,datetime
import hashlib
import io
import json
import os
from pathlib import Path
import time
import uuid
import zipfile

from bot.integrations.nas import NasConfig,NasStorage


class ArchivePacks:
    def __init__(self,database,settings,threshold=10000000,max_wait=86400,keep_local=False):
        self.database,self.settings=database,settings
        self.threshold,self.max_wait=threshold,max_wait
        self.directory=settings.archives/'packs'
        self.keep_local=keep_local

    def zip_bytes(self,identifier,jobs):
        stream=io.BytesIO()
        manifest={'schema_version':1,'pack_id':identifier,'archives':[]}
        with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for job in jobs:
                source=self.settings.archives/job['filename']
                data=source.read_bytes()
                if hashlib.sha256(data).hexdigest()!=job['sha256']:raise ValueError('Source archive hash mismatch')
                archive.writestr(job['filename'],data)
                manifest['archives'].append({k:job[k] for k in ['id','conversation_id','filename','count','sha256']})
            archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,separators=(',',':')))
        return stream.getvalue()

    def pack_one(self,force=False):
        with self.database.transaction() as db:
            pack=db.execute("SELECT * FROM archive_packs WHERE status='building' ORDER BY created_at LIMIT 1").fetchone()
            if pack is None:
                first=db.execute("SELECT * FROM archive_jobs WHERE status='completed' AND pack_id IS NULL ORDER BY rowid LIMIT 1").fetchone()
                if first is None:return None
                candidates=[dict(r) for r in db.execute("SELECT * FROM archive_jobs WHERE status='completed' AND pack_id IS NULL AND conversation_id=? ORDER BY rowid LIMIT 20",(first['conversation_id'],))]
                identifier=uuid.uuid4().hex
                selected=[]
                size=0
                reached=False
                for job in candidates:
                    candidate=self.zip_bytes(identifier,selected+[job])
                    if len(candidate)>self.threshold:
                        reached=True
                        if not selected:selected=[job]
                        break
                    selected.append(job)
                    size=len(candidate)
                oldest=min((self.settings.archives/job['filename']).stat().st_mtime for job in candidates)
                if not force and not reached and size<self.threshold and time.time()-oldest<self.max_wait:return None
                ids=[job['id'] for job in selected]
                db.execute('''INSERT INTO archive_packs(id,conversation_id,filename,sha256,bytes,job_ids,status,created_at)
                              VALUES(?,?,?,'',0,?,'building',?)''',(identifier,first['conversation_id'],identifier+'.zip',json.dumps(ids),time.time()))
                for job in selected:db.execute('UPDATE archive_jobs SET pack_id=? WHERE id=?',(identifier,job['id']))
                pack=db.execute('SELECT * FROM archive_packs WHERE id=?',(identifier,)).fetchone()
            pack=dict(pack)
            jobs=[dict(db.execute('SELECT * FROM archive_jobs WHERE id=?',(identifier,)).fetchone()) for identifier in json.loads(pack['job_ids'])]
        self.directory.mkdir(parents=True,exist_ok=True)
        final=self.directory/pack['filename']
        if not final.exists():
            data=self.zip_bytes(pack['id'],jobs)
            temporary=final.with_suffix('.tmp')
            with temporary.open('wb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,final)
        with zipfile.ZipFile(final) as archive:
            if archive.testzip() is not None:raise ValueError('ZIP CRC check failed')
            manifest=json.loads(archive.read('manifest.json'))
            if manifest['pack_id']!=pack['id'] or {a['id'] for a in manifest['archives']}!=set(json.loads(pack['job_ids'])):
                raise ValueError('Archive manifest mismatch')
            for job in jobs:
                if hashlib.sha256(archive.read(job['filename'])).hexdigest()!=job['sha256']:raise ValueError('ZIP content hash mismatch')
        digest=hashlib.sha256(final.read_bytes()).hexdigest()
        with self.database.transaction() as db:
            db.execute("UPDATE archive_packs SET status='local',sha256=?,bytes=? WHERE id=?",(digest,final.stat().st_size,pack['id']))
        return pack['id']

    def upload_one(self,storage=None):
        with self.database.transaction() as db:
            row=db.execute("SELECT * FROM archive_packs WHERE status IN ('local','retry') AND next_attempt<=? ORDER BY created_at LIMIT 1",(time.time(),)).fetchone()
            if row is None:return None
            pack=dict(row)
            db.execute("UPDATE archive_packs SET status='uploading',attempts=attempts+1 WHERE id=?",(pack['id'],))
        source=self.directory/pack['filename']
        try:
            if hashlib.sha256(source.read_bytes()).hexdigest()!=pack['sha256']:raise ValueError('Local package hash mismatch')
            kind,bot,target=pack['conversation_id'].split(':')
            when=datetime.fromtimestamp(pack['created_at'],UTC)
            relative=f'archives/{bot}/{kind}/{target}/{when:%Y/%m}/{pack["id"]}-{pack["sha256"][:16]}.zip'
            remote=(storage or NasStorage(NasConfig.from_env())).upload_verified(source,relative)
            with self.database.transaction() as db:
                db.execute("UPDATE archive_packs SET status='verified',remote_path=?,error=NULL WHERE id=?",(remote,pack['id']))
        except Exception as exc:
            with self.database.transaction() as db:
                db.execute("UPDATE archive_packs SET status='retry',error=?,next_attempt=? WHERE id=?",
                           (type(exc).__name__,time.time()+min(3600,30*2**min(pack['attempts'],6)),pack['id']))
            return 'retry'
        if not self.keep_local:self.cleanup_one()
        return pack['id']

    def cleanup_one(self):
        if self.keep_local:return None
        with self.database.transaction() as db:
            row=db.execute("SELECT * FROM archive_packs WHERE status='verified' AND remote_path IS NOT NULL ORDER BY created_at LIMIT 1").fetchone()
            if row is None:return None
            pack=dict(row)
            jobs=[dict(db.execute('SELECT * FROM archive_jobs WHERE id=?',(identifier,)).fetchone()) for identifier in json.loads(pack['job_ids'])]
        candidates=[(self.directory/pack['filename'],pack['sha256'])]+[(self.settings.archives/job['filename'],job['sha256']) for job in jobs]
        try:
            for source,expected in candidates:
                if not source.resolve().is_relative_to(self.settings.archives.resolve()) or source.is_symlink():
                    raise ValueError('Unexpected local archive path')
                if source.exists():
                    if hashlib.sha256(source.read_bytes()).hexdigest()!=expected:raise ValueError('Local archive changed; retained')
                    source.unlink()
            with self.database.transaction() as db:db.execute("UPDATE archive_packs SET status='cleaned',error=NULL WHERE id=?",(pack['id'],))
            return pack['id']
        except Exception as exc:
            with self.database.transaction() as db:db.execute('UPDATE archive_packs SET error=? WHERE id=?',('local_cleanup_'+type(exc).__name__,pack['id']))
            return 'retained'

    def recover(self):
        with self.database.transaction() as db:
            db.execute("UPDATE archive_packs SET status='retry',next_attempt=0 WHERE status='uploading'")
