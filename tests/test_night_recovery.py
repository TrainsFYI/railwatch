import datetime as dt
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from railwatcher.config import Settings
from railwatcher.footage import FootageBuffer
from railwatcher.night_recovery import NightRecovery, in_night_window
from railwatcher.passages import Passages
from railwatcher.store import Store

BASE = dt.datetime(2026,10,21,1,tzinfo=dt.UTC).timestamp()


def settings(**changes):
    return Settings(enabled=True,setup_complete=True,latitude=43,longitude=-81,night_recovery_enabled=True,night_review_timezone='America/Toronto',
                    landmarks=[{'id':'crossing','label':'crossing lights','box':[.1,.2,.2,.3]}],**changes)


def make_recovery(tmp_path, config=None):
    config=config or settings()
    store=Store(tmp_path)
    passages=Passages(store)
    footage=SimpleNamespace(_segments=list,find=lambda identity:identity,export_mark=Mock())
    pairing=SimpleNamespace(credential=lambda:{'camera':{'id':'camera-one'}})
    worker=NightRecovery(footage,SimpleNamespace(get=lambda:config),store,passages,pairing,lambda:'a'*64)
    worker.state.update(signature=worker.signature(config),cursor=BASE)
    return worker,config


def segments():
    return [{'id':str(i),'started':BASE+i*120,'ended':BASE+(i+1)*120} for i in range(3)]


def test_cadence_windows_merge_across_segments_and_wait_for_inspected_tail(tmp_path,monkeypatch):
    worker,config=make_recovery(tmp_path)
    signature=worker.signature(config)
    worker.add_windows(segments()[0],[{'started':100,'ended':110,'landmark_id':'crossing'}],signature)
    worker.add_windows(segments()[1],[{'started':5,'ended':30,'landmark_id':'crossing'}],signature)
    assert len(worker.state['episodes'])==1
    episode=worker.state['episodes'][0]
    assert episode['first_signal']==BASE+100 and episode['last_signal']==BASE+150
    worker.state['cursor']=BASE+239
    worker.finish_ready(segments(),config,signature)
    worker.footage.export_mark.assert_not_called()
    worker.state['cursor']=BASE+240
    worker.passages.submit_history=Mock(return_value='passage-one')
    worker.finish_ready(segments(),config,signature)
    assert episode['state']=='submitted'
    args,kwargs=worker.footage.export_mark.call_args
    assert args[2:]==(BASE,BASE+195)
    assert kwargs['candidate_evidence']['status']=='unconfirmed'
    assert kwargs['candidate_evidence']['requires_train_review']
    assert worker.passages.submit_history.call_args.kwargs['incomplete_reason']=='night_crossing_signal_unconfirmed'
    worker.finish_ready(segments(),config,signature)
    worker.footage.export_mark.assert_called_once()
    restarted=NightRecovery(worker.footage,worker.config,worker.store,worker.passages,worker.pairing,worker.model_provider)
    restarted.finish_ready(segments(),config,signature)
    worker.footage.export_mark.assert_called_once()


def test_deterministic_identity_and_long_signal_splits_are_bounded(tmp_path):
    worker,config=make_recovery(tmp_path);signature=worker.signature(config)
    first={'started':10,'ended':20,'landmark_id':'crossing'}
    worker.add_windows(segments()[0],[first],signature)
    identity=worker.state['episodes'][0]['id']
    worker.state['episodes']=[]
    worker.add_windows(segments()[0],[first],signature)
    assert worker.state['episodes'][0]['id']==identity
    for stamp in range(30,750,30):
        worker.add_windows({'started':BASE+stamp,'ended':BASE+stamp+120},[first],signature)
    assert len(worker.state['episodes'])==2
    assert all(row['last_signal']-row['first_signal']<=600 for row in worker.state['episodes'])


def test_opt_in_pairing_and_enabled_capture_are_required(tmp_path):
    worker,config=make_recovery(tmp_path)
    worker.footage._segments=Mock(return_value=segments())
    for change in ['night_recovery_enabled','enabled']:
        setattr(config,change,False);worker.scan_once();setattr(config,change,True)
    worker.pairing.credential=lambda:None
    worker.scan_once()
    worker.footage._segments.assert_not_called()
    assert not Settings().night_recovery_enabled


def test_daytime_and_empty_windows_do_not_create_candidates(tmp_path,monkeypatch):
    worker,config=make_recovery(tmp_path)
    worker.footage._segments=lambda:segments()
    monkeypatch.setattr('railwatcher.night_recovery.time.time',lambda:BASE+1000)
    with patch('railwatcher.night_recovery.analyse_flashes',return_value={'windows':[]}) as analyse:
        worker.scan_once()
        analyse.assert_called_once()
    assert worker.state['episodes']==[]
    assert in_night_window(BASE,config)
    assert not in_night_window(BASE+12*3600,config)


