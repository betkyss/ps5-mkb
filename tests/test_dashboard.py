"""Поведенческие проверки store, apply/rollback, HTTP и native adapter без PS5."""
import copy
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.schema import Problem, validate, loads, encode, digest, source, CODES, RESERVED
from dashboard.store import Store
from dashboard.service import Service
from dashboard.server import Server, main as run_server
from dashboard.adapters import Native, Baseline, Adapters
ROOT = Path(__file__).resolve().parents[1]
PRESET = loads((ROOT / 'profiles/arc-raiders-dashboard.json').read_bytes())
BASE = loads((ROOT / 'profiles/arc-raiders-linear-zero-deadzone.json').read_bytes())


class RadialMenuTests(unittest.TestCase):
    def test_schema_roundtrip(self):
        p = copy.deepcopy(PRESET)
        p.pop('radial_menu', None)
        self.assertEqual(validate(loads(encode(p))), p)
        p['radial_menu'] = dict(enabled=True, gain=.015, open_actions=['L1', 'R1', 'Left'])
        self.assertEqual(validate(loads(encode(p))), p)
        for delay in (0, 350, 1500):
            profile = copy.deepcopy(p)
            profile['radial_menu']['open_delay_ms'] = delay
            self.assertEqual(validate(loads(encode(profile))), profile)
        for delay in (True, False, -1, 1501, .5, float('nan'), float('inf'), '350', None):
            profile = copy.deepcopy(p)
            profile['radial_menu']['open_delay_ms'] = delay
            with self.subTest(delay=delay), self.assertRaises(Problem):
                validate(profile)

        for value in (None, True, {}, dict(enabled=1, gain=.015, open_actions=['L1']),
                      dict(enabled=True, gain=0, open_actions=['L1']),
                      dict(enabled=True, gain=float('nan'), open_actions=['L1']),
                      dict(enabled=True, gain=.015, open_actions=[]),
                      dict(enabled=True, gain=.015, open_actions=['L1', 'L1']),
                      dict(enabled=True, gain=.015, open_actions=['Right']),
                      dict(enabled=True, gain=.015, open_actions=[1]),
                      dict(enabled=True, gain=.015, open_actions=['L1'], extra=1)):
            with self.subTest(value=value), self.assertRaises(Problem):
                validate({**p, 'radial_menu': value})
        p['radial_menu'] = dict(enabled=False, gain=.0001, open_actions=[])
        self.assertEqual(validate(p), p)


class Fake:
    def __init__(self):
        self.current = copy.deepcopy(BASE)
        self.capture = 'active'
        self.calls = []
        self.fail = None
        self.session = 's'
        self.process = 'p'
        self.can_apply = True
        self.revision = 0
        self.epoch = 0
        self.control_id = ""
        self.capture_generation = 1

    def status(self):
        return dict(processAlive=True, connected=True, capture=self.capture, canApply=self.can_apply,
                    processId=self.process, sessionId=self.session, digest=digest(self.current),
                    adapter="native", sessionGeneration=1, captureGeneration=self.capture_generation, workerRevision=self.revision, controlEpoch=self.epoch, controlOperationId=self.control_id)

    def control(self, command, expected=None, operation_id=None, cancelled=None):
        self.calls.append(command)
        self.epoch += 1
        if command=="arm":
            self.control_id=operation_id or self.control_id
            self.capture_generation+=1
        if self.fail == 'release':
            raise Problem('neutral timeout')
        self.capture = 'off' if command == 'release' else 'active'
        return dict(self.status(), releaseConfirmed=True, releaseProof='native-cleanup', cleanupFinished=True)

    def prepare_launch(self, snapshot_factory):
        if self.fail == 'launch-conflict':
            raise Problem('already active')
        path=snapshot_factory()
        return dict(profilePath=str(path),launchNonce='a'*32,binary='fixture',binaryDigest='a'*64,profileDigest=digest(loads(path.read_bytes())))

    def launch(self, snapshot_factory, cancelled=None, spawn=None, plan=None):
        path=Path(plan['profilePath']) if plan else snapshot_factory()
        self.current=loads(path.read_bytes())
        self.capture='off'
        return self.status()

    def apply(self, profile, expected=None, operation_id=None):
        self.calls.append('apply')
        self.control_id=operation_id or self.control_id
        self.epoch+=1
        if self.fail == 'apply':
            raise Problem('stale permission / disconnect')
        self.capture = 'off'
        self.current = copy.deepcopy(profile)
        self.revision += 1
        if self.fail == 'timeout':
            self.fail = None
            raise Problem('timeout после apply')
        if self.fail == 'digest':
            return dict(self.status(), digest='wrong')
        return dict(self.status(), cleanupFinished=True)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name, copy.deepcopy(PRESET), copy.deepcopy(BASE))
        self.adapter = Fake()
        self.service = Service(self.store, self.adapter)

    def tearDown(self):
        self.tmp.cleanup()

    def sample_status(self):
        # Wait only in tests. Production HTTP never waits for the sampler.
        self.service.sampled = 0
        previous = self.service.sample_sequence
        end = time.monotonic()+2
        while time.monotonic()<end:
            result = self.service.status()
            if self.service.sample_sequence>previous:
                return self.service.status()
            time.sleep(.005)
        self.fail('sampler did not finish')

    def call(self, command, **extra):
        return self.service.mutate(command, dict(revision=self.store.data['revision'], **extra))


