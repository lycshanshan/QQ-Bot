"""Install pinned KaTeX assets locally, verifying npm registry integrity first."""
from pathlib import Path
import base64,hashlib,io,json,tarfile,urllib.request
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/runtime/renderer/katex'
VERSION='0.16.22'

def main():
    with urllib.request.urlopen('https://registry.npmjs.org/katex/'+VERSION,timeout=30) as response: meta=json.load(response)
    with urllib.request.urlopen(meta['dist']['tarball'],timeout=30) as response: data=response.read(8*1024*1024+1)
    if len(data)>8*1024*1024:raise ValueError('KaTeX archive exceeds limit')
    expected=meta['dist']['integrity']
    actual='sha512-'+base64.b64encode(hashlib.sha512(data).digest()).decode()
    if actual!=expected:raise ValueError('KaTeX integrity mismatch')
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
        for member in archive.getmembers():
            prefix='package/dist/'
            name=member.name
            if not member.isfile() or not name.startswith(prefix):continue
            relative=Path(name[len(prefix):])
            if relative.is_absolute() or '..' in relative.parts:raise ValueError('Invalid archive path')
            if relative.as_posix() not in {'katex.min.js','katex.min.css'} and not relative.as_posix().startswith('fonts/'):continue
            target=OUT/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            stream=archive.extractfile(member)
            target.write_bytes(stream.read())
        license_member=archive.getmember('package/LICENSE')
        OUT.mkdir(parents=True,exist_ok=True)
        (OUT/'LICENSE').write_bytes(archive.extractfile(license_member).read())
    print('KaTeX assets installed with verified sha512:',VERSION)

if __name__=='__main__':main()
