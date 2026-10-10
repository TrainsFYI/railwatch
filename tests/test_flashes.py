import datetime as dt

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from railwatcher.app import create_app
from railwatcher.config import ConfigStore, Landmark
from railwatcher.flashes import FlashIndex, analyse_flashes, flash_windows
from railwatcher.footage import FootageBuffer


def clip(path, kind):
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),5,(160,90))
    assert writer.isOpened()
    for i in range(60):
        frame=np.full((90,160,3),40,np.uint8)
        on=kind=='steady' or (kind in {'flashing','outside','white','alternating','dim'} and i%5<2) or (kind=='brief' and i<2)
        if on:
            x=100 if kind=='outside' else 30
            frame[20:30,x:x+10]=(240,240,240) if kind=='white' else (25,25,70) if kind=='dim' else (20,20,240)
        if kind=='alternating' and not on:
            frame[20:30,42:47]=(20,20,240)
        writer.write(frame)
    writer.release()


@pytest.mark.parametrize('kind,expected', [('flashing','motion'),('alternating','motion'),('dim','motion'),('outside','quiet'),
    ('steady','quiet'),('brief','quiet'),('white','quiet'),('quiet','quiet')])
def test_flash_detection_requires_repeated_red_in_fixed_box(tmp_path,kind,expected):
    path=tmp_path/'test.mp4'
    clip(path,kind)
    landmark=Landmark(id='lamp',label='Crossing light',box=(.15,.15,.3,.4))
    result=analyse_flashes(path,[landmark])
    assert result['state']==expected
    if expected=='motion':
        assert result['windows'][0]['label']=='Crossing light'
        assert result['windows'][0]['landmark_id']=='lamp'
        assert result['windows'][0]['ended']>3


def test_irregular_red_pulses_are_not_crossing_flashes():
    samples=[(i*.2,100 if i in {2,3,15,16,40,41} else 0) for i in range(60)]
    assert flash_windows(samples)==[]


def test_flash_cache_invalidation_and_activity_api(tmp_path):
    config=ConfigStore(tmp_path)
    landmark={'id':'lamp','label':'Crossing','box':[.15,.15,.3,.4]}
    config.update({'landmarks':[landmark]})
    footage=FootageBuffer(tmp_path/'buffer')
    name=dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%S.mp4')
    clip(footage.directory/name,'flashing')
    row=footage.list()['segments'][0]
    index=FlashIndex(footage,config)
    index.scan(row)
    assert index.result(row)['state']=='motion'
    assert FlashIndex(footage,config).result(row)['state']=='motion'
    app=create_app(tmp_path,workers=False)
    with TestClient(app) as client:
        response=client.get('/api/buffer?activity=red_flashes&movement=motion')
        assert response.status_code==200
        assert response.json()['segments'][0]['motion']['windows'][0]['label']=='Crossing'
        assert response.json()['activity']=='red_flashes'
        assert client.get('/api/buffer?activity=bad').status_code==422
    config.update({'landmarks':[{**landmark,'box':[.6,.15,.8,.4]}]})
    assert index.result(row)['state']=='pending'
    index.scan(row)
    assert index.result(row)['state']=='quiet'


def test_flash_decode_errors_are_not_quiet(tmp_path):
    path=tmp_path/'broken.mp4'
    path.write_bytes(b'not video')
    with pytest.raises(ValueError):
        analyse_flashes(path,[])
