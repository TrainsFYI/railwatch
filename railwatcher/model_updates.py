"""Versioned model downloads from the RailWatch release bucket."""
import hashlib
import json
import os
import re
import threading
from pathlib import Path

import cv2
import httpx
import numpy as np

from .detection import TrainDetector
from .local_analysis import TypeClassifier

SOURCE = 'https://storage.googleapis.com/trainsfyi-railwatch-models/'
MAX_BYTES = 256*1024*1024
NAMES = {'detector':'Train detector', 'train_type':'Freight / passenger classifier'}
FILES = {'detector':'train.onnx','train_type':'train-type.onnx'}


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2))
    temporary.replace(path)


class ModelUpdates:
    def __init__(self, directory, bundled, config, camera_id_provider=None):
        self.directory=Path(directory)/'model-releases';self.directory.mkdir(exist_ok=True)
        self.state_path=self.directory/'active.json'
        self.bundled=Path(bundled);self.config=config
        self.camera_id_provider=camera_id_provider or (lambda:None)
        self.active=json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        self.catalog=None;self.error=None;self.busy=None
        self.lock=threading.RLock();self.thread=None
        self.source=os.environ.get('RAILWATCH_MODEL_SOURCE',SOURCE)
        if self.source != SOURCE:
            raise ValueError('Unsupported model release source')

    def path(self, identity):
        item=self.active.get(identity)
        if item:
            path=self.directory/identity/item['sha256']/FILES[identity]
            if path.is_file():
                return path
        return self.bundled if identity=='detector' else self.bundled.parent/'train-type.onnx'

    def compatible(self, item):
        if item.get('camera_ids') and self.camera_id_provider() not in item['camera_ids']:
            return False
        scope=item.get('source_sha256')
        return not scope or scope==hashlib.sha256(self.config.get().source.encode()).hexdigest()

    def public(self):
        with self.lock:
            models=[]
            for identity,name in NAMES.items():
                path=self.path(identity);manifest=path.with_suffix('.json')
                data=json.loads(manifest.read_text()) if manifest.exists() else {}
                installed=self.active.get(identity)
                available=next((m for m in (self.catalog or {}).get('models',[]) if m['id']==identity),None)
                models.append({'id':identity,'name':name,'installed':path.is_file(),
                               'version':installed['version'] if installed else data.get('version',data.get('architecture','Bundled model')) if path.is_file() else None,
                               'sha256':installed['sha256'] if installed else data.get('onnx_sha256',data.get('sha256')),
                               'latest_version':available['version'] if available else None,
                               'compatible':self.compatible(available) if available else None,
                               'scope':available.get('scope','General') if available else None})
            return {'models':models,'source':self.source,'busy':self.busy,'error':self.error,
                    'algorithms':[{'name':'Direction estimation','version':'device-analysis-v1','weights':'Included in app'},
                                  {'name':'Approximate speed','version':'two_landmark_affine_optical_flow-v1','weights':'Included in app'}]}

    def check(self, client):
        response=client.get(self.source+'catalog-v1.json');response.raise_for_status()
        if len(response.content)>65536:
            raise ValueError('Model catalog is too large')
        catalog=response.json()
        if catalog.get('schema_version')!=1 or not isinstance(catalog.get('models'),list):
            raise ValueError('Invalid model catalog')
        ids=set()
        for item in catalog['models']:
            if item.get('id') not in FILES or item['id'] in ids:
                raise ValueError('Invalid model identity')
            ids.add(item['id'])
            if 'camera_ids' in item and (not isinstance(item['camera_ids'],list) or not item['camera_ids'] or any(not isinstance(value,str) or not value for value in item['camera_ids'])):
                raise ValueError('Invalid model camera scope')
            if not isinstance(item.get('version'),str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}',item['version']):
                raise ValueError('Invalid model version')
            if not re.fullmatch(r'[a-f0-9]{64}',str(item.get('sha256',''))):
                raise ValueError('Invalid model checksum')
            if type(item.get('bytes')) is not int or not 0<item['bytes']<=MAX_BYTES:
                raise ValueError('Invalid model size')
            if not isinstance(item.get('url'),str) or not item['url'].startswith(self.source) or '..' in item['url']:
                raise ValueError('Model must come from the release bucket')
            if not isinstance(item.get('manifest'),dict):
                raise TypeError('Model metadata is required')
        with self.lock:
            self.catalog=catalog
        return catalog

    def install(self, identity, client):
        catalog=self.check(client)
        item=next((m for m in catalog['models'] if m['id']==identity),None)
        if item is None or not self.compatible(item):
            raise ValueError('No compatible model is published for this camera')
        directory=self.directory/identity/item['sha256'];directory.mkdir(parents=True,exist_ok=True)
        path=directory/FILES[identity];staging=directory/'download.part'
        digest=hashlib.sha256();size=0
        try:
            with client.stream('GET',item['url']) as response:
                response.raise_for_status()
                with staging.open('wb') as handle:
                    for chunk in response.iter_bytes(1024*1024):
                        size+=len(chunk)
                        if size>item['bytes']:
                            raise ValueError('Model download exceeded its declared size')
                        digest.update(chunk);handle.write(chunk)
            if size!=item['bytes'] or digest.hexdigest()!=item['sha256']:
                raise ValueError('Model checksum did not match; existing model kept')
            # Never overwrite an installed immutable release; validate before activation.
            candidate=directory/('candidate-'+FILES[identity]);staging.replace(candidate)
            metadata={**item['manifest'],'version':item['version']}
            metadata['onnx_sha256' if identity=='detector' else 'sha256']=item['sha256']
            if identity=='train_type':
                metadata['source_sha256']=hashlib.sha256(self.config.get().source.encode()).hexdigest()
            candidate.with_suffix('.json').write_text(json.dumps(metadata))
            if identity=='detector':
                detector=TrainDetector(candidate)
                detector.confidence(np.zeros((360,640,3),np.uint8))
            else:
                classifier=TypeClassifier(candidate,{'source_sha256':hashlib.sha256(self.config.get().source.encode()).hexdigest()})
                classifier.net.setInput(np.zeros((1,3,classifier.size[1],classifier.size[0]),np.float32))
                output=classifier.net.forward().reshape(-1)
                if len(output)!=2 or not np.isfinite(output).all():
                    raise ValueError('Classifier output is incompatible')
            if not self.compatible(item):
                raise ValueError('Camera source changed during download; model not activated')
            candidate.replace(path)
            atomic_json(path.with_suffix('.json'),metadata)
            with self.lock:
                active={**self.active,identity:{'sha256':item['sha256'],'version':item['version']}}
                atomic_json(self.state_path,active);self.active=active
        finally:
            staging.unlink(missing_ok=True)

    def launch(self, identity=None):
        with self.lock:
            if self.busy:
                raise ValueError('A model download or update check is already running')
            self.busy=identity or 'checking';self.error=None
        def work():
            try:
                with httpx.Client(timeout=120,follow_redirects=False) as client:
                    if identity is None:self.check(client)
                    else:self.install(identity,client)
            except (httpx.HTTPError, OSError, ValueError, TypeError, cv2.error):
                # Do not expose signed URLs, tokens, or transport internals in the UI.
                self.error='Model update failed. Check the source and camera compatibility; the previous model is still active.'
            finally:
                with self.lock:self.busy=None
        self.thread=threading.Thread(target=work,name='model-update',daemon=True);self.thread.start()
