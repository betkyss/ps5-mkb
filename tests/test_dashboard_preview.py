"""Targeted HTTP checks for the loopback-only preview fixture."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from serve_dashboard_isolated import PreviewFake, PreviewHandler, Store, Service, Server, PRESET, BASE


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.adapter=PreviewFake()
        self.service=Service(Store(self.tmp.name,PRESET,BASE),self.adapter)
        self.server=Server(('127.0.0.1',0),self.service)
        self.server.RequestHandlerClass=PreviewHandler
        self.server.preview_assets=Path(__file__).resolve().parents[1]/'dashboard/web-isolated'
        self.server.preview_delay=.06
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)
        deadline=time.monotonic()+1
        while self.service.sampling and time.monotonic()<deadline:time.sleep(.005)
        self.tmp.cleanup()

    def request(self,path,body=None,**overrides):
        host='127.0.0.1:'+str(self.server.server_address[1])
        headers={'Host':host,'Origin':'http://'+host,'Content-Type':'application/json','X-CSRF-Token':self.server.csrf}
        headers.update(overrides)
        conn=http.client.HTTPConnection(host,timeout=3)
        conn.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,headers)
        response=conn.getresponse();data=response.read();status=response.status;kind=response.getheader('Content-Type');conn.close()
        return status,kind,data

    def test_security_errors_are_json_on_static_and_failure_overrides(self):
        self.adapter.response_mode='failure'
        for path,body in [('/',None),('/app.js',None),('/style.css',None),('/api/launch',{'revision':1}),('/_fixture',{'mode':'ready'})]:
            for header,value,reason in [('Host','evil.invalid','Host'),('Origin','http://evil.invalid','Origin')]:
                with self.subTest(path=path,header=header):
                    status,kind,data=self.request(path,body,**{header:value})
                    self.assertEqual(status,400)
                    self.assertIn('application/json',kind)
                    self.assertIn(reason,json.loads(data)['error'])
        self.assertEqual(self.adapter.calls,[])

    def test_absent_client_can_fail_then_launch_with_delayed_ack(self):
        status,_,_=self.request('/_fixture',{'clientPresent':False,'responseMode':'failure'})
        self.assertEqual(status,200)
        self.assertFalse(self.adapter.status()['processAlive'])
        status,_,data=self.request('/api/launch',{'revision':1})
        self.assertEqual(status,503)
        self.assertIn('Команда не выполнена',json.loads(data)['error'])
        self.assertFalse(self.adapter.client_present)
        status,_,_=self.request('/_fixture',{'clientPresent':False,'responseMode':'pending'})
        self.assertEqual(status,200)
        started=time.monotonic()
        status,_,data=self.request('/api/launch',{'revision':1})
        self.assertEqual(status,200,json.loads(data))
        self.assertGreaterEqual(time.monotonic()-started,self.server.preview_delay)
        self.assertTrue(self.adapter.client_present)
        self.assertEqual(self.adapter.response_mode,'pending')
        self.assertTrue(json.loads(data)['status']['processAlive'])


if __name__=='__main__':unittest.main()