class LiveSettings(Fixture):
    def setUp(self):
        super().setUp()
        original = self.adapter.status
        self.runtime = dict(fresh=True, enabled=True, strengthPercent=7.5,
                            rapidFireEnabled=True, runtimeRevision=4, profileRevision=0)
        self.adapter.status = lambda: dict(original(), recoil=copy.deepcopy(self.runtime))
        self.adapter.live = self.live

    def live(self, profile, before, operation_id):
        self.adapter.calls.append('live')
        self.assertEqual(before['recoil']['runtimeRevision'], 4)
        self.adapter.current = copy.deepcopy(profile)
        self.adapter.control_id = operation_id
        self.adapter.epoch += 1
        self.adapter.revision += 1
        self.runtime['profileRevision'] = self.adapter.revision
        return self.adapter.status()

    def test_active_ack_persists_hotkeys_and_unmodified_fields(self):
        profile = copy.deepcopy(PRESET)
        profile['name'] = 'live neighbor'
        result = self.call('live', profile=profile, runtimeFields=[])
        saved = result['store']['profiles'][result['store']['selected']]
        self.assertEqual(saved['recoil']['vertical'], .075)
        self.assertTrue(saved['recoil']['enabled'])
        self.assertTrue(saved['rapid_fire']['enabled'])
        for key in ('hip', 'ads', 'binds', 'dpi', 'radial_deadzone'):
            self.assertEqual(saved.get(key), profile.get(key))
        self.assertEqual(result['status']['capture'], 'active')
        self.assertEqual(result['status']['captureGeneration'], 1)
        self.assertEqual(self.adapter.calls, ['live'])
        self.service.check_ack_generation(result[Service.ACK_GENERATION])
        self.call('release')
        with self.assertRaises(Problem):
            self.service.check_ack_generation(result[Service.ACK_GENERATION])

    def test_off_update_never_arms(self):
        self.adapter.capture = 'off'
        result = self.call('live', profile=PRESET, runtimeFields=[])
        self.assertEqual(result['status']['capture'], 'off')
        self.assertEqual(self.adapter.calls, ['live'])
        self.assertEqual(self.adapter.current, PRESET)

    def test_explicit_fields_override_runtime_and_invalid_never_sends(self):
        profile = copy.deepcopy(PRESET)
        profile['recoil'] = dict(enabled=False, vertical=.02)
        profile['rapid_fire'] = dict(enabled=False, interval_ms=160, press_ms=48)
        result = self.call('live', profile=profile, runtimeFields=['enabled', 'vertical', 'rapidFireEnabled'])
        self.assertEqual(self.adapter.current, profile)
        self.assertFalse(self.service.journal.pending())
        for value in (None, -1, .26):
            invalid = copy.deepcopy(profile); invalid['recoil']['vertical'] = value
            with self.assertRaises(Problem): self.call('live', profile=invalid, runtimeFields=[])
        invalid = copy.deepcopy(profile); invalid['rapid_fire']['press_ms'] = 17
        with self.assertRaises(Problem): self.call('live', profile=invalid, runtimeFields=[])
        self.assertEqual(self.adapter.calls, ['live'])

    def test_disconnect_and_uncertain_ack_keep_durable_recovery(self):
        prior = copy.deepcopy(self.store.data)
        def lose(profile, before, operation_id):
            self.live(profile, before, operation_id)
            raise Problem('connection lost', 503)
        self.adapter.live = lose
        with self.assertRaises(Problem): self.call('live', profile=PRESET, runtimeFields=[])
        self.assertEqual(self.store.data, prior)
        self.assertTrue(self.service.journal.pending())
        self.assertTrue(self.service.recovery_required)
        self.call('recover')
        self.assertEqual(self.adapter.capture, 'off')
        self.assertEqual(self.adapter.calls, ['live', 'release'])
        self.assertFalse(self.service.journal.pending())

    def test_release_during_native_update_rejects_late_ack(self):
        def release_first(profile, before, operation_id):
            result = self.live(profile, before, operation_id)
            self.call('release')
            return result
        self.adapter.live = release_first
        with self.assertRaisesRegex(Problem, 'отменена'):
            self.call('live', profile=PRESET, runtimeFields=[])
        self.assertEqual(self.adapter.capture, 'off')
        self.assertTrue(self.service.journal.pending())
        self.assertEqual(self.adapter.calls, ['live', 'release'])


