"""Build offline or explicitly sync and publish generated files to GitHub Pages."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
BRANCH = 'gh-pages'
OUTPUT = ROOT/'dist-pages'


def run(args, cwd=ROOT, env=None, capture=False, check=True):
    return subprocess.run(args,cwd=cwd,env=env,text=True,capture_output=capture,check=check)


def git(*args, cwd=ROOT, check=True):
    return run(['git',*args],cwd=cwd,capture=True,check=check)


def resolve_remote(value):
    if not value or value.startswith('-'):
        raise ValueError('Invalid Pages remote')
    result=git('remote','get-url',value,check=False)
    if result.returncode==0:
        return result.stdout.strip()
    if re.fullmatch(r'[a-zA-Z0-9_-]+',value):
        raise ValueError(f'No Git remote named {value}. Configure it first, or set PAGES_REMOTE to the repository URL.')
    return value


def infer_base(remote):
    match=re.fullmatch(r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([a-zA-Z0-9_-]+)/([a-zA-Z0-9_.-]+)/?',remote)
    if not match:
        raise ValueError('Set PAGES_BASE explicitly for a non-GitHub remote or custom domain.')
    owner,repository=match.groups()
    repository=repository.removesuffix('.git')
    return '/' if repository.lower()==owner.lower()+'.github.io' else '/'+repository+'/'


def validate_base(value):
    if not re.fullmatch(r'/(?:[a-zA-Z0-9._~-]+/)*[a-zA-Z0-9._~-]*/?',value) or any(p in ('.','..') for p in value.split('/')):
        raise ValueError('PAGES_BASE must be a URL path such as /better-meteo/ or /')
    return '/' if value=='/' else '/'+ '/'.join(p for p in value.split('/') if p)+'/'


def build(base):
    env={**os.environ,'PAGES_BASE':base,'PAGES_OUTPUT':str(OUTPUT)}
    run(['npm','run','build'],env=env)
    run([os.environ.get('NODE','node'),'scripts/export-pages.mjs'],env=env)


def copy_site(site, checkout):
    """Only replace the disposable deployment checkout, never the source worktree."""
    if not (site/'.better-meteo-export').is_file() or not (site/'index.html').is_file():
        raise ValueError('Missing or unrecognised static export; refusing to publish')
    cname=(checkout/'CNAME').read_bytes() if (checkout/'CNAME').is_file() else None
    for item in checkout.iterdir():
        if item.name=='.git':
            continue
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()
    for item in site.iterdir():
        if item.is_symlink():
            raise ValueError('Static export must not contain symbolic links')
        if item.is_dir():
            shutil.copytree(item,checkout/item.name)
        else:
            shutil.copy2(item,checkout/item.name)

    if cname is not None and not (checkout/'CNAME').exists():
        (checkout/'CNAME').write_bytes(cname)


def prepare_checkout(remote, checkout, name, email):
    result=git('ls-remote','--exit-code','--heads',remote,'refs/heads/'+BRANCH,check=False)
    if result.returncode==0:
        run(['git','clone','--quiet','--depth','1','--single-branch','--branch',BRANCH,remote,str(checkout)])
    elif result.returncode==2:
        checkout.mkdir()
        git('init','--quiet','--initial-branch='+BRANCH,cwd=checkout)
        git('remote','add','origin',remote,cwd=checkout)
    else:
        raise RuntimeError('Cannot read the deployment repository. Check Git access before syncing. '+result.stderr.strip())
    git('config','user.name',name,cwd=checkout)
    git('config','user.email',email,cwd=checkout)


def commit_and_push(checkout):
    git('add','--all',cwd=checkout)
    unchanged=git('diff','--cached','--quiet',cwd=checkout,check=False)
    if unchanged.returncode==0:
        print('Published files are unchanged; no commit or push needed.')
        return False
    if unchanged.returncode!=1:
        raise RuntimeError('Could not inspect deployment changes')
    message='Update weather dashboard '+datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    git('commit','--quiet','-m',message,cwd=checkout)
    # Ordinary fast-forward push: never overwrite remote history or source changes.
    run(['git','push','origin',f'HEAD:refs/heads/{BRANCH}'],cwd=checkout)
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['build','publish'])
    parser.add_argument('--city',default='turin')
    args=parser.parse_args()
    os.environ.setdefault('GIT_TERMINAL_PROMPT','0')
    os.environ.setdefault('GIT_SSH_COMMAND','ssh -oBatchMode=yes -oConnectTimeout=15')
    requested=os.environ.get('PAGES_REMOTE','origin')
    explicit_base=os.environ.get('PAGES_BASE')
    if args.command=='build':
        if explicit_base:
            base=explicit_base
        else:
            try:
                base=infer_base(resolve_remote(requested))
            except ValueError:
                base='/'
        build(validate_base(base))
        return
    # Validate Git settings and access before making any weather requests.
    remote=resolve_remote(requested)
    base=validate_base(explicit_base or infer_base(remote))
    name=os.environ.get('GIT_AUTHOR_NAME') or git('config','user.name',check=False).stdout.strip()
    email=os.environ.get('GIT_AUTHOR_EMAIL') or git('config','user.email',check=False).stdout.strip()
    if not name or not email:
        raise ValueError('Configure git user.name and user.email before publishing.')
    config=json.loads((ROOT/'config.json').read_text())
    if not any(city['id']==args.city for city in config['cities']):
        raise ValueError('Unknown city id')
    runtime=Path(os.environ.get('BETTER_METEO_DATA',ROOT/'data'))
    if not runtime.is_absolute():
        runtime=ROOT/runtime
    runtime.mkdir(parents=True,exist_ok=True)
    with (runtime/'pages-publish.lock').open('w') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another Pages publication is running')
        with tempfile.TemporaryDirectory(prefix='better-meteo-publish-') as temporary:
            checkout=Path(temporary)/'site'
            prepare_checkout(remote,checkout,name,email)
            run([sys.executable,'-m','collector','sync','--city',args.city])
            # Keep the existing PNG-only reader option; this step is offline.
            if (runtime/'cities'/args.city/'forecast.json').exists():
                run([sys.executable,'-m','collector','render','--city',args.city])
            build(base)
            copy_site(OUTPUT,checkout)
            commit_and_push(checkout)
    print(f'Pages branch published: {BRANCH}. Enable it under repository Settings → Pages → Deploy from a branch → {BRANCH} / (root).')
    print('The source worktree was not staged, committed or switched. Historical archives and policy records stay local.')


if __name__=='__main__':
    try:
        main()
    except (ValueError,RuntimeError,subprocess.CalledProcessError) as exc:
        print(f'Pages publication stopped: {exc}',file=sys.stderr)
        sys.exit(1)
