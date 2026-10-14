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