class ReviewRegressions(Fixture):
    def test_radial_save_restart_preserves_profile_and_settings(self):
        before = copy.deepcopy(self.store.data)
        profile = copy.deepcopy(before['profiles'][before['selected']])
        profile['radial_menu'] = dict(enabled=True, gain=.015, open_actions=['L1', 'R1', 'Left'])
        self.call('save', profile=profile)
        restarted = Store(self.tmp.name, PRESET, BASE)
        self.assertEqual(restarted.data['profiles'][before['selected']], profile)
        self.assertEqual(restarted.data['gameSettings'], before['gameSettings'])
        self.assertEqual(self.adapter.calls, [])

    def test_safety_release_ignores_stale_revision(self):
        self.call('save', profile=PRESET)
        result=self.service.mutate('release', {'revision':1})
        self.assertTrue(result['status']['releaseConfirmed'])
        self.assertEqual(self.adapter.capture,'off')
        self.assertEqual(self.store.data['revision'],2)
        self.assertTrue(self.service.mutate('release', {})['status']['releaseConfirmed'])

    def test_launch_snapshot_survives_save_restart_and_rejected_launch(self):
        p=copy.deepcopy(PRESET);p['name']='custom P';p['hip']['gain']=.027
        q=copy.deepcopy(PRESET);q['name']='custom Q';q['hip']['gain']=.031
        self.call('save',profile=p);self.call('launch')
        path=self.store.directory/('launch-'+digest(p)+'.json');snapshot=path.read_bytes()
        self.call('save',profile=q)
        self.adapter.fail='launch-conflict'
        with self.assertRaises(Problem):self.call('launch')
        self.assertEqual(snapshot,path.read_bytes())
        self.assertFalse((self.store.directory/('launch-'+digest(q)+'.json')).exists())
        restarted=Store(self.tmp.name,PRESET,BASE)
        self.assertEqual(restarted.launch_profile(digest(p)),p)
        service=Service(restarted,self.adapter);self.adapter.fail=None
        self.assertFalse(service.recovery_required)  # preflight failed before any intent
        service.mutate('apply',{'revision':restarted.data['revision']})
        self.assertEqual(self.adapter.current,q)
        # First-apply persistence failure can still roll back to launch P after restart.
        self.adapter.current=p;self.store=restarted;self.service=Service(restarted,self.adapter)
        with patch('dashboard.store.atomic_write',side_effect=OSError('disk full')),self.assertRaises(Problem):
            self.call('apply')
        self.assertEqual(self.adapter.current,q)
        self.assertTrue(self.service.journal.pending())

    def test_unknown_or_faulted_release_cannot_change_saved_selection(self):
        for result in ({'capture':'unknown'}, {'capture':'error','releaseConfirmed':True,'releaseProof':'helper-command'},
                       {'capture':'off','releaseConfirmed':True,'releaseProof':'native-cleanup','cleanupFinished':False}):
            with self.subTest(result=result),patch.object(self.adapter,'control',return_value=result):
                before=self.store.snapshot()
                with self.assertRaises(Problem):self.call('reset',kind='baseline')
                self.assertEqual(self.store.data,before)
        with patch.object(self.adapter,'control',return_value={'capture':'unknown','releaseConfirmed':True,'releaseProof':'helper-command','cleanupFinished':False}):
            self.call('reset',kind='baseline')
        self.assertEqual(self.store.data['profiles']['arc'],BASE)

    def test_recoil_telemetry_age_and_cancellation(self):
        with patch.object(self.adapter, 'status', return_value=dict(self.adapter.status(), recoil={
                'fresh': True, 'enabled': True, 'sampleAgeMs': 0, 'strengthPercent': 11})):
            result = self.sample_status()
        self.assertTrue(result['recoil']['fresh'])
        self.service.sampling = True
        self.service.sampled = time.monotonic() - 1.2
        result = self.service.status()
        self.assertFalse(result['recoil']['fresh'])
        self.assertGreaterEqual(result['recoil']['sampleAgeMs'], 1000)
        fresh = dict(result, statusEpoch=self.service.sample_epoch - 1, recoil={'fresh': True, 'sampleAgeMs': 0})
        self.assertFalse(self.service.fence_status(fresh)['recoil']['fresh'])
        self.assertTrue(fresh['recoil']['fresh'])
        self.service.sampling = False

    def test_all_commits_bounded_and_apply_rolls_back_near_limit(self):
        p=copy.deepcopy(PRESET)
        p['hip']['cm360']=p['ads']['cm360']=30;p['dpi']=800
        table=[[i,i/1000] for i in range(600)]
        p['hip']['x']=p['hip']['y']=table
        p['ads']['x']=p['ads']['y']=table
        data=self.store.snapshot();data['profiles']={'arc':p,'copy':copy.deepcopy(p)}
        # Fill with valid calibration until saved store is close to the limit.
        while len(encode(data))<56000:
            n=len(data['profiles']);data['profiles']['p'+str(n)]=copy.deepcopy(BASE)
        self.assertLess(len(encode(data)),65536)
        self.store.commit(data);self.service.saved=self.store.snapshot();before=self.store.snapshot();disk=self.store.path.read_bytes()
        with self.assertRaises(Problem):self.call('apply')
        self.assertEqual(self.adapter.current,p)
        self.assertTrue(self.service.journal.pending())
        self.assertEqual(self.store.data,before);self.assertEqual(self.store.path.read_bytes(),disk)
        self.assertEqual(Store(self.tmp.name,PRESET,BASE).data,before)
        oversized=copy.deepcopy(data);oversized['applied']={'digest':digest(p),'processId':'p','sessionId':'s','profile':p}
        self.assertGreater(len(encode(oversized)),65536)
        with self.assertRaises(Problem):self.store.commit(oversized)

    def test_huge_integer_rejected_without_overflow(self):
        p=copy.deepcopy(PRESET);p['dpi']=10**400
        with self.assertRaises(Problem):validate(p)


