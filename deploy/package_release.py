"""Package an explicit source allowlist. Never include databases, keys or uploads."""
import argparse
import io
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ['index.html','app.js','style.css','settlement.js','billing.py','web_server.py','test_backend.py','README.md','IMPORT.md','BACKUP.md','REQUIREMENTS.md','LICENSE','server.mjs','package.json','build.mjs']
FILES += ['samples/wechat-sample.csv', 'samples/alipay-sample.csv', 'agents.md']
FILES += ['deploy/'+name for name in ('README.md','RELEASE.md','nginx.conf','nginx-acme.conf','travel-aa.service','travel-aa-cert-renew.service','travel-aa-cert-renew.timer','activate.sh','rollback.sh','bootstrap-tls.sh','initialize-admin.ps1','update.ps1','package_release.py','render_config.py','test_render_config.py')]

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    for name in FILES:
        path=ROOT/name
        if path.is_symlink() or not path.is_file(): raise ValueError(f'Missing or unsafe source: {name}')
    with tarfile.open(args.output,'w:gz') as archive:
        def source_permissions(info):
            info.mode=0o644
            info.uid=info.gid=0
            info.uname=info.gname='root'
            return info
        for name in FILES:
            archive.add(ROOT/name,arcname=name,recursive=False,filter=source_permissions)
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).strip()+b'\n'
        info = tarfile.TarInfo('REVISION')
        info.size = len(revision)
        archive.addfile(source_permissions(info), io.BytesIO(revision))
    print(f'Packaged {len(FILES)} source files; no ledger, credentials or test output.')
