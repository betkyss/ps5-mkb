"""Audio commands use the production schema and independent service lane."""
import copy
from pathlib import Path
import sys
import tempfile
import threading
import http.client
import json
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.schema import audio_request, Problem
from dashboard.service import Service
from dashboard.store import Store
from dashboard.server import Server

class Audio(unittest.TestCase):
    def body(self, **action):
        return dict(operationId='op',processId='p',sessionId='s',sessionGeneration=1,audioRevision=2,**action)

    def test_strict(self):
        for action in (dict(setVolume=0),dict(setVolume=100),dict(setOutputMuted=True),dict(setOutputMuted=False)):
            audio_request(self.body(**action))
        for action in (dict(),dict(setVolume=True),dict(setVolume=float('nan')),dict(setVolume=101),dict(setVolume=-1),dict(setMicrophoneEnabled=1),dict(setOutputMuted=None),dict(setVolume=1,setOutputMuted=True)):
            with self.assertRaises(Problem):audio_request(self.body(**action))
        for key,value in (('audioRevision',True),('sessionGeneration',-1),('sessionId',''),('operationId',42),('audioRevision',2.0),('expectedDigest','x')):
            bad=self.body(setVolume=42);bad[key]=value
            with self.assertRaises(Problem):audio_request(bad)

    def test_microphone_is_unknown_without_runtime_action(self):
        for enabled in (False, True):
            with self.assertRaisesRegex(Problem, 'Неизвестное поле'):
                audio_request(self.body(setMicrophoneEnabled=enabled))
            service=Service.__new__(Service)
            service.adapter=Mock()
            with self.assertRaisesRegex(Problem, 'Неизвестное поле'):
                service.mutate('audio',self.body(setMicrophoneEnabled=enabled))
            service.adapter.audio.assert_not_called()

    def test_independent_lane(self):
        with tempfile.TemporaryDirectory() as d:
            import json
            root=Path(__file__).resolve().parents[1]
            preset=json.loads((root/'profiles/arc-raiders-dashboard.json').read_text())
            baseline=json.loads((root/'profiles/arc-raiders-linear-zero-deadzone.json').read_text())
            store=Store(Path(d),preset,baseline);adapter=Mock();adapter.audio.return_value={'accepted':True,'status':{'audio':{'audioRevision':3}}}
            service=Service(store,adapter);before=copy.deepcopy(service.saved)
            service.operation={'busy':'profile'}
            service.lock.acquire()
            try:
                result=service.mutate('audio',self.body(setVolume=50))
            finally:service.lock.release()
            self.assertTrue(result['accepted']);self.assertEqual(before,service.saved)
            self.assertEqual(service.release_generation,0);self.assertEqual(service.sample_epoch,0)
            adapter.audio.assert_called_once()

    def test_http_output_bypasses_profile_and_rejects_microphone(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(__file__).resolve().parents[1]
            preset=json.loads((root/'profiles/arc-raiders-dashboard.json').read_text())
            baseline=json.loads((root/'profiles/arc-raiders-linear-zero-deadzone.json').read_text())
            adapter=Mock();entered=threading.Event();finish=threading.Event();calls=[]
            def audio(body):
                calls.append(body)
                if 'setVolume' in body:
                    entered.set()
                    self.assertTrue(finish.wait(3))
                return dict(accepted=True,status={'audio':{'audioRevision':3}})
            adapter.audio.side_effect=audio
            service=Service(Store(directory,preset,baseline),adapter)
            server=Server(('127.0.0.1',0),service)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            port=server.server_address[1]
            def post(body,csrf=True):
                connection=http.client.HTTPConnection('127.0.0.1',port,timeout=4)
                try:
                    headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{port}',
                             'X-CSRF-Token':server.csrf if csrf else 'wrong'}
                    connection.request('POST','/api/audio',json.dumps(body),headers)
                    response=connection.getresponse();response.read();return response.status
                finally:connection.close()
            volume=threading.Thread(target=lambda:post(self.body(setVolume=50)),daemon=True)
            try:
                service.lock.acquire();volume.start();self.assertTrue(entered.wait(2))
                self.assertEqual(post(self.body(setOutputMuted=True)),200)
                for enabled in (False,True):
                    self.assertEqual(post(self.body(setMicrophoneEnabled=enabled)),400)
                self.assertEqual(post(self.body(setOutputMuted=False),False),400)
                self.assertEqual(len(calls),2)
            finally:
                finish.set();service.lock.release();volume.join(4);server.shutdown();server.server_close();thread.join(4)

if __name__=='__main__':unittest.main()
