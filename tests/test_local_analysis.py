import hashlib
import json
from unittest.mock import patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from railwatcher.app import create_app
from railwatcher.local_analysis import LocalAnalysis, TypeClassifier, classification, motion_estimate


def rail():
    return {'revision':'pose-one','points':[
        {'image':[.1,.5], 'latitude':43., 'longitude':-79.},
        {'image':[.9,.5], 'latitude':43., 'longitude':-78.999},
    ]}


def test_classification_abstains_without_consistent_high_confidence():
    assert classification([])['value'] is None
    assert classification([[.7,.3]]*10)['value'] is None
    assert classification([[.99,.01]]*3+[[.01,.99]]*2)['value'] is None
    assert classification([[.01,.99]]*5)['value']=='passenger'
    assert classification([[.99,.01]]*5)['value']=='freight'


def test_motion_and_approximate_speed_need_enough_consistent_motion_and_landmarks():
    assert motion_estimate([[10,0]]*3,rail(),(360,640),True)['direction']['value'] is None
    assert motion_estimate([[0,0]]*10,rail(),(360,640),True)['speed']['value'] is None
    value=motion_estimate([[10,0]]*10,None,(360,640),True)
    assert value['direction']['value']=='right'
    assert value['speed']['reason']=='rail_landmarks_required'
    assert motion_estimate([[10,0]]*10,rail(),(360,640))['speed']['reason']=='approximate_speed_not_enabled'
    value=motion_estimate([[10,0]]*10,rail(),(360,640),True)
    assert value['speed']['approximate'] is True
    assert 5<value['speed']['value']<6
    assert 89<value['direction']['bearing_degrees']<91
    assert motion_estimate([[0,10]]*10,rail(),(360,640),True)['speed']['value'] is None
    assert motion_estimate([[10,0]]*5+[[-10,0]]*5,rail(),(360,640),True)['direction']['value'] is None


def test_classifier_model_identity_and_camera_scope(tmp_path):
    model=tmp_path/'train-type.onnx';model.write_bytes(b'model')
    metadata={'sha256':hashlib.sha256(b'model').hexdigest(),'labels':['freight','passenger'],
              'source_sha256':'camera-one','input_size':224,'minimum_context_luminance':30}
    model.with_suffix('.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError,match='scoped'):
        TypeClassifier(model,{'source_sha256':'another-camera'})
    with patch('cv2.dnn.readNetFromONNX'):
        TypeClassifier(model,{'source_sha256':'camera-one'})
    model.write_bytes(b'changed')
    with pytest.raises(ValueError,match='identity'):
        TypeClassifier(model,{'source_sha256':'camera-one'})


def test_worker_persists_estimates_separately_and_review_type_survives(tmp_path):
    app=create_app(tmp_path,workers=False);store=app.state.store
    store.create('one',100,.7,{'analysis_context':{'source_sha256':'source'}})
    store.update('one',ended=120,filename='one.mp4',status='uploaded')
    (store.clips/'one.mp4').write_bytes(b'clip')
    result={'version':'device-analysis-v1','train_type':{'value':'freight'},'direction':{'value':'right'},'speed':{'value':None}}
    with patch('railwatcher.local_analysis.analyse_clip',return_value=result) as analyse:
        app.state.local_analysis.scan();app.state.local_analysis.scan()
        assert analyse.call_count==1
    client=TestClient(app);headers={'X-Railwatcher':'1'}
    assert client.get('/api/sightings').json()[0]['train_type']=='freight'
    response=client.post('/api/sightings/one/review',json={'decision':'confirmed','train_type':'passenger'},headers=headers)
    assert response.status_code==200
    row=client.get('/api/sightings').json()[0]
    assert row['train_type']=='passenger'
    assert row['local_analysis']['train_type']['value']=='freight'
    for kind in ['mixed',{},[]]:
        assert client.post('/api/sightings/one/review',json={'decision':'confirmed','train_type':kind},headers=headers).status_code==422
    assert client.post('/api/sightings/one/review',json={'decision':'dismissed','train_type':'passenger'},headers=headers).status_code==422
    client.post('/api/sightings/one/review',json={'decision':'dismissed'},headers=headers)
    row=client.get('/api/sightings').json()[0]
    assert row['train_type'] is None and row['user_train_type'] is None
    assert LocalAnalysis(store,app.state.engine.passages,tmp_path/'missing.onnx').results()['one']==result


def test_train_region_optical_flow_rejects_background_only_and_measures_motion():
    import cv2

    from railwatcher.local_analysis import flow_vector

    rng=np.random.default_rng(7)
    background=rng.integers(0,256,(180,320),dtype=np.uint8)
    background=cv2.GaussianBlur(background,(3,3),0)
    before=background.copy()
    texture=rng.integers(0,256,(80,120),dtype=np.uint8)
    before[50:130,80:200]=texture
    after=background.copy();after[50:130,85:205]=texture
    value=flow_vector(before,after,[.25,.27,.64,.73],.5)
    assert value is not None
    assert abs(value[0]-10)<1 and abs(value[1])<1
    assert flow_vector(before,before,[.25,.27,.64,.73],.5) is None


def test_rerun_and_reset_preserve_upload_and_capture(tmp_path):
    app=create_app(tmp_path,workers=False);store=app.state.store;worker=app.state.local_analysis
    store.create('old',100,.7,{'analysis_context':{'source_sha256':'source'}})
    store.update('old',ended=120,filename='old.mp4',status='uploaded',remote_id='remote-one',attempts=1)
    (store.clips/'old.mp4').write_bytes(b'clip')
    model=tmp_path/'model.onnx';model.write_bytes(b'model');worker.detector_provider=lambda:model
    original=dict(store.get('old'))
    result={'train_type':{'value':'freight'},'direction':{'value':'right'},'speed':{'value':None}}
    client=TestClient(app);headers={'X-Railwatcher':'1'}
    with patch('railwatcher.local_analysis.detect_recording',return_value=([],{'value':'train'})) as detector,patch('railwatcher.local_analysis.analyse_clip',return_value=result):
        assert client.post('/api/sightings/old/analysis/rerun',headers=headers).status_code==200
        worker.scan()
        assert detector.call_count==1
        assert worker.states()['old']=='completed'
        assert worker.results()['old']['presence']['value']=='train'
        assert dict(store.get('old'))==original
        assert client.post('/api/sightings/old/analysis/reset',headers=headers).status_code==200
        worker.scan()
        assert 'old' not in worker.results()
        assert detector.call_count==1
        worker.request('old')
        def reset_during_analysis(*args):
            worker.request('old',reset=True)
            return result
        with patch('railwatcher.local_analysis.analyse_clip',side_effect=reset_during_analysis):worker.scan()
        assert 'old' not in worker.results()
    client.post('/api/sightings/old/review',json={'decision':'confirmed','train_type':'passenger'},headers=headers)
    assert client.post('/api/sightings/old/review/reset',headers=headers).status_code==200
    row=client.get('/api/sightings').json()[0]
    assert row['review_status']=='required' and row['user_train_type'] is None
    assert row['status']=='uploaded' and store.get('old')['remote_id']=='remote-one'
    assert client.post('/api/sightings/missing/analysis/rerun',headers=headers).status_code==404
    (store.clips/'old.mp4').unlink()
    assert client.post('/api/sightings/old/analysis/rerun',headers=headers).status_code==409