class Schema(unittest.TestCase):
    def test_profile_and_aliases(self):
        validate(PRESET)
        self.assertEqual(PRESET['binds']['14'], PRESET['binds']['15'])
        self.assertEqual(PRESET['hip'], PRESET['ads'])
        for key, action in {'57':'DodgeRoll','5':'Left','3':'R1','17':'Up','258':'R3','259':'Down','260':'Right'}.items():
            self.assertEqual(PRESET['binds'][key], action)
        self.assertEqual(set(PRESET['binds'].values()), set(__import__('dashboard.schema', fromlist=['ACTIONS']).ACTIONS) - {'RapidFireToggle','HoldLeft'})

    def test_rapid_fire(self):
        p = copy.deepcopy(PRESET)
        p['rapid_fire'] = {'enabled': True, 'interval_ms': 120, 'press_ms': 40}
        p['binds']['17'] = 'RapidFireToggle'
        validate(p)
        for key, values in {'enabled': (1, 'true', None),
                            'interval_ms': (0, 63, 121, 1008, True, '120'),
                            'press_ms': (0, 8, 17, 120, 496, True, '40')}.items():
            for value in values:
                q = copy.deepcopy(p);q['rapid_fire'][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(Problem):validate(q)
        for value in (None, [], {}, {'enabled': False}, dict(p['rapid_fire'], unknown=1)):
            q = copy.deepcopy(p);q['rapid_fire'] = value
            with self.subTest(value=value), self.assertRaises(Problem):validate(q)
        for interval, press in ((64,16),(1000,496),(120.0,40.0)):
            p['rapid_fire'].update(interval_ms=interval,press_ms=press);validate(p)

    def test_optional_recoil(self):
        p = copy.deepcopy(PRESET)
        p['recoil'] = {'vertical': .02}
        p['binds'].update({'97': 'RecoilToggle', '98': 'RecoilDecrease', '100': 'RecoilIncrease'})
        self.assertIs(validate(p), p)
        self.assertEqual(source('F6'), '97')
        self.assertEqual(source('F7'), '98')
        self.assertEqual(source('F8'), '100')
        for value in (None, [], {}, {'vertical': -1}, {'vertical': .251},
                      {'vertical': True}, {'vertical': '0.02'}, {'vertical': float('nan')},
                      {'vertical': .02, 'enabled': 'yes'}, {'vertical': .02, 'enabled': 1}):
            invalid = copy.deepcopy(p)
            invalid['recoil'] = value
            with self.subTest(value=value), self.assertRaises(Problem):
                validate(invalid)
        for enabled in (False, True):
            p['recoil']['enabled'] = enabled
            validate(p)
        for strength in (0, .25):
            p['recoil']['vertical'] = strength
            validate(p)

    def test_pulse_recoil_schema(self):
        p = copy.deepcopy(PRESET)
        p['recoil'] = {'enabled': True, 'vertical': .09, 'mode': 'pulse', 'pulse_peak': .11, 'period_ms': 120}
        validate(p)
        for key, values in {
            'mode': ('unknown', None, False),
            'pulse_peak': (0, .251, '0.11', True),
            'period_ms': (31, 33, 36, 121, 500, 501, 120.5, True, '120'),
        }.items():
            for value in values:
                q = copy.deepcopy(p);q['recoil'][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(Problem):validate(q)
        for period in (32, 120.0, 496):
            p['recoil']['period_ms'] = period;validate(p)

    def test_middle_profile(self):
        p = copy.deepcopy(PRESET)
        p['binds']['258'] = 'R3'
        p['binds']['259'] = 'Right'
        p['binds']['260'] = 'Down'
        validate(p)

    def test_gameplay_bindings(self):
        p = copy.deepcopy(PRESET)
        before = copy.deepcopy(p)
        p['binds'].update({'20': 'HoldLeft', '54': 'Moon', '55': 'Moon', '57': 'DodgeRoll'})
        self.assertIs(validate(p), p)
        for key in before.keys() - {'binds'}:
            self.assertEqual(p[key], before[key])
        for key in before['binds'].keys() - {'20', '54', '55', '57'}:
            self.assertEqual(p['binds'][key], before['binds'][key])
        for code, value in [('CapsLock', '57'), ('MetaLeft', '55'), ('MetaRight', '54')]:
            self.assertEqual(source(code), value)
        p['binds']['20'] = 'HoldRight'
        validate(p)
        for action in ['Moon', 'HoldLeft', 'HoldRight', 'Cross', 'RapidFireToggle']:
            p['binds']['57'] = action
            with self.subTest(action=action), self.assertRaises(Problem):
                validate(p)

    def test_codes(self):
        self.assertEqual(source('KeyE'), '14')
        self.assertEqual(source('KeyR'), '15')
        self.assertEqual(source('KeyI'), '34')
        self.assertEqual(source('KeyJ'), '38')
        self.assertEqual(source('KeyK'), '40')
        self.assertEqual(source('KeyL'), '37')
        self.assertEqual(source('MouseRight'), '257')
        self.assertEqual(source('MouseMiddle'), '258')
        self.assertEqual(source('MouseBack'), '259')
        self.assertEqual(source('MouseForward'), '260')
        for code in ['Escape', 'KeyM', 'AltLeft', 'ControlRight', 'Fn', 'MouseExtra']:
            with self.subTest(code=code), self.assertRaises(Problem):
                source(code)

    def test_invalid_inputs(self):
        for value in (b'{"name":"a","name":"b"}', b'{"a":1,"\\u0061":2}', b'{"a":NaN}', b'{"a":1e9999}', b'{"a":"\\ud800"}', b'['*17+b'0'+b']'*17, b'{'*70000, b'\xff'):
            with self.subTest(value=value[:30]), self.assertRaises(Problem):
                loads(value)
        for key, value in [('extra', 1), ('dpi', True), ('hip', {}), ('binds', {'261': 'Cross'}),
                           ('binds', {'53': 'Cross'}), ('binds', {'01': 'Cross'}), ('binds', {'14': 'Heal'}), ('name', '')]:
            p = copy.deepcopy(PRESET)
            p[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(Problem):
                validate(p)


class Persistence(Fixture):
    def test_save_reload_and_restart(self):
        p = copy.deepcopy(PRESET)
        p['hip']['gain'] = .02
        saved = self.call('save', profile=p)['store']
        self.assertIsNone(saved['applied'])
        other = Store(self.tmp.name, PRESET, BASE)
        self.assertEqual(other.data['profiles']['arc']['hip']['gain'], .02)
        self.assertEqual(other.data['revision'], 2)
        self.assertEqual(Path(self.tmp.name).stat().st_mode & 0o777, 0o700)
        self.assertEqual(other.path.stat().st_mode & 0o777, 0o600)

    def test_corruption_no_reset(self):
        self.store.path.write_text('{broken')
        with self.assertRaises(Problem):
            Store(self.tmp.name, PRESET, BASE)
        self.assertEqual(self.store.path.read_text(), '{broken')

    def test_stale_and_two_editors(self):
        barrier = threading.Barrier(3)
        results = []
        def save():
            barrier.wait()
            try:
                self.service.mutate('save', dict(revision=1, profile=PRESET))
                results.append(200)
            except Problem as exc:
                results.append(exc.status)
        threads = [threading.Thread(target=save) for _ in range(2)]
        for t in threads:
            t.start()
        barrier.wait()
        for t in threads:
            t.join()
        self.assertCountEqual(results, [200, 409])

    def test_failed_save_leaves_revision(self):
        before = self.store.snapshot()
        with patch('dashboard.store.atomic_write', side_effect=OSError('disk full')), self.assertRaises(Problem):
            self.call('save', profile=PRESET)
        self.assertEqual(self.store.data, before)

    def test_import_duplicate_unknown_rejected_before_mutation(self):
        for raw in ['{"version":1,"version":1}', json.dumps(dict(PRESET, unknown=1))]:
            with self.assertRaises(Problem):
                self.call('import', profileText=raw)
        self.assertEqual(self.store.data['revision'], 1)
        self.call('import', profileText=json.dumps(BASE))
        self.assertEqual(self.store.data['profiles']['arc'], BASE)

    def test_select_and_reset_release(self):
        self.call('create', id='copy', profile=BASE)
        self.assertEqual(self.adapter.capture, 'off')
        self.assertEqual(self.adapter.calls, ['release'])
        self.call('select', id='arc')
        self.call('reset', kind='baseline')
        self.assertEqual(self.store.data['profiles']['arc'], BASE)
        self.assertEqual(self.adapter.calls, ['release']*3)
        self.assertIsNone(self.store.data['applied'])

    def test_failed_release_blocks_reset(self):
        self.adapter.fail = 'release'
        with self.assertRaises(Problem):
            self.call('reset', kind='baseline')
        self.assertEqual(self.store.data['revision'], 1)


class GameSettings(Fixture):
    def test_legacy_checklist_preserves_unknown_details(self):
        detailed = {'lookBoostVertical', 'zoomBoostHorizontal', 'zoomBoostVertical', 'zoomRamp'}
        data = self.store.snapshot()
        for key in detailed:
            del data['gameSettings']['actual'][key]
        data['gameSettings']['actual']['lookBoost'] = 4
        data['gameSettings']['confirmedAt'] = '2026-10-24'
        self.store.commit(data)
        reloaded = Store(self.tmp.name, PRESET, BASE)
        self.assertEqual(reloaded.data['gameSettings']['actual']['lookBoost'], 4)
        self.assertFalse(detailed & reloaded.data['gameSettings']['actual'].keys())
        before = self.store.snapshot()
        with self.assertRaisesRegex(Problem, 'все значения'):
            self.call('settings', gameSettings=data['gameSettings'])
        self.assertEqual(self.store.data, before)

    def test_separate_boost_ramps_and_unconfirmed_values(self):
        settings = self.store.snapshot()['gameSettings']
        settings['actual'].update(horizontal=400, zoom=200, lookBoost=2,
                                  lookBoostVertical=3, zoomBoostHorizontal=4,
                                  zoomBoostVertical=5, ramp=1.6, zoomRamp=1.2)
        settings['confirmedAt'] = '2026-10-25'
        self.call('settings', gameSettings=settings)
        self.assertEqual(Store(self.tmp.name, PRESET, BASE).data['gameSettings'], settings)
        settings['actual']['zoomRamp'] = None
        with self.assertRaises(Problem):
            self.call('settings', gameSettings=settings)
        settings['confirmedAt'] = ''
        self.call('settings', gameSettings=settings)
        self.assertIsNone(self.store.data['gameSettings']['actual']['zoomRamp'])


class Apply(Fixture):

    def test_rejected_native_cannot_launch_apply_or_arm_but_baseline_can(self):
        (self.store.directory / 'native-rejected.json').write_text('{}')
        self.service.rejected=True
        self.adapter.launch = Mock()
        with self.assertRaises(Problem):
            self.call('launch')
        with self.assertRaises(Problem):
            self.call('apply')
        with self.assertRaises(Problem):
            self.call('arm')
        self.adapter.launch.assert_not_called()
        self.assertEqual(self.adapter.calls, [])
        self.assertTrue(self.sample_status()['nativeRejected'])
        self.assertFalse(self.sample_status()['canApply'])
        original = self.adapter.status
        self.adapter.status = lambda: dict(original(), adapter='baseline')
        self.call('arm')
        self.assertEqual(self.adapter.capture, 'active')
        self.call('release')
        self.assertEqual(self.adapter.capture, 'off')

    def test_ack_and_applied_identity(self):
        self.assertTrue(self.sample_status()['pendingProfile'])
        result = self.call('apply')
        self.assertEqual(self.adapter.capture, 'off')
        self.assertEqual(result['store']['applied']['digest'], digest(PRESET))
        self.assertTrue(self.sample_status()['appliedKnown'])
        self.adapter.session = 'other'
        self.assertFalse(self.sample_status()['appliedKnown'])

    def test_failure_no_false_success(self):
        for failure in ('apply', 'digest', 'timeout'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                store = Store(tmp, copy.deepcopy(PRESET), copy.deepcopy(BASE))
                adapter = Fake()
                service = Service(store, adapter)
                adapter.fail = failure
                with self.assertRaises(Problem):
                    service.mutate('apply', {'revision':1})
                self.assertEqual(adapter.current, BASE if failure=='apply' else PRESET)
                self.assertTrue(service.recovery_required)
                self.assertTrue(service.journal.pending())
                self.assertIsNone(store.data['applied'])

    def test_persist_failure_rolls_back(self):
        with patch('dashboard.store.atomic_write', side_effect=OSError('disk full')), self.assertRaises(Problem):
            self.call('apply')
        self.assertEqual(self.adapter.current, PRESET)
        self.assertTrue(self.service.journal.pending())
        self.assertEqual(self.store.data['revision'], 1)

    def test_rollback_failure_is_explicit(self):
        def fail(profile, expected, operation_id=None):
            self.adapter.current = copy.deepcopy(PRESET)
            raise Problem('native disconnected')
        self.adapter.apply = fail
        with self.assertRaisesRegex(Problem, 'native disconnected'):
            self.call('apply')
        self.assertIsNone(self.store.data['applied'])
        self.assertTrue(self.sample_status()['recoveryRequired'])
        with self.assertRaises(Problem):
            self.call('arm')
        self.assertTrue(Service(self.store, self.adapter).recovery_required)

    def test_baseline_no_apply(self):
        with self.assertRaises(Problem):
            Baseline().apply(PRESET)
        self.adapter.can_apply = False
        with self.assertRaises(Problem):
            self.call('apply')
        self.assertEqual(self.adapter.calls, [])

    def test_status_during_mutation(self):
        self.service.lock.acquire()
        try:
            s = self.sample_status()
            self.assertFalse(s['serviceBusy'])
            self.assertEqual(s['capture'], 'active')
            with self.assertRaises(Problem) as busy:
                self.call('save', profile=PRESET)
            self.assertEqual(busy.exception.status, 409)
        finally:
            self.service.lock.release()


class HTTP(Fixture):
    def setUp(self):
        super().setUp()
        self.server = Server(('127.0.0.1', 0), self.service)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def req(self, method='GET', path='/api/bootstrap', data=None, **headers):
        client = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        body = encode(data) if data is not None else None
        client.request(method, path, body=body, headers=headers)
        r = client.getresponse()
        result = r.read()
        client.close()
        return r.status, result

    def headers(self):
        return {'Origin': f'http://127.0.0.1:{self.port}', 'X-CSRF-Token': self.server.csrf, 'Content-Type': 'application/json'}

    def test_security_and_confirmed_save(self):
        code, data = self.req()
        self.assertEqual(code, 200)
        self.assertEqual(loads(data)['csrf'], self.server.csrf)
        for headers in ({}, {'Host':'attacker:8765'}, {'Origin':'https://evil.example'},
                        dict(self.headers(), **{'X-CSRF-Token':'bad'}), dict(self.headers(), **{'X-CSRF-Token':'\xff'})):
            code, _ = self.req('POST', '/api/save', dict(revision=1, profile=PRESET), **headers)
            self.assertEqual(code, 400)
        code, data = self.req('POST', '/api/save', dict(revision=1, profile=BASE), **self.headers())
        self.assertEqual(code, 200)
        self.assertEqual(loads(data)['store']['profiles']['arc'], BASE)
        self.assertEqual(self.req('GET', '/../../etc/passwd')[0], 404)
        self.assertEqual(self.req('POST', '/api/shell', dict(command='id'), **self.headers())[0], 400)
        self.assertEqual(self.req('POST', '/api/save', dict(revision=1, profile=PRESET), **self.headers())[0], 409)
        self.assertEqual(self.req('GET', '/api/export')[0], 200)
        huge=copy.deepcopy(PRESET);huge['dpi']=10**400
        self.assertEqual(self.req('POST','/api/save',dict(revision=2,profile=huge),**self.headers())[0],400)

    def test_long_canonical_binding_returns_400_without_mutation(self):
        profile=copy.deepcopy(PRESET);profile['binds']={'1'*5000:'Cross'}
        with self.assertRaises(Problem):validate(profile)
        before=self.store.snapshot()
        status,_=self.req('POST','/api/save',dict(revision=1,profile=profile),**self.headers())
        self.assertEqual(status,400)
        self.assertEqual(self.store.data,before)

    def test_cross_origin_get_and_large_body(self):
        self.assertEqual(self.req('GET', '/api/bootstrap', **{'Origin':'https://foreign.example'})[0], 400)
        self.assertEqual(self.req('GET', '/api/bootstrap', **{'Sec-Fetch-Site':'cross-site'})[0], 400)
        self.assertEqual(self.req('POST', '/api/save', {'data':'a'*66000}, **self.headers())[0], 400)

    def test_oversized_content_length_identity_is_rejected_before_read(self):
        headers = dict(self.headers(), **{'Content-Length': '9' * 5000})
        self.assertEqual(self.req('POST', '/api/save', {'revision': 1, 'profile': BASE}, **headers)[0], 400)
        self.assertEqual(self.store.data['revision'], 1)


class AdapterSafety(unittest.TestCase):
    def test_rejection_routes_control_to_baseline_despite_foreign_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'native-rejected.json').write_text('{}')
            (Path(tmp) / 'native.sock').touch()
            adapter = Adapters(tmp)
            with patch.object(adapter.baseline, 'status', return_value={'adapter': 'baseline', 'processAlive': True}), \
                 patch.object(adapter.baseline, 'control', return_value={'releaseConfirmed': True}) as baseline, \
                 patch.object(adapter.native, 'status') as native_status, \
                 patch.object(adapter.native, 'control') as native_control:
                self.assertEqual(adapter.status()['adapter'], 'baseline')
                self.assertTrue(adapter.control('release')['releaseConfirmed'])
                baseline.assert_called_once_with('release', cancelled=None)
                native_status.assert_not_called()
                native_control.assert_not_called()

    def test_rejected_candidate_blocks_launch_before_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'native-rejected.json').write_text('{}')
            adapter = Adapters(tmp)
            factory = Mock()
            with patch('dashboard.adapters.subprocess.Popen') as popen:
                with self.assertRaisesRegex(Problem, 'отклонена'):
                    adapter.launch(factory)
                factory.assert_not_called()
                popen.assert_not_called()

    def test_absent_client_can_release_without_keystrokes(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Adapters(tmp)
            with patch.object(adapter.baseline, 'status', return_value={'processAlive':False}), \
                 patch('dashboard.adapters.subprocess.run', return_value=type('Result', (), {'returncode':1})()), \
                 patch.object(adapter.baseline, 'control') as control:
                self.assertEqual(adapter.control('release')['capture'], 'off')
                control.assert_not_called()

    def test_unknown_client_blocks_release_and_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Adapters(tmp)
            with patch.object(adapter.baseline, 'status', return_value={'processAlive':False}), \
                 patch('dashboard.adapters.subprocess.run', return_value=type('Result', (), {'returncode':0})()), \
                 patch('dashboard.adapters.subprocess.Popen') as popen:
                with self.assertRaises(Problem):
                    adapter.control('release')
                factory=Mock()
                with self.assertRaises(Problem):
                    adapter.launch(factory)
                factory.assert_not_called()
                popen.assert_not_called()

    def test_headset_launcher_rejects_legacy_binary_before_spawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);binary=root/'build/client/gui/chiaki.app/Contents/MacOS/chiaki'
            binary.parent.mkdir(parents=True);binary.write_bytes(b'legacy CHIAKI_MKB_NO_AV')
            runtime=root/'runtime';runtime.mkdir();profile=runtime/'profile.json';profile.write_bytes(encode(PRESET))
            adapter=Adapters(runtime);factory=Mock(return_value=profile)
            with patch('dashboard.adapters.ROOT',root), \
                 patch('dashboard.adapters.subprocess.run',return_value=Mock(returncode=1)), \
                 patch('dashboard.adapters.subprocess.Popen') as popen:
                with self.assertRaisesRegex(Problem,'[Уу]становите новую сборку'):
                    adapter.prepare_launch(factory)
                factory.assert_not_called();popen.assert_not_called()
                # A caller-supplied/recovered plan must pass the same guard.
                import hashlib
                plan=dict(binary=str(binary),profilePath=str(profile),
                          binaryDigest=hashlib.sha256(binary.read_bytes()).hexdigest(),
                          profileDigest=hashlib.sha256(profile.read_bytes()).hexdigest())
                with self.assertRaisesRegex(Problem,'[Уу]становите новую сборку'):
                    adapter.launch(factory,plan=plan)
                popen.assert_not_called()

    def test_launch_preserves_opengl_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);binary=root/'build/client/gui/chiaki.app/Contents/MacOS/chiaki'
            binary.parent.mkdir(parents=True);binary.write_bytes(b'fixture CHIAKI_MKB_HEADSET_QUIET')
            runtime=root/'runtime';runtime.mkdir();profile=runtime/'profile.json';profile.write_bytes(encode(PRESET))
            (runtime/'connection.json').write_bytes(encode(dict(console_name='Test console',console_host='192.0.2.10',mouse=dict(vendor_id=0x1234,product_id=0x5678,location_id=0x1000000))))
            adapter=Adapters(runtime)
            binary_digest=__import__('hashlib').sha256(binary.read_bytes()).hexdigest()
            def native_status(**kwargs):
                env=popen.call_args.kwargs['env']
                return dict(connected=True,pid=123,digest=digest(PRESET),launchNonce=env['CHIAKI_MKB_LAUNCH_NONCE'],binaryDigest=binary_digest)
            with patch('dashboard.adapters.ROOT',root), \
                 patch('dashboard.adapters.subprocess.run',return_value=Mock(returncode=1)), \
                 patch('dashboard.adapters.subprocess.Popen',return_value=Mock(pid=123,poll=lambda:None)) as popen, \
                 patch.object(adapter.native,'status',side_effect=native_status), \
                 patch('dashboard.adapters.run',return_value=str(binary)), \
                 patch.dict('os.environ',{'HOME':tmp, 'USER':'fixture', 'TMPDIR':tmp,
                     'CHIAKI_FORCE_VULKAN_FALLBACK':'0', 'CHIAKI_MKB_NO_AV':'1', 'CHIAKI_MKB_HEADSET_QUIET':'0',
                     'CHIAKI_MKB_WORKER':'0', 'CHIAKI_MKB_MOUSE_SOURCE':'cg',
                     'CHIAKI_MKB_PROFILE':'/wrong/profile', 'CHIAKI_MKB_SOCKET':'/wrong/socket',
                     'CHIAKI_MKB_TRACE':'1', 'CHIAKI_MKB_MOUSE_TRACE_DIR':'/wrong/trace',
                     'CHIAKI_MKB_NATIVE_PHASE_TRACE':'1', 'QT_LOGGING_RULES':'*.debug=true',
                     'QT_QPA_PLATFORM':'offscreen', 'QT_PLUGIN_PATH':'/wrong/qt',
                     'DYLD_LIBRARY_PATH':'/wrong/loader', 'DYLD_INSERT_LIBRARIES':'/wrong/inject',
                     'PATH':'/wrong/bin', 'UNLISTED_FUTURE_DIAGNOSTIC':'1'},clear=True):
                adapter.launch(lambda:profile)
            args,kwargs=popen.call_args
            self.assertEqual(args[0],[str(binary),'stream','Test console','192.0.2.10'])
            env=kwargs['env']
            self.assertEqual(env['CHIAKI_FORCE_VULKAN_FALLBACK'],'1')
            self.assertIn('OpenGL',env['CHIAKI_FORCE_VULKAN_FALLBACK_REASON'])
            self.assertEqual(env['CHIAKI_MKB_HEADSET_QUIET'],'1')
            self.assertEqual(env['CHIAKI_MKB_WORKER'],'1')
            self.assertEqual(env['CHIAKI_MKB_MOUSE_SOURCE'],'hid')
            self.assertEqual(env['CHIAKI_MKB_HID_VID'],'0x1234')
            self.assertEqual(env['CHIAKI_MKB_HID_PID'],'0x5678')
            self.assertEqual(env['CHIAKI_MKB_HID_LOCATION'],'0x1000000')
            self.assertEqual(env['CHIAKI_MKB_PROFILE'],str(profile))
            self.assertEqual(env['CHIAKI_MKB_SOCKET'],str(adapter.native.path))
            self.assertEqual(env['PATH'],'/usr/bin:/bin:/usr/sbin:/sbin')
            self.assertEqual(env['HOME'],tmp)
            self.assertEqual(env['TMPDIR'],tmp)
            self.assertEqual(set(env), {'HOME','USER','TMPDIR','PATH','CHIAKI_FORCE_VULKAN_FALLBACK',
                'CHIAKI_FORCE_VULKAN_FALLBACK_REASON','CHIAKI_MKB_HEADSET_QUIET','CHIAKI_MKB_WORKER',
                'CHIAKI_MKB_MOUSE_SOURCE','CHIAKI_MKB_HID_VID','CHIAKI_MKB_HID_PID',
                'CHIAKI_MKB_HID_LOCATION','CHIAKI_MKB_PROFILE','CHIAKI_MKB_SOCKET','CHIAKI_MKB_LAUNCH_NONCE'})




class StartupSafety(unittest.TestCase):
    def test_partial_listener_failure_closes_without_waiting_for_unstarted_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Mock()
            with patch('dashboard.server.Server', side_effect=[first, OSError('Tailnet unavailable')]), \
                 patch('sys.argv', ['dashboard.server', '--state-dir', tmp, '--listen', '100.64.0.2']):
                with self.assertRaises(OSError):
                    run_server()
            first.shutdown.assert_not_called()
            first.server_close.assert_called_once()


class NativeAdapter(unittest.TestCase):
    def test_private_socket_roundtrip_and_expected_identity(self):
        with tempfile.TemporaryDirectory(prefix='arc-ipc-', dir='/tmp') as directory:
            path = Path(directory) / 'native.sock'
            server = socket.socket(socket.AF_UNIX)
            server.bind(str(path))
            server.listen()
            seen = []
            def serve():
                conn, _ = server.accept()
                with conn:
                    data = b''
                    while b'\n' not in data:
                        data += conn.recv(8192)
                    request = loads(data)
                    seen.append(request)
                    conn.sendall(encode(dict(requestId=request['requestId'], ok=True, status={'digest':digest(PRESET)}))+b'\n')
            thread = threading.Thread(target=serve)
            thread.start()
            result = Native(path).apply(PRESET, dict(processId='p', sessionId='s', digest='old'))
            thread.join()
            server.close()
            self.assertEqual(seen[0]['expectedDigest'], 'old')
            self.assertEqual(loads(seen[0]['profileBytes']), PRESET)
            self.assertEqual(result['digest'], digest(PRESET))

    def test_live_serializes_nonzero_runtime_revision(self):
        with tempfile.TemporaryDirectory(prefix='arc-live-ipc-', dir='/tmp') as directory:
            path = Path(directory) / 'native.sock'
            server = socket.socket(socket.AF_UNIX)
            server.bind(str(path)); server.listen()
            seen = []
            def serve():
                conn, _ = server.accept()
                with conn:
                    raw = b''
                    while b'\n' not in raw: raw += conn.recv(8192)
                    request = loads(raw); seen.append(request)
                    conn.sendall(encode(dict(requestId=request['requestId'], ok=True,
                        status=dict(digest=digest(PRESET), capture='active')))+b'\n')
            thread = threading.Thread(target=serve); thread.start()
            try:
                result = Adapters(directory).live(PRESET, dict(processId='p', sessionId='s',
                    digest='before', captureGeneration=9, sessionGeneration=3, controlEpoch=5,
                    recoil=dict(runtimeRevision=17)), 'live-operation')
            finally:
                thread.join(timeout=3); server.close()
            self.assertFalse(thread.is_alive())
            self.assertEqual(seen[0]['command'], 'live')
            self.assertEqual(seen[0]['runtimeRevision'], 17)
            self.assertEqual(seen[0]['operationId'], 'live-operation')
            self.assertEqual(seen[0]['captureGeneration'], 9)
            self.assertEqual(loads(seen[0]['profileBytes']), PRESET)
            self.assertEqual(result['digest'], digest(PRESET))
            self.assertEqual(result['capture'], 'active')

    def test_missing_socket_and_arbitrary_command(self):
        with self.assertRaises(Problem):
            Native('/tmp/absent-arc-native.sock').status()
        with self.assertRaises(Problem):
            Baseline().control('shell')


if __name__ == '__main__':
    unittest.main(verbosity=2)
