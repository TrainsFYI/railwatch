import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from railwatcher.releases import extract_bundle, newest_release, release_asset, version_key
from railwatcher.software_updates import INTERVAL, SoftwareUpdates


def release(tag='v0.1.2', prerelease=True):
    name=f'railwatch-{tag.removeprefix("v")}.tar.gz'
    return {'tag_name':tag,'prerelease':prerelease,'draft':False,'published_at':'2026-10-20T00:00:00Z',
            'assets':[{'name':name,'state':'uploaded','size':3,'digest':'sha256:'+hashlib.sha256(b'abc').hexdigest(),
                       'browser_download_url':f'https://github.com/TrainsFYI/railwatch/releases/download/{tag}/{name}'}]}


def test_semantic_selection_includes_prereleases_but_not_drafts_or_unverified_assets():
    draft=release('v9.0.0');draft['draft']=True
    incomplete=release('v8.0.0');incomplete['assets']=[]
    assert newest_release([release('v0.1.9'),release('v0.1.10'),draft,incomplete])['tag_name']=='v0.1.10'
    assert version_key('0.2.0rc1')==version_key('v0.2.0-rc.1')
    assert version_key('v1.0.0')>version_key('v1.0.0-rc.2')>version_key('v1.0.0-beta.10')
    for invalid in ['../1.0.0','v1.0.0;reboot','main','1.0','01.0.0']:
        with pytest.raises(ValueError):
            version_key(invalid)
    external=release();external['assets'][0]['browser_download_url']='https://evil.example/bundle'
    with pytest.raises(ValueError):
        release_asset(external)


def test_daily_checks_survive_restarts_and_offline_keeps_known_update(tmp_path):
    updates=SoftwareUpdates(tmp_path,current='0.1.1')
    with patch('railwatcher.software_updates.fetch_json',return_value=[release()]) as fetch:
        updates.check();updates.check()
        restarted=SoftwareUpdates(tmp_path,current='0.1.1');restarted.check()
        assert fetch.call_count==1
    assert restarted.public()['update_available']
    restarted.state['checked_at']-=INTERVAL
    with patch('railwatcher.software_updates.fetch_json',side_effect=OSError):
        restarted.check()
    assert restarted.public()['latest_version']=='v0.1.2'
    assert restarted.public()['error']
    assert not SoftwareUpdates(tmp_path,current='0.1.3').public()['update_available']


def test_update_request_exclusive_and_docker_has_manual_instructions(tmp_path):
    runtime=tmp_path/'run';(runtime/'inbox').mkdir(parents=True)
    updates=SoftwareUpdates(tmp_path,runtime=runtime,current='0.1.1')
    updates.state={'latest_version':'v0.1.2'}
    with pytest.raises(ValueError,match='Raspberry Pi'):
        updates.request_install()
    (runtime/'enabled').touch()
    updates.request_install()
    assert json.loads((runtime/'inbox/request.json').read_text())=={'version':'v0.1.2'}
    with pytest.raises(ValueError,match='already'):
        updates.request_install()
    (runtime/'inbox/request.json').unlink()
    (runtime/'status.json').write_text(json.dumps({'version':'v0.1.2','status':'installing'}))
    with pytest.raises(ValueError,match='already'):
        updates.request_install()


def test_update_api_obeys_browser_guard_and_never_accepts_arbitrary_urls(tmp_path):
    from railwatcher.app import create_app
    app=create_app(tmp_path,workers=False)
    client=TestClient(app)
    assert client.get('/api/software').json()['current_version']=='0.1.1'
    assert client.post('/api/software/install').status_code==403
    assert client.post('/api/software/install',headers={'X-Railwatcher':'1','Origin':'https://evil.example'}).status_code==403
    assert client.post('/api/software/install',headers={'X-Railwatcher':'1'},json={'url':'https://evil.example'}).status_code==409


@pytest.mark.parametrize('name,kind',[('../escape','file'),('/absolute','file'),('bundle/link','symlink')])
def test_archive_rejects_traversal_and_links(tmp_path,name,kind):
    archive=tmp_path/'test.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        info=tarfile.TarInfo(name)
        if kind=='symlink':
            info.type=tarfile.SYMTYPE;info.linkname='/etc'
            tar.addfile(info)
        else:
            info.size=1;tar.addfile(info,io.BytesIO(b'x'))
    with pytest.raises(ValueError,match='Unsafe'):
        extract_bundle(archive,tmp_path/'out')
    assert not (tmp_path/'escape').exists()


