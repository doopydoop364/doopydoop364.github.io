#!/bin/sh
set -eu
command -v python3 >/dev/null || { echo 'Python 3.10+ is required; install it explicitly using your system package manager.' >&2; exit 1; }
if [ "$(id -u)" -eq 0 ]; then echo 'Run as your ordinary VM user, not root.' >&2; exit 1; fi
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "$0" in
    */install.sh|install.sh)
        if [ -f "$SCRIPT_DIR/install_app.py" ]; then
            exec python3 "$SCRIPT_DIR/install_app.py" "$@"
        fi
        ;;
esac
exec python3 - "$@" <<'PY'
import argparse
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import quote
from urllib.request import urlopen

if sys.version_info < (3, 10):
    sys.exit('Python 3.10+ is required')
parser=argparse.ArgumentParser(add_help=False)
parser.add_argument('--ref', default='feature/arch-games-2')
parser.add_argument('--source')
args,rest=parser.parse_known_args()
if args.source:
    source=Path(args.source).resolve()
    sys.exit(subprocess.call([sys.executable,str(source/'install_app.py'),'--source',str(source)]+rest))
url='https://codeload.github.com/doopydoop364/doopydoop364.github.io/tar.gz/'+quote(args.ref,safe='')
try:
    with tempfile.TemporaryDirectory(prefix='arch-games-install-') as temp:
        archive=Path(temp)/'source.tar.gz'
        with urlopen(url,timeout=30) as response, archive.open('wb') as output:
            received=0
            while True:
                chunk=response.read(256*1024)
                if not chunk: break
                received+=len(chunk)
                if received>64*1024*1024: raise RuntimeError('Source archive exceeds bootstrap limit')
                output.write(chunk)
        destination=Path(temp)/'source'
        destination.mkdir()
        count=expanded=0
        with tarfile.open(archive,'r:gz') as package:
            for member in package:
                count+=1
                if count>20000: raise RuntimeError('Source file-count limit exceeded')
                parts=PurePosixPath(member.name).parts
                if len(parts)<4 or parts[1:3]!=('arch86','games'): continue
                relative=parts[3:]
                if any(p in {'.','..'} or ':' in p for p in relative): raise RuntimeError('Unsafe source path')
                path=destination.joinpath(*relative)
                if member.isdir():
                    path.mkdir(parents=True,exist_ok=True)
                    continue
                if not member.isfile(): raise RuntimeError('Source links/special files refused')
                expanded+=member.size
                if member.size>32*1024*1024 or expanded>128*1024*1024: raise RuntimeError('Source size limit exceeded')
                path.parent.mkdir(parents=True,exist_ok=True)
                with package.extractfile(member) as stream, path.open('xb') as output:
                    shutil.copyfileobj(stream,output,256*1024)
        result=subprocess.call([sys.executable,str(destination/'install_app.py'),'--source',str(destination),'--ref',args.ref]+rest)
        sys.exit(result)
except (OSError,ValueError,tarfile.TarError,RuntimeError) as error:
    sys.exit('Bootstrap failed: '+str(error))
PY
