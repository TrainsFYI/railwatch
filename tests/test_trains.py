import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from railwatcher.app import create_app
from railwatcher.config import Settings
from railwatcher.detection import TrainDetector
from railwatcher.trains import analyse_trains


class FakeNetwork:
    def setInput(self, blob):
        self.blob = blob

    def forward(self):
        result = np.zeros((1,84,2),np.float32)
        result[0,:4,:] = [[320,32],[320,32],[10,10],[10,10]]
        result[0,4,:] = [.99,.99]  # COCO person must not count as train.
        result[0,10,:] = [.8,.95]
        return result


def test_manifest_selects_train_class_and_cropped_centers_map_to_original(tmp_path,monkeypatch):
    path=tmp_path/'train.onnx'
    manifest={'classes':{str(i):'train' if i==6 else str(i) for i in range(80)},
              'train_class_id':6,'focus_on_area':True}
    path.with_suffix('.json').write_text(json.dumps(manifest))
    network=FakeNetwork()
    monkeypatch.setattr(cv2.dnn,'readNetFromONNX',lambda _:network)
    detector=TrainDetector(path)
    frame=np.zeros((540,960,3),np.uint8)
    mask=[(.4,.4),(.6,.4),(.6,.6),(.4,.6)]
    assert detector.confidence(frame,mask)==pytest.approx(.8)
    assert detector.confidence(frame)==pytest.approx(.95)
    assert network.blob.shape==(1,3,640,640)
    path.with_suffix('.json').write_text(json.dumps({**manifest,'train_class_id':0}))
    with pytest.raises(ValueError,match='train class'):
        TrainDetector(path)


def write_clip(path):
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),5,(160,90))
    for i in range(60):
        writer.write(np.full((90,160,3),200 if 10<=i<30 else 20,np.uint8))
    writer.release()


@pytest.mark.parametrize('confirmations,expected',[(3,'motion'),(10,'quiet')])
def test_archive_uses_live_confirmations_area_threshold_and_pre_post_roll(tmp_path,confirmations,expected):
    path=tmp_path/'sample.mp4'
    write_clip(path)
    mask=[(.1,.1),(.9,.1),(.9,.9),(.1,.9)]
    settings=Settings(confirmations=confirmations,pre_roll=3,post_roll=3,detection_mask=mask)

    class Detector:
        def confidence(self,frame,detection_mask):
            assert detection_mask==mask
            return .8 if frame.mean()>100 else .1

    result=analyse_trains(path,Detector(),settings)
    assert result['state']==expected
    assert result['peak_confidence']==.8
    if expected=='motion':
        assert len(result['windows'])==1
        assert result['windows'][0]['started']==0
        assert 7<=result['windows'][0]['ended']<=9


def test_train_activity_filter_does_not_use_motion_results(tmp_path):
    app=create_app(tmp_path,workers=False)
    footage=app.state.engine.footage
    row={'id':'sample.mp4','bytes':10,'started':0,'ended':120,'video_url':'/api/buffer/sample.mp4'}
    footage.list=lambda:{'segments':[row],'available_seconds':120}
    app.state.trains.cache[row['id']]={'signature':app.state.trains.signature(row),
        'result':{'state':'motion','windows':[{'started':10,'ended':50}],'peak_confidence':.9}}
    app.state.motion.cache[row['id']]={'signature':app.state.motion.signature(row),
        'result':{'state':'quiet','windows':[]}}
    with TestClient(app) as client:
        result=client.get('/api/buffer?activity=trains&movement=motion').json()
        assert result['segments'][0]['motion']['peak_confidence']==.9
        assert client.get('/api/buffer?activity=movement&movement=motion').json()['segments']==[]


def test_new_footage_is_prioritized_during_historical_backfill(tmp_path):
    from railwatcher.config import ConfigStore
    from railwatcher.footage import FootageBuffer
    from railwatcher.trains import TrainIndex

    footage = FootageBuffer(tmp_path/'buffer')
    rows = [{'id':'old.mp4'}, {'id':'recent.mp4'}]
    footage._segments = lambda: rows.copy()
    index = TrainIndex(footage,ConfigStore(tmp_path),tmp_path/'missing.onnx')
    index.result = lambda _: {'state':'pending'}
    scanned = []

    def scan(row):
        scanned.append(row['id'])
        if len(scanned)==1:
            rows.append({'id':'new.mp4'})
        else:
            index.stop.set()

    index.scan = scan
    index.stop.wait = lambda _: None
    index._work()
    assert scanned==['recent.mp4','new.mp4']