def test_bundle_download_checks_github_digest_before_install(tmp_path):
    from railwatcher.releases import download_bundle
    with patch('urllib.request.urlopen',return_value=io.BytesIO(b'abc')):
        download_bundle(release(),tmp_path/'good')
    with patch('urllib.request.urlopen',return_value=io.BytesIO(b'bad')),pytest.raises(ValueError,match='checksum'):
        download_bundle(release(),tmp_path/'bad')


def helper_module(monkeypatch):
    original=importlib.util.spec_from_file_location
    script=Path(__file__).parents[1]/'scripts/update_helper.py'
    spec=original('update_helper',script)
    module=importlib.util.module_from_spec(spec)
    def locate(name,path,*args,**kwargs):
        if name=='release_support':
            path=Path(__file__).parents[1]/'railwatcher/releases.py'
        return original(name,path,*args,**kwargs)
    monkeypatch.setattr(importlib.util,'spec_from_file_location',locate)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('health',[True,False])
def test_privileged_update_keeps_data_and_rolls_back_unhealthy_release(tmp_path,monkeypatch,health):
    helper=helper_module(monkeypatch)
    base=tmp_path/'opt';base.mkdir();old=base/'venv';old.mkdir();(base/'current').symlink_to(old)
    runtime=tmp_path/'run';(runtime/'inbox').mkdir(parents=True)
    (runtime/'inbox/request.json').write_text(json.dumps({'version':'v0.1.2'}))
    data=tmp_path/'data';data.mkdir();(data/'camera.json').write_text('unchanged')
    source=tmp_path/'source';source.mkdir()
    monkeypatch.setattr(helper,'BASE',base);monkeypatch.setattr(helper,'RUNTIME',runtime)
    monkeypatch.setattr(helper,'fetch_json',lambda url:release())
    monkeypatch.setattr(helper,'download_bundle',lambda *args:None)
    monkeypatch.setattr(helper,'extract_bundle',lambda *args:source)
    monkeypatch.setattr(helper,'wait_until_idle',lambda:None)
    commands=[]
    def run(*arguments,**kwargs):
        commands.append(arguments)
        return '0.1.1' if str(old) in arguments[0] else '0.1.2'
    monkeypatch.setattr(helper,'run',run)
    monkeypatch.setattr(helper,'healthy',lambda expected:health if expected=='0.1.2' else True)
    if health:
        helper.main()
        assert (base/'current').resolve()!=old
    else:
        with pytest.raises(ValueError,match='health check'):
            helper.main()
        assert (base/'current').resolve()==old
    status=json.loads((runtime/'status.json').read_text())
    assert status['status']==('complete' if health else 'failed')
    assert (data/'camera.json').read_text()=='unchanged'
    assert any(args[:2]==('/usr/bin/systemctl','restart') for args in commands)


def test_privileged_helper_rejects_invalid_version_before_network_or_commands(tmp_path,monkeypatch):
    helper=helper_module(monkeypatch)
    runtime=tmp_path/'run';(runtime/'inbox').mkdir(parents=True)
    (runtime/'inbox/request.json').write_text(json.dumps({'version':'../../evil'}))
    monkeypatch.setattr(helper,'RUNTIME',runtime)
    with patch.object(helper,'run') as run,patch.object(helper,'fetch_json') as fetch,pytest.raises(ValueError):
        helper.main()
    run.assert_not_called();fetch.assert_not_called()


def test_curl_pipe_wizard_reads_confirmation_from_terminal_before_installing(tmp_path):
    """The script's stdin contains code, so interactive answers must use /dev/tty."""
    import errno
    import os
    import pty
    import select
    import time

    tools=tmp_path/'bin';tools.mkdir()
    uname=tools/'uname';uname.write_text('#!/bin/sh\nif [ "$1" = "-s" ]; then echo Linux; else echo aarch64; fi\n');uname.chmod(0o755)
    installer=Path(__file__).parents[1]/'install.sh'
    pid,descriptor=pty.fork()
    if pid==0:
        os.environ['PATH']=str(tools)+os.pathsep+os.environ['PATH']
        os.execl('/bin/bash','bash','-c',f'cat "{installer}" | bash')
    output=b''
    try:
        deadline=time.monotonic()+10
        answered=False
        while time.monotonic()<deadline:
            if not select.select([descriptor],[],[],.2)[0]:
                continue
            try:
                part=os.read(descriptor,4096)
            except OSError as error:
                if error.errno==errno.EIO:
                    break
                raise
            if not part:
                break
            output+=part
            if b'Ready to install?' in output and not answered:
                os.write(descriptor,b'n\n');answered=True
        if not answered:
            os.kill(pid,15)
        _,status=os.waitpid(pid,0)
        assert os.waitstatus_to_exitcode(status)==0,output.decode()
        assert answered
        assert b'Installing download tools' not in output
    finally:
        os.close(descriptor)
