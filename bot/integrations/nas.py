from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass,field
import hashlib
import base64
import os
from pathlib import Path,PurePosixPath

import paramiko

from bot.config import ROOT


@dataclass(frozen=True)
class NasConfig:
    host: str
    username: str
    password: str=field(repr=False)
    port: int=22
    known_hosts: Path=ROOT/'artifacts/runtime/nas-known-hosts'
    root: str='/home'
    fingerprint: str='SHA256:kj9fo+ZhMhPtETTL6RatEogfIWJ+y639vZCS+locLXU'

    @classmethod
    def from_env(cls):
        password=os.getenv('NAS_PASSWORD','')
        if not password:raise ValueError('NAS_PASSWORD is not configured')
        return cls(os.getenv('NAS_HOST','192.168.124.3'),os.getenv('NAS_USERNAME','qqbot'),password,
                   int(os.getenv('NAS_PORT','22')),Path(os.getenv('NAS_KNOWN_HOSTS',str(ROOT/'artifacts/runtime/nas-known-hosts'))))


class NasStorage:
    def __init__(self,config: NasConfig):self.config=config

    @contextmanager
    def connect(self):
        client=paramiko.SSHClient()
        self.config.known_hosts.parent.mkdir(parents=True,exist_ok=True)
        if not self.config.known_hosts.exists():self.config.known_hosts.touch()
        client.load_host_keys(str(self.config.known_hosts))
        expected=self.config.fingerprint
        known=self.config.known_hosts
        class PinnedKey(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self,client,hostname,key):
                actual='SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
                if actual!=expected:raise paramiko.SSHException('NAS host key fingerprint mismatch')
                client.get_host_keys().add(hostname,key.get_name(),key)
                client.save_host_keys(str(known))
        client.set_missing_host_key_policy(PinnedKey())
        try:
            client.connect(self.config.host,port=self.config.port,username=self.config.username,password=self.config.password,
                           allow_agent=False,look_for_keys=False,timeout=10,banner_timeout=10,auth_timeout=10)
            with client.open_sftp() as sftp:
                sftp.get_channel().settimeout(30)
                yield sftp
        finally:client.close()

    @staticmethod
    def hash_remote(sftp,path):
        digest=hashlib.sha256()
        with sftp.open(path,'rb') as stream:
            while chunk:=stream.read(128*1024):digest.update(chunk)
        return digest.hexdigest()

    def upload_verified(self,local: Path,relative: str) -> str:
        path=PurePosixPath(relative)
        if path.is_absolute() or '..' in path.parts or not path.parts:raise ValueError('Invalid NAS relative path')
        remote=str(PurePosixPath(self.config.root)/path)
        expected=hashlib.sha256(local.read_bytes()).hexdigest()
        with self.connect() as sftp:
            sftp.stat(self.config.root)
            current=PurePosixPath(self.config.root)
            for part in path.parts[:-1]:
                current=current/part
                try:sftp.stat(str(current))
                except FileNotFoundError:sftp.mkdir(str(current))
            try:
                sftp.stat(remote)
                if self.hash_remote(sftp,remote)!=expected:raise ValueError('NAS destination already exists with different content')
                return remote
            except FileNotFoundError:pass
            temporary=remote+'.part'
            sftp.put(str(local),temporary,confirm=True)
            if self.hash_remote(sftp,temporary)!=expected:raise IOError('NAS hash verification failed')
            sftp.rename(temporary,remote)
            if sftp.stat(remote).st_size!=local.stat().st_size:raise IOError('NAS final size mismatch')
        return remote
