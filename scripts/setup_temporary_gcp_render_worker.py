"""User-operated Cloud Shell setup for the two existing GCP VMs only.

Never creates/resizes/deletes VMs, changes API credentials or opens firewalls.
Initial render-only auth is provisioned only with --provision-render-auth.
SSH host keys come from authenticated VM sessions, not an unverified key scan.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import tempfile
import time
import urllib.request

REPOSITORY = "nyu1791-collab/hf-site-agent"
COORDINATOR = "instance-20261001-071545"
WORKER = "hf-render-worker"
SOURCE_DIR = "/home/n_yu1791/hf-site-agent"
PORT = 18765


@contextmanager
def operation_lock():
    """Serialize Cloud Shell invocations so a repeated paste cannot race key setup."""
    lock_dir = Path.home() / ".cache" / "hf-site-agent"
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = lock_dir.lstat()
    if not __import__("stat").S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError("temporary renderer operation lock directory is not private")
    if info.st_mode & 0o077:
        os.chmod(lock_dir, 0o700)
    lock_path = lock_dir / "temporary-render-operation.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        state = os.fstat(fd)
        if not __import__("stat").S_ISREG(state.st_mode) or state.st_uid != os.getuid() or state.st_mode & 0o077:
            raise RuntimeError("temporary renderer operation lock file is not private")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another temporary renderer operation is already running") from exc
        yield
    finally:
        os.close(fd)


def command(args, check=True):
    completed = subprocess.run(args, text=True, capture_output=True)
    if check and completed.returncode:
        # Arguments never include tokens/private keys. Do not dump environments.
        raise RuntimeError(f"command failed: {args[0]} (exit {completed.returncode}); {completed.stderr[-1800:]}")
    return completed


class Connection:
    def __init__(self, project, instance):
        self.project = project
        self.name = instance["name"]
        self.zone = instance["zone"].rsplit("/", 1)[-1]
        self.ip = instance["networkInterfaces"][0]["networkIP"]
        if not ipaddress.ip_address(self.ip).is_private:
            raise RuntimeError("The render tunnel requires a private VM address")

    def ssh(self, script, check=True):
        return command(["gcloud", "compute", "ssh", self.name, "--project", self.project,
                        "--zone", self.zone, "--quiet", "--command", script], check=check)

    def put(self, source, destination):
        command(["gcloud", "compute", "scp", str(source), f"{self.name}:{destination}",
                 "--project", self.project, "--zone", self.zone, "--quiet"])

    def get(self, source, destination):
        command(["gcloud", "compute", "scp", f"{self.name}:{source}", str(destination),
                 "--project", self.project, "--zone", self.zone, "--quiet"])


def current_source():
    def read(url):
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.load(response)
    pr = read(f"https://api.github.com/repos/{REPOSITORY}/pulls/40")
    if pr["state"] != "open" or not pr["draft"] or pr["merged"] or pr["head"]["ref"] != "ai-army/provider-v3":
        raise RuntimeError("PR #40 must remain open, draft and unmerged on the canonical branch")
    return pr["head"]["sha"]


WORKER_SETUP = r'''
import json, os, pathlib, pwd, shutil, subprocess, sys, time
base=pathlib.Path(sys.argv[1]); sha=sys.argv[2]; coordinator_ip=sys.argv[3]
os.environ['DEBIAN_FRONTEND']='noninteractive'
def run(*args): subprocess.run(args, check=True, stdout=subprocess.DEVNULL)
env_path=pathlib.Path('/etc/hf-render-worker/worker.env')
if env_path.exists() or pathlib.Path('/etc/hf-render-worker/id_ed25519').exists():
    raise SystemExit('Existing render auth found; refusing to replace secrets')
run('apt-get','update','-qq')
run('apt-get','install','-y','--no-install-recommends','python3','python3-pil','ffmpeg','openssh-client','fonts-noto-cjk')
try: account=pwd.getpwnam('hf-render-worker')
except KeyError:
    run('useradd','--system','--home-dir','/var/lib/hf-render-worker','--shell','/usr/sbin/nologin','hf-render-worker')
    account=pwd.getpwnam('hf-render-worker')
root=pathlib.Path('/opt/hf-site-agent')
if root.exists(): raise SystemExit('Existing worker source directory requires a reviewed update; refusing overwrite')
root.mkdir(mode=0o755,parents=True)
run('tar','-xzf',str(base/'source.tar.gz'),'--strip-components=1','-C',str(root))
(root/'REVISION').write_text(sha+'\n')
for p in [pathlib.Path('/var/lib/hf-render-worker'),pathlib.Path('/var/lib/hf-render-worker/home'),pathlib.Path('/var/lib/hf-render-worker/tmp')]:
    p.mkdir(parents=True,exist_ok=True); p.chmod(0o700); os.chown(p,account.pw_uid,account.pw_gid)
etc=pathlib.Path('/etc/hf-render-worker');etc.mkdir(exist_ok=True);etc.chmod(0o750);os.chown(etc,0,account.pw_gid)
assets=pathlib.Path('/srv/hf-render-assets');assets.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(root));os.chdir(root)
from scripts.media_render_e2e import materialize_shell
from scripts.media_render_worker import _tree_sha256, _sha256_file
shell=materialize_shell(assets/'acceptance-shell')
font=assets/'NotoSansCJK-Regular.ttc'
shutil.copyfile('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',font)
for p in assets.rglob('*'): p.chmod(0o755 if p.is_dir() else 0o644)
hashes={'shell_sha256':_tree_sha256(shell),'font_sha256':_sha256_file(font),'commit':sha}
(base/'hashes.json').write_text(json.dumps(hashes))
token=(base/'render-token').read_text().strip()
if len(token)!=43: raise SystemExit('Invalid initial render token')
values={'MEDIA_RENDER_SHARED_TOKEN':token,'MEDIA_RENDER_SHELL':str(shell),'MEDIA_RENDER_FONT':str(font),
        'MEDIA_RENDER_SHELL_SHA256':hashes['shell_sha256'],'MEDIA_RENDER_FONT_SHA256':hashes['font_sha256'],
        'MEDIA_RENDER_WORK_DIR':'/var/lib/hf-render-worker','MEDIA_RENDER_WORKER_ID':'hf-render-worker',
        'MEDIA_RENDER_ACCEPT_UNTIL':str(time.time()+72*3600)}
env_path.write_text(''.join(k+'='+v+'\n' for k,v in values.items()));env_path.chmod(0o600)
key=etc/'id_ed25519';run('ssh-keygen','-q','-t','ed25519','-N','','-f',str(key))
os.chown(key,account.pw_uid,account.pw_gid);key.chmod(0o600)
shutil.copyfile(str(key)+'.pub',base/'worker-key.pub')
shutil.copyfile(base/'known_hosts',etc/'known_hosts');(etc/'known_hosts').chmod(0o644)
(etc/'tunnel.env').write_text('COORDINATOR_HOST='+coordinator_ip+'\n');(etc/'tunnel.env').chmod(0o600)
for name in ['hf-render-worker.service','hf-render-worker-check.service','hf-render-worker-tunnel.service']:
    shutil.copyfile(root/'deploy/systemd'/name,pathlib.Path('/etc/systemd/system')/name)
journal=pathlib.Path('/etc/systemd/journald.conf.d');journal.mkdir(exist_ok=True)
(journal/'hf-render-worker.conf').write_text('[Journal]\nSystemMaxUse=128M\nRuntimeMaxUse=32M\nMaxRetentionSec=4day\n')
run('systemctl','restart','systemd-journald');run('systemctl','daemon-reload')
run('systemctl','start','hf-render-worker-check.service')
run('systemctl','enable','--now','hf-render-worker.service')
(base/'render-token').unlink()
print(json.dumps({'worker_setup':'installed','commit':sha,'acceptance_fixture_only':True,'admission_hours':72}))
'''


COORDINATOR_SETUP = r'''
import json, os, pathlib, pwd, shutil, subprocess, sys
base=pathlib.Path(sys.argv[1]);sha=sys.argv[2]
def run(*args): return subprocess.run(args,check=True,capture_output=True,text=True).stdout
account=pwd.getpwnam('n_yu1791');root=pathlib.Path('/home/n_yu1791/hf-site-agent')
if run('sudo','-u','n_yu1791','git','-C',str(root),'rev-parse','HEAD').strip()!=sha: raise SystemExit('Coordinator must be updated and tested at the same commit first')
env=pathlib.Path('/home/n_yu1791/.config/hf-site-agent/media-render.env')
ssh_conf=pathlib.Path('/etc/ssh/sshd_config.d/90-hf-render-tunnel.conf')
if env.exists() or ssh_conf.exists(): raise SystemExit('Existing render auth/config found; refusing to replace it')
try: tunnel=pwd.getpwnam('hf-render-tunnel')
except KeyError:
    run('useradd','--system','--create-home','--home-dir','/var/lib/hf-render-tunnel','--shell','/usr/sbin/nologin','hf-render-tunnel')
    tunnel=pwd.getpwnam('hf-render-tunnel')
key=(base/'worker-key.pub').read_text().strip()
if not key.startswith('ssh-ed25519 '): raise SystemExit('Invalid worker public key')
sshdir=pathlib.Path(tunnel.pw_dir)/'.ssh';sshdir.mkdir(mode=0o700,exist_ok=True);os.chown(sshdir,tunnel.pw_uid,tunnel.pw_gid)
authorized=sshdir/'authorized_keys'
if authorized.exists(): raise SystemExit('Existing tunnel account key found; refusing replacement')
authorized.write_text('restrict,port-forwarding,permitlisten="127.0.0.1:18765" '+key+'\n');authorized.chmod(0o600);os.chown(authorized,tunnel.pw_uid,tunnel.pw_gid)
ssh_conf.write_text('Match User hf-render-tunnel\n  AllowTcpForwarding remote\n  AllowStreamLocalForwarding no\n  PermitOpen none\n  PermitListen 127.0.0.1:18765\n  GatewayPorts no\n  AllowAgentForwarding no\n  X11Forwarding no\n  PermitTTY no\n  PermitTunnel no\n  MaxSessions 0\nMatch all\n')
run('/usr/sbin/sshd','-t');run('systemctl','reload','ssh')
hashes=json.loads((base/'hashes.json').read_text());token=(base/'render-token').read_text().strip()
values={'MEDIA_RENDER_SHARED_TOKEN':token,'MEDIA_RENDER_EXPECTED_SHELL_SHA256':hashes['shell_sha256'],
        'MEDIA_RENDER_EXPECTED_FONT_SHA256':hashes['font_sha256'],'MEDIA_RENDER_WORKER_URL':'http://127.0.0.1:18765/v1/render'}
env.parent.mkdir(parents=True,exist_ok=True)
env.write_text(''.join(k+'='+v+'\n' for k,v in values.items()));env.chmod(0o600);os.chown(env,account.pw_uid,account.pw_gid)
units=pathlib.Path('/home/n_yu1791/.config/systemd/user');units.mkdir(parents=True,exist_ok=True)
for name in ['hf-site-agent-media-render-check.service','hf-site-agent-media-render@.service']:
    dest=units/name;shutil.copyfile(root/'deploy/systemd/user'/name,dest);os.chown(dest,account.pw_uid,account.pw_gid)
run('sudo','-u','n_yu1791','env','XDG_RUNTIME_DIR=/run/user/'+str(account.pw_uid),'systemctl','--user','daemon-reload')
(base/'render-token').unlink()
print(json.dumps({'coordinator_setup':'installed','commit':sha,'queue_reinitialized':False}))
'''


def coordinator_run(coordinator, module, arguments=""):
    # Match the persistent user service's local, on-demand VOICEVOX runtime.
    # Do not source media.env here: that file also contains paid API credentials
    # that this fixed fixture must never need or inherit.
    shell = (f"cd {SOURCE_DIR} && set -a && . /home/n_yu1791/.config/hf-site-agent/media-render.env && set +a && "
             "export VOICEVOX_ENGINE_DIR=/home/n_yu1791/.local/share/voicevox_engine/linux-cpu-x64 "
             f"VOICEVOX_CACHE_DIR={SOURCE_DIR}/runtime/voice-cache "
             "VOICEVOX_URL=http://127.0.0.1:50021 VOICEVOX_REMOTE_TUNNEL=0 VV_CPU_NUM_THREADS=1 && "
             f"python3 -m {module} {arguments}")
    return coordinator.ssh("sudo -n -u n_yu1791 bash -c " + shlex.quote(shell))


def setup(coordinator, worker, sha, provision):
    if not provision:
        raise RuntimeError("New renderer-only token and SSH key require explicit --provision-render-auth; existing secrets are never replaced")
    for host in (coordinator, worker):
        host.ssh("sudo -n true")
    current = coordinator.ssh(f"sudo -n -u n_yu1791 git -C {SOURCE_DIR} rev-parse HEAD").stdout.strip()
    if current != sha:
        raise RuntimeError("Coordinator is not on current tested PR HEAD; run the private control update_test first")
    coordinator.ssh("sudo -n test ! -e /home/n_yu1791/.config/hf-site-agent/media-render.env && sudo -n test ! -e /etc/ssh/sshd_config.d/90-hf-render-tunnel.conf")
    worker.ssh("sudo -n test ! -e /etc/hf-render-worker/worker.env && sudo -n test ! -e /opt/hf-site-agent")
    host_key = coordinator.ssh("sudo -n cat /etc/ssh/ssh_host_ed25519_key.pub").stdout.strip()
    if not host_key.startswith("ssh-ed25519 "):
        raise RuntimeError("Coordinator host key was not verified")
    with tempfile.TemporaryDirectory(prefix="hf-render-private-") as directory:
        local = Path(directory)
        os.chmod(local, 0o700)
        token_file = local / "render-token"
        token_file.write_text(secrets.token_urlsafe(32));token_file.chmod(0o600)
        with urllib.request.urlopen(f"https://api.github.com/repos/{REPOSITORY}/tarball/{sha}", timeout=60) as source, (local / "source.tar.gz").open("wb") as target:
            __import__("shutil").copyfileobj(source, target)
        (local / "known_hosts").write_text(coordinator.ip + " " + host_key + "\n")
        for host, program in ((worker, WORKER_SETUP), (coordinator, COORDINATOR_SETUP)):
            stage = host.ssh("umask 077; mktemp -d /tmp/hf-render-setup.XXXXXXXX").stdout.strip()
            if not stage.startswith("/tmp/hf-render-setup."):
                raise RuntimeError("Invalid private staging directory")
            (local / "install.py").write_text(program)
            inputs = ["install.py", "render-token"]
            inputs += ["source.tar.gz", "known_hosts"] if host is worker else ["hashes.json", "worker-key.pub"]
            for name in inputs:
                host.put(local / name, stage + "/" + name)
            argument = " " + shlex.quote(coordinator.ip) if host is worker else ""
            result = host.ssh("sudo -n python3 " + shlex.quote(stage + "/install.py") + " " + shlex.quote(stage) + " " + sha + argument)
            print(result.stdout.strip())
            if host is worker:
                worker.get(stage + "/hashes.json", local / "hashes.json")
                worker.get(stage + "/worker-key.pub", local / "worker-key.pub")
    worker.ssh("sudo -n systemctl enable --now hf-render-worker-tunnel.service")
    return wait_ready(coordinator)


def wait_ready(coordinator, previous_boot=None):
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            result = coordinator_run(coordinator, "scripts.media_render_transport", "--check")
            health = json.loads(result.stdout.strip().splitlines()[-1])
            if health.get("status") == "READY" and (previous_boot is None or health.get("worker_boot_id") != previous_boot):
                print(json.dumps(health, sort_keys=True))
                return health
        except (RuntimeError, ValueError):
            pass
        print("Waiting for authenticated loopback worker recovery...", flush=True)
        time.sleep(10)
    raise RuntimeError("Worker recovery timeout; queue and prepared media are preserved")


def e2e(coordinator, job):
    result = coordinator_run(coordinator, "scripts.media_render_e2e", f"--db runtime/media-queue.sqlite3 --workspace runtime --job {job}")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    print(json.dumps(payload, sort_keys=True))
    if payload.get("queue_status") != "success" or payload.get("paid_llm_requests") != 0:
        raise RuntimeError("E2E acceptance did not succeed")
    return payload


def worker_control(worker, operation):
    shell = "cd /opt/hf-site-agent && set -a && . /etc/hf-render-worker/worker.env && set +a && exec sudo -u hf-render-worker --preserve-env=MEDIA_RENDER_WORK_DIR,MEDIA_RENDER_ACCEPT_UNTIL python3 -m scripts.media_render_worker --" + operation
    return json.loads(worker.ssh("sudo -n bash -c " + shlex.quote(shell)).stdout.strip().splitlines()[-1])


def reboot_check(coordinator, worker):
    first = e2e(coordinator, "first")
    health = wait_ready(coordinator)
    if worker_control(worker, "status")["active_render"]:
        raise RuntimeError("Active render must finish before reboot")
    worker.ssh("sudo -n systemctl reboot", check=False)
    recovered = wait_ready(coordinator, previous_boot=health["worker_boot_id"])
    second = e2e(coordinator, "second")
    receipt = {"first": first, "second": second, "old_boot_id": health["worker_boot_id"], "new_boot_id": recovered["worker_boot_id"], "automatic_recovery": True, "human_ssh_after_reboot": False}
    text = json.dumps(receipt, sort_keys=True)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reboot-e2e-receipt.json";path.write_text(text)
        stage = coordinator.ssh("umask 077; mktemp -d /tmp/hf-render-receipt.XXXXXXXX").stdout.strip()
        coordinator.put(path, stage + "/receipt.json")
        coordinator.ssh(f"sudo -n install -o n_yu1791 -g n_yu1791 -m 0600 {shlex.quote(stage+'/receipt.json')} {SOURCE_DIR}/runtime/temporary-render-e2e/reboot-e2e-receipt.json")
    print(json.dumps({"reboot_e2e_verified": True, "completed_jobs": 2}))


def _coordinator_snapshot_command(expected_sha):
    program = f'''import json,os,pathlib,shlex,sqlite3,stat,subprocess,sys,time
root=pathlib.Path('/home/n_yu1791/hf-site-agent'); expected={expected_sha!r}; uid=os.getuid()
env=os.environ.copy(); env['XDG_RUNTIME_DIR']=f'/run/user/{{uid}}'; env['DBUS_SESSION_BUS_ADDRESS']=f"unix:path=/run/user/{{uid}}/bus"
def run(args,user=False):
 p=subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=12,check=False,env=env if user else None)
 return p.stdout.strip() if p.returncode==0 else ''
def user(unit,prop): return run(['systemctl','--user','show',unit,'--property='+prop,'--value'],True)
head=run(['git','-C',str(root),'rev-parse','HEAD']); clean=not run(['git','-C',str(root),'status','--porcelain'])
units=[x.split()[0] for x in run(['systemctl','list-unit-files','actions.runner.*.service','--no-legend','--no-pager']).splitlines() if x.split()]
runner_active=len(units)==1 and run(['systemctl','is-active',units[0]])=='active'
runner_enabled=len(units)==1 and run(['systemctl','is-enabled',units[0]]) in {{'enabled','enabled-runtime','static'}}
timer='hf-site-agent-media-news.timer'; source='hf-site-agent-media-source.timer'
boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(); boot_epoch=time.time()-float(pathlib.Path('/proc/uptime').read_text().split()[0])
queue_total=0; rss_fresh=False; rss_boot=False; provider_paused=False; fixtures=0
try:
 db=root/'runtime/media-queue.sqlite3'; c=sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True,timeout=4); c.execute('PRAGMA query_only=ON')
 queue_total=int(c.execute('SELECT COUNT(*) FROM source_inbox').fetchone()[0])
 p=json.loads((root/'config/media_source_ingress_policy.json').read_text()); enabled={{str(x['feed_id']) for x in p.get('feeds',[]) if isinstance(x,dict) and x.get('enabled') is True and x.get('feed_id')}}; stale=int(p.get('stale_after_seconds',0) or 0)
 feeds={{str(a):(float(b),str(d)) for a,b,d in c.execute('SELECT feed_id,last_checked_at,last_status FROM source_feed_state')}}
 rss_fresh=bool(enabled) and all(k in feeds and feeds[k][1] in {{'OK','NOT_MODIFIED'}} and 0<=time.time()-feeds[k][0]<=stale for k in enabled)
 rss_boot=rss_fresh and all(feeds[k][0]>=boot_epoch-5 for k in enabled)
 tables={{x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}}
 if 'media_news_provider_circuit' in tables:
  row=c.execute("SELECT state,http_status,reason_code FROM media_news_provider_circuit WHERE provider='openrouter'").fetchone(); provider_paused=bool(row and row[0]=='PAUSED' and row[1]==403 and row[2]=='CREDIT_OR_KEY_BUDGET_LIMIT')
 if 'render_e2e_jobs' in tables: fixtures=int(c.execute("SELECT COUNT(*) FROM render_e2e_jobs WHERE status='success' AND job_id IN ('first','second')").fetchone()[0])
 c.close()
except Exception: pass
health='BLOCKED'; worker_boot=''
try:
 f=pathlib.Path.home()/'.config/hf-site-agent/media-render.env'; st=f.lstat()
 if stat.S_ISREG(st.st_mode) and st.st_uid==uid and stat.S_IMODE(st.st_mode)==0o600:
  for raw in f.read_text().splitlines():
   parts=shlex.split(raw,comments=True)
   if len(parts)==1 and '=' in parts[0]:
    k,v=parts[0].split('=',1)
    if k.startswith('MEDIA_RENDER_'): os.environ[k]=v
  sys.path.insert(0,str(root)); from scripts.media_render_transport import check_remote_worker
  h=check_remote_worker(); health=str(h.get('status','BLOCKED')); worker_boot=str(h.get('worker_boot_id') or '')
except Exception: pass
print(json.dumps({{'boot_id':boot,'head':head,'expected_head':expected,'clean':clean,'media_service':user('hf-site-agent-media-news.service','ActiveState'),
 'timer_active':user(timer,'ActiveState')=='active','timer_enabled':user(timer,'UnitFileState') in {{'enabled','enabled-runtime','static'}},
 'source_timer_active':user(source,'ActiveState')=='active','source_timer_enabled':user(source,'UnitFileState') in {{'enabled','enabled-runtime','static'}},
 'runner_active':runner_active,'runner_enabled':runner_enabled,'runner_count':len(units),'linger':run(['loginctl','show-user','n_yu1791','--property=Linger','--value'])=='yes',
 'rss_fresh':rss_fresh,'rss_this_boot':rss_boot,'queue_total':queue_total,'provider_paused':provider_paused,'fixture_jobs_success':fixtures,
 'worker_health':health,'worker_boot_id':worker_boot}},sort_keys=True))'''
    return "python3 -c " + shlex.quote(program)


def _record_coordinator_baseline(coordinator, expected_sha):
    program=f'''import datetime,json,os,pathlib,stat,subprocess,tempfile
root=pathlib.Path('/home/n_yu1791/hf-site-agent'); expected={expected_sha!r}; commit=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
if commit!=expected: raise SystemExit('coordinator commit does not match current PR head')
dest=pathlib.Path.home()/'.local/state/hf-site-agent/vm-control-baseline.json'; dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700); os.chmod(dest.parent,0o700)
try:
 st=dest.lstat()
 if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o600: raise SystemExit('existing baseline is not a protected regular file')
except FileNotFoundError: pass
payload={{'schema_version':'hf-vm-control-baseline-v1','configured_boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'configured_commit':commit,'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}}
fd,tmp=tempfile.mkstemp(prefix='.vm-control-baseline.',dir=str(dest.parent)); os.fchmod(fd,0o600)
try:
 with os.fdopen(fd,'w',encoding='utf-8') as out: json.dump(payload,out,sort_keys=True); out.write('\\n'); out.flush(); os.fsync(out.fileno())
 os.replace(tmp,dest)
finally:
 if os.path.exists(tmp): os.unlink(tmp)
print(json.dumps({{'baseline_recorded':True,'commit':commit,'secret_values':False}},sort_keys=True))'''
    raw=coordinator.ssh("python3 -c "+shlex.quote(program)).stdout.strip().splitlines()[-1]
    result=json.loads(raw)
    if result.get('baseline_recorded') is not True or result.get('commit')!=expected_sha:
        raise RuntimeError('Coordinator reboot baseline was not recorded for the current PR commit')


def coordinator_reboot_check(coordinator, worker, expected_sha):
    snapshot_cmd=_coordinator_snapshot_command(expected_sha)
    idle_deadline=time.monotonic()+1800
    while True:
        if worker_control(worker,'status').get('active_render') is True:
            before=None
        else:
            first=coordinator.ssh(snapshot_cmd,check=False)
            if first.returncode:
                raise RuntimeError('Could not read coordinator readiness before reboot')
            before=json.loads(first.stdout.strip().splitlines()[-1])
        if before is not None and before.get('media_service')=='inactive':
            break
        if time.monotonic()>=idle_deadline:
            raise RuntimeError('Active render or media preparation did not finish within 30 minutes; coordinator was not rebooted')
        print('Waiting for current render and media job to finish before coordinator reboot...',flush=True)
        time.sleep(10)
    required=('clean','timer_active','timer_enabled','source_timer_active','source_timer_enabled',
        'runner_active','runner_enabled','linger','rss_fresh','provider_paused','worker_health')
    if before.get('head')!=expected_sha or before.get('expected_head')!=expected_sha:
        raise RuntimeError('Coordinator must be on the current PR head with the media service idle before reboot')
    failed=[key for key in required if before.get(key) is not True and not (key=='worker_health' and before.get(key)=='READY')]
    if failed:
        raise RuntimeError('Coordinator pre-reboot checks failed: '+','.join(failed))
    _record_coordinator_baseline(coordinator,expected_sha)
    command(['gcloud','compute','instances','reset',coordinator.name,'--project',coordinator.project,
        '--zone',coordinator.zone,'--quiet'])
    deadline=time.monotonic()+900; last=None
    while time.monotonic()<deadline:
        current=coordinator.ssh(snapshot_cmd,check=False)
        if current.returncode==0:
            try:
                last=json.loads(current.stdout.strip().splitlines()[-1])
                recovered=(last.get('boot_id')!=before.get('boot_id') and last.get('head')==expected_sha
                    and last.get('media_service')=='inactive' and last.get('queue_total',0)>=before.get('queue_total',0)
                    and last.get('fixture_jobs_success',0)>=2 and last.get('worker_health')=='READY'
                    and all(last.get(key) is True for key in ('clean','timer_active','timer_enabled','source_timer_active',
                        'source_timer_enabled','runner_active','runner_enabled','linger','rss_fresh','rss_this_boot','provider_paused')))
                if recovered:
                    print(json.dumps({'coordinator_reboot_verified':True,'old_boot_id':before['boot_id'],
                        'new_boot_id':last['boot_id'],'commit':expected_sha,'runner_ready':True,'timers_ready':True,
                        'rss_observed_this_boot':True,'worker_health':'READY','fixture_jobs_preserved':last['fixture_jobs_success'],
                        'queue_preserved':True,'paid_provider_circuit':'PAUSED','paid_requests_sent':False},sort_keys=True))
                    return
            except (ValueError,TypeError,KeyError): pass
        print('Waiting for coordinator services, fresh RSS poll and render tunnel recovery...',flush=True)
        time.sleep(10)
    fields=('boot_id','head','media_service','timer_active','source_timer_active','runner_active',
        'rss_this_boot','worker_health','fixture_jobs_success','queue_total')
    safe_last={{key:last.get(key) for key in fields}} if last else {{}}
    raise RuntimeError('Coordinator reboot recovery timed out: '+json.dumps(safe_last,sort_keys=True))


def drain(coordinator, worker, stop_vm):
    worker_control(worker, "drain")
    deadline = time.monotonic() + 1900
    while time.monotonic() < deadline:
        state = worker_control(worker, "status")
        if state["safe_to_stop"]:
            break
        print("Draining: current render is still active; queue is preserved...", flush=True)
        time.sleep(10)
    else:
        raise RuntimeError("Drain timeout: do not stop the worker while rendering")
    with tempfile.TemporaryDirectory() as directory:
        stamp = str(int(time.time()))
        remote = worker.ssh("umask 077; mktemp /tmp/hf-render-backup.XXXXXXXX.tar.gz").stdout.strip()
        owner = worker.ssh("id -u").stdout.strip()
        group = worker.ssh("id -g").stdout.strip()
        if not owner.isdecimal() or not group.isdecimal():
            raise RuntimeError("Backup destination ownership could not be verified")
        backup_program = "import os,pathlib,tarfile; root=pathlib.Path('/var/lib/hf-render-worker'); paths=[root/n for n in ['request-ledger','render-logs','active-job.json','heartbeat.json']]+list(root.glob('response-*.tar.gz')); archive=tarfile.open(" + repr(remote) + ", 'w:gz'); [archive.add(p,arcname=p.relative_to(root).as_posix()) for p in paths if p.exists()]; archive.close(); os.chmod(" + repr(remote) + ",0o600); os.chown(" + repr(remote) + "," + owner + "," + group + ")"
        worker.ssh("sudo -n python3 -c " + shlex.quote(backup_program))
        local = Path(directory) / "worker-state.tar.gz"
        worker.get(remote, local)
        digest = __import__("hashlib").sha256(local.read_bytes()).hexdigest()
        stage = coordinator.ssh("umask 077; mktemp -d /tmp/hf-render-backup.XXXXXXXX").stdout.strip()
        coordinator.put(local, stage + "/worker-state.tar.gz")
        destination = f"{SOURCE_DIR}/runtime/temporary-render-worker-backup/{stamp}.tar.gz"
        coordinator.ssh("sudo -n install -d -o n_yu1791 -g n_yu1791 -m 0700 " + SOURCE_DIR + "/runtime/temporary-render-worker-backup && sudo -n install -o n_yu1791 -g n_yu1791 -m 0600 " + shlex.quote(stage + "/worker-state.tar.gz") + " " + shlex.quote(destination))
        actual = coordinator.ssh("sudo -n -u n_yu1791 sha256sum " + shlex.quote(destination)).stdout.split()[0]
        if actual != digest:
            raise RuntimeError("Backup verification failed; worker must remain on")
    worker.ssh("sudo -n systemctl stop hf-render-worker.service hf-render-worker-tunnel.service")
    print(json.dumps({"drained": True, "backup": destination, "sha256": digest, "safe_to_stop_vm": True}))
    if stop_vm:
        command(["gcloud", "compute", "instances", "stop", WORKER, "--project", worker.project, "--zone", worker.zone, "--quiet"])
        print(json.dumps({"vm_stopped": WORKER, "vm_deleted": False}))


def run_operation(args):
    inventory = json.loads(command(["gcloud", "compute", "instances", "list", "--project", args.project, "--format=json"]).stdout)
    selected = {}
    for name in (COORDINATOR, WORKER):
        matches = [item for item in inventory if item["name"] == name]
        if len(matches) != 1:
            raise RuntimeError(f"Exactly one existing {name} must be visible in this project")
        selected[name] = Connection(args.project, matches[0])
    coordinator, worker = selected[COORDINATOR], selected[WORKER]
    print(json.dumps({"project": args.project, "coordinator_zone": coordinator.zone, "worker_zone": worker.zone, "new_vms_created": False}))
    sha = current_source()
    if args.operation == "probe":
        print(json.dumps({"canonical_commit": sha, "existing_vms_found": True}))
    elif args.operation == "setup":
        setup(coordinator, worker, sha, args.provision_render_auth)
        if args.complete_e2e:
            reboot_check(coordinator, worker)
    elif args.operation == "reboot-check":
        reboot_check(coordinator, worker)
    elif args.operation == "coordinator-reboot-check":
        coordinator_reboot_check(coordinator, worker, sha)
    elif args.operation in ("drain", "stop"):
        drain(coordinator, worker, stop_vm=args.operation == "stop")
    else:
        worker_control(worker, "resume")
        worker.ssh("sudo -n systemctl start hf-render-worker.service hf-render-worker-tunnel.service")
        wait_ready(coordinator)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--operation", choices=["probe", "setup", "reboot-check", "coordinator-reboot-check", "drain", "stop", "resume"], default="probe")
    parser.add_argument("--provision-render-auth", action="store_true")
    parser.add_argument("--complete-e2e", action="store_true")
    args = parser.parse_args()
    with operation_lock():
        run_operation(args)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        raise SystemExit(2)
