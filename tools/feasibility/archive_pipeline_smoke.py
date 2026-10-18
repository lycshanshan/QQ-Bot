"""Synthetic complete archive pipeline with verified NAS upload and own-file cleanup."""
from dataclasses import replace
from pathlib import Path,PurePosixPath
import json,sys,time,uuid
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from bot.config import load_settings
from bot.domain.messages import Incoming
from bot.services.application import Application
from bot.workers.archive import Archiver
from bot.workers.nas_archive import ArchivePacks
from bot.integrations.nas import NasConfig,NasStorage

def main():
    load_dotenv(ROOT/'.env')
    identifier=uuid.uuid4().hex
    base=ROOT/'artifacts/feasibility/archive-pipeline'/identifier
    settings=replace(load_settings(),database=base/'probe.db',archives=base/'archives',message_window=1,archive_batch=1,
                     ai_enabled=False,ai_policy=base/'policy.toml',agent_workspaces=base/'tasks')
    app=Application(settings)
    bot='probe-'+identifier
    now=time.time()
    for i in range(4):
        event=Incoming(bot,'private','99999','99999',str(i),int(now),now,'synthetic','',
                       [{'type':'text','data':{'text':'SYNTHETIC_ARCHIVE_TEST_'+str(i)}}],'synthetic')
        app.process(event)
    exporter=Archiver(app.database,settings)
    while exporter.export_one():pass
    packs=ArchivePacks(app.database,settings,keep_local=True)
    pack_id=packs.pack_one(force=True)
    storage=NasStorage(NasConfig.from_env())
    result=packs.upload_one(storage)
    with app.database.transaction() as db:
        pack=dict(db.execute('SELECT * FROM archive_packs WHERE id=?',(pack_id,)).fetchone())
    if result!=pack_id or pack['status']!='verified':raise RuntimeError('NAS archive pipeline did not verify')
    remote=pack['remote_path']
    if not remote.startswith('/home/archives/'+bot+'/'):raise ValueError('Unexpected probe remote path')
    with storage.connect() as sftp:
        sftp.remove(remote)
        directory=PurePosixPath(remote).parent
        while str(directory).startswith('/home/archives/'+bot):
            sftp.rmdir(str(directory))
            directory=directory.parent
    report={'status':pack['status'],'bytes':pack['bytes'],'sha256':pack['sha256'],'synthetic_messages':4,
            'json_archives':3,'nas_probe_files_removed':True,'local_sources_retained':True}
    print(json.dumps(report))
    (ROOT/'artifacts/feasibility/archive-pipeline-result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')

if __name__=='__main__':main()