def test_signal_failure_retries_segment_and_view_change_holds_old_candidates(tmp_path,monkeypatch):
    worker,config=make_recovery(tmp_path)
    worker.add_windows(segments()[0],[{'started':10,'ended':15,'landmark_id':'crossing'}],worker.signature(config))
    worker.footage._segments=lambda:segments()
    monkeypatch.setattr('railwatcher.night_recovery.time.time',lambda:BASE+1000)
    with patch('railwatcher.night_recovery.analyse_flashes',side_effect=OSError),pytest.raises(OSError):
        worker.scan_once()
    assert worker.state['cursor']==BASE
    config.source='/dev/video1'
    worker.scan_once()
    assert worker.state['episodes'][0]['state']=='held_configuration_changed'
    worker.footage.export_mark.assert_not_called()


def test_existing_confirmed_capture_avoids_duplicate_recovery(tmp_path):
    worker,config=make_recovery(tmp_path);signature=worker.signature(config)
    worker.add_windows(segments()[0],[{'started':10,'ended':20,'landmark_id':'crossing'}],signature)
    identity=worker.passages.start(config,BASE,'a'*64)
    worker.passages.mutate(identity,lambda value:value.update(detected_ended=BASE+30))
    worker.passages.end()
    worker.state['cursor']=BASE+240
    worker.finish_ready(segments(),config,signature)
    assert worker.state['episodes'][0]['state']=='covered_by_live_detection'
    worker.footage.export_mark.assert_not_called()


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='FFmpeg required')
def test_real_candidate_export_is_idempotent_unreviewed_and_cannot_claim_live_passage(tmp_path,monkeypatch):
    config=settings();store=Store(tmp_path);passages=Passages(store)
    footage=FootageBuffer(tmp_path/'buffer',SimpleNamespace(get=lambda:config))
    monkeypatch.setattr('railwatcher.footage.time.time',lambda:BASE+240)
    source=footage.directory/'20261021T010000.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=black:s=320x180:r=5',
                    '-t','8','-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
    evidence={'method':'fixed_landmark_red_flash_v1','status':'unconfirmed'}
    identity='night-candidate-one'
    first=footage.export_mark(store,config,BASE,BASE+5,event_id=identity,candidate_evidence=evidence)
    assert first['confidence']==0
    metadata=json.loads(first['metadata'])
    assert not metadata['manual_review'] and metadata['review_reset']
    assert 'user_review' not in metadata
    assert metadata['night_candidate_evidence']==evidence
    modified=(store.clips/first['filename']).stat().st_mtime_ns
    second=footage.export_mark(store,config,BASE,BASE+5,event_id=identity,candidate_evidence=evidence)
    assert second['id']==first['id'] and (store.clips/first['filename']).stat().st_mtime_ns==modified
    with pytest.raises(ValueError,match='identity'):
        footage.export_mark(store,config,BASE+1,BASE+5,event_id=identity,candidate_evidence=evidence)
    passage_id=passages.submit_history(identity,config,'a'*64,incomplete_reason='night_crossing_signal_unconfirmed')
    assert passages.submit_history(identity,config,'a'*64,incomplete_reason='night_crossing_signal_unconfirmed')==passage_id
    contract=passages.contract(passages.pending(),'camera-one')
    assert contract['passage']['origin']=='owner_submitted_archive'
    assert not contract['passage']['complete']
    assert contract['passage']['incomplete_reason']=='night_crossing_signal_unconfirmed'
    assert contract['edge_observations']==[]
    assert contract['capture']['started_at']=='2026-10-21T01:00:00+00:00'
    assert contract['camera_context']['training_intervals'][0]['label']=='unreviewed_train_candidate'


def test_expired_candidate_is_audited_without_blocking_later_episodes(tmp_path):
    worker,config=make_recovery(tmp_path);signature=worker.signature(config)
    worker.state['episodes']=[{'id':'expired','clip_id':'expired','signature':signature,'state':'exporting',
                               'started':BASE-30,'ended':BASE+30,'first_signal':BASE,'last_signal':BASE+10,
                               'landmark_ids':['crossing']}]
    worker.state['cursor']=BASE+240
    worker.finish_ready(segments(),config,signature)
    assert worker.state['episodes'][0]['state']=='footage_expired'
    assert worker.public()['held_candidates']==1
    worker.footage.export_mark.assert_not_called()
