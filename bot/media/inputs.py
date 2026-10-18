from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import shutil
import urllib.parse
import urllib.error
import urllib.request
import uuid

MAX_BYTES=10*1024*1024


def validate_url(url: str) -> None:
    p=urllib.parse.urlparse(url)
    host=(p.hostname or '').lower()
    if p.scheme!='https' or p.username or p.password or not any(host==d or host.endswith('.'+d) for d in ('qq.com','qq.com.cn','qpic.cn')):
        raise ValueError('Unsupported attachment download origin')


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        validate_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def copy_source(source: str,destination: Path,remote: bool) -> None:
    if remote:
        validate_url(source)
        stream=urllib.request.build_opener(Redirects()).open(source,timeout=20)
    else:
        stream=Path(source).open('rb')
    try:
        with stream,destination.open('wb') as out:
            count=0
            while chunk:=stream.read(65536):
                count+=len(chunk)
                if count>MAX_BYTES:
                    raise ValueError('Attachment exceeds 10 MiB')
                out.write(chunk)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


async def prepare_media(bot,envelope: dict,workspace) -> list[Path]:
    segments=list(envelope['input']['segments'])
    reply=envelope['input'].get('reply')
    if reply and not reply.get('unavailable'):
        segments.extend(reply.get('segments',[]))
    images=[]
    artifacts=[]
    media=[s for s in segments if s['type'] in {'image','file'}]
    if len(media)>5:
        raise ValueError('每次请求最多处理 5 个附件。')
    for segment in media:
        kind=segment['type']
        data=segment.get('data',{})
        url=data.get('url')
        details={}
        reference=data.get('file_id') or data.get('file') or data.get('id')
        if not url and reference:
            action='get_image' if kind=='image' else 'get_file'
            param='file' if kind=='image' else 'file_id'
            details=await asyncio.wait_for(bot.call_api(action,**{param:reference}),timeout=20)
            url=details.get('url')
        source=url or details.get('file')
        if not source:
            raise ValueError('附件已失效或没有可用文件实体，请重新发送。')
        filename=str(data.get('name') or data.get('file_name') or details.get('file_name') or '')
        suffix=Path(filename).suffix.lower()
        if suffix not in {'.txt','.md','.csv','.json','.pdf','.docx','.xlsx','.py','.zip','.png','.jpg','.jpeg','.webp','.gif'}:
            suffix='.bin'
        target=workspace.path/'input'/(uuid.uuid4().hex+suffix)
        try:
            await asyncio.to_thread(copy_source,str(source),target,bool(urllib.parse.urlparse(str(source)).scheme in {'http','https'}))
        except (urllib.error.HTTPError,urllib.error.URLError,FileNotFoundError,TimeoutError):
            if not reference:raise
            action='get_image' if kind=='image' else 'get_file'
            param='file' if kind=='image' else 'file_id'
            refreshed=await asyncio.wait_for(bot.call_api(action,**{param:reference}),timeout=20)
            source=refreshed.get('url') or refreshed.get('file')
            if not source:raise ValueError('附件已失效，请重新发送。')
            await asyncio.to_thread(copy_source,str(source),target,bool(urllib.parse.urlparse(str(source)).scheme in {'http','https'}))
        if kind=='image':
            with target.open('rb') as stream:
                signature=stream.read(16)
            if signature.startswith(b'\x89PNG\r\n\x1a\n'):
                ext='.png'
            elif signature.startswith(b'\xff\xd8\xff'):
                ext='.jpg'
            elif signature.startswith(b'RIFF') and signature[8:12]==b'WEBP':
                ext='.webp'
            else:
                raise ValueError('当前图片输入支持 PNG/JPEG/WebP，GIF 请先转换为静态图。')
            renamed=target.with_suffix(ext)
            target.rename(renamed)
            target=renamed
            images.append(target)
        artifacts.append({'id':target.stem,'filename':filename,'path':str(target),'bytes':target.stat().st_size,
                          'sha256':await asyncio.to_thread(lambda p=target:hashlib.sha256(p.read_bytes()).hexdigest())})
    envelope['artifacts']=artifacts
    return images
