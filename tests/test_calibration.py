import pytest
from pydantic import ValidationError

from railwatcher.config import ConfigStore, RailCalibration


def proposal():
    return {'revision':'new','points':[
        {'image':[.2,.4],'latitude':43.,'longitude':-79.},
        {'image':[.8,.4],'latitude':43.,'longitude':-78.999}]}


def test_calibration_revision_and_camera_pose_invalidation(tmp_path):
    store=ConfigStore(tmp_path)
    a=store.update({'railway_calibration':proposal()}).railway_calibration
    assert a.revision!='new'
    assert store.update({'name':'Renamed'}).railway_calibration==a
    b=store.update({'railway_calibration':proposal()}).railway_calibration
    assert b.revision!=a.revision
    assert store.update({'bearing':90}).railway_calibration is None
    store.update({'railway_calibration':proposal()})
    assert store.update({'railway_calibration':None}).railway_calibration is None


def test_bad_coordinates_rejected():
    for patch in [{'image':[-1,0]}, {'latitude':float('nan')}, {'longitude':-79.}, {'verified':True}]:
        data=proposal();data['points'][1].update(patch)
        with pytest.raises(ValidationError):RailCalibration.model_validate(data)


def test_passage_freezes_calibration_at_capture_time(tmp_path):
    from railwatcher.config import Settings
    from railwatcher.passages import Passages
    from railwatcher.store import Store
    store=Store(tmp_path);passages=Passages(store)
    settings=Settings(railway_calibration=proposal())
    identity=passages.start(settings,12,'model')
    settings.railway_calibration=None
    store.create('part',0,.9,{})
    path=store.clips/'part.mp4';path.write_bytes(b'video')
    store.update('part',ended=30,filename=path.name,status='pending',bytes=5)
    passages.add_part(identity,'part');passages.end()
    assert passages.contract(passages.pending(),'camera')['camera']['calibration_revision']=='new'


def test_malformed_calibration_does_not_replace_config(tmp_path):
    store=ConfigStore(tmp_path)
    for value in ['invalid', [], 12]:
        with pytest.raises(ValidationError):store.update({'railway_calibration':value})
    assert store.get().railway_calibration is None


def test_single_point_draft_survives_reload_and_pose_changes_clear_it(tmp_path):
    store=ConfigStore(tmp_path)
    point=proposal()['points'][0]
    store.update({'railway_calibration_draft':[point]})
    restored=ConfigStore(tmp_path).get()
    assert len(restored.railway_calibration_draft)==1
    assert restored.railway_calibration is None
    assert store.update({'bearing':90}).railway_calibration_draft==[]


def test_invalid_point_draft_cannot_replace_saved_point(tmp_path):
    store=ConfigStore(tmp_path)
    point=proposal()['points'][0]
    store.update({'railway_calibration_draft':[point]})
    for invalid in [[point]*4,[{**point,'image':[-1,0]}]]:
        with pytest.raises(ValidationError):store.update({'railway_calibration_draft':invalid})
    assert len(ConfigStore(tmp_path).get().railway_calibration_draft)==1
