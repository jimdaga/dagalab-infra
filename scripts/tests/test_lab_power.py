import importlib.util, json, os, pathlib, tempfile, unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('lab_power', HERE.parent / 'lab-power.py')
lp = importlib.util.module_from_spec(spec); spec.loader.exec_module(lp)


def wl(kind, ns, name, vols=None, vct=None, replicas=1):
    spec = {'replicas': replicas, 'template': {'spec': {'volumes': vols or []}}}
    if vct: spec['volumeClaimTemplates'] = vct
    return {'kind': kind, 'metadata': {'namespace': ns, 'name': name}, 'spec': spec}


class LabPowerTest(unittest.TestCase):
    def test_kubectl_cmd_local_uses_rke2(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LAB_POWER_KUBECTL', None)
            self.assertEqual(lp.kubectl_cmd(True), ['/var/lib/rancher/rke2/bin/kubectl', '--kubeconfig', '/etc/rancher/rke2/rke2.yaml', '--request-timeout=20s'])
            self.assertEqual(lp.kubectl_cmd(False), ['kubectl'])
        with mock.patch.dict(os.environ, {'LAB_POWER_KUBECTL': 'fake kube'}):
            self.assertEqual(lp.kubectl_cmd(True), ['fake', 'kube'])

    def test_volume_workloads_only_pvc_users_outside_system(self):
        items = [wl('Deployment', 'media', 'sonarr', [{'persistentVolumeClaim': {'claimName': 'x'}}]),
                 wl('Deployment', 'media', 'decluttarr', [{'configMap': {'name': 'c'}}]),
                 wl('StatefulSet', 'vault', 'vault', vct=[{}], replicas=3),
                 wl('Deployment', 'longhorn-system', 'ui', [{'persistentVolumeClaim': {'claimName': 'y'}}])]
        with mock.patch.object(lp, 'kj', return_value={'items': items}):
            got = [(w['ns'], w['name'], w['replicas']) for w in lp.volume_workloads()]
        self.assertEqual(got, [('media', 'sonarr', 1), ('vault', 'vault', 3)])

    def test_nfs_claim_pods(self):
        pvcs = {'items': [{'metadata': {'namespace': 'media', 'name': 'media-data'}, 'spec': {'volumeName': 'media-data'}},
                          {'metadata': {'namespace': 'media', 'name': 'sonarr'}, 'spec': {'volumeName': 'pvc-1'}}]}
        pvs = {'items': [{'metadata': {'name': 'media-data'}, 'spec': {'nfs': {'server': '192.168.3.21'}}},
                         {'metadata': {'name': 'pvc-1'}, 'spec': {'csi': {'driver': 'driver.longhorn.io'}}}]}
        pods = {'items': [{'metadata': {'namespace': 'media', 'name': 'sonarr-abc'},
                           'spec': {'volumes': [{'persistentVolumeClaim': {'claimName': 'media-data'}}]}},
                          {'metadata': {'namespace': 'media', 'name': 'decluttarr-x'}, 'spec': {'volumes': []}}]}
        def fake(*args):
            return {'pvc': pvcs, 'pv': pvs, 'pods': pods}[args[1]]
        with mock.patch.object(lp, 'kj', side_effect=fake):
            self.assertEqual(lp.nfs_claim_pods(), ['media/sonarr-abc'])

    def test_wait_stopped(self):
        with mock.patch.object(lp, 'volumes', return_value=[('v1', 'detached', 'unknown')]):
            self.assertTrue(lp.wait_stopped(1, every=0.01))
        with mock.patch.object(lp, 'volumes', return_value=[('v1', 'attached', 'healthy')]):
            self.assertFalse(lp.wait_stopped(0.05, every=0.01))
        with mock.patch.object(lp, 'volumes', side_effect=SystemExit('api down')):
            self.assertFalse(lp.wait_stopped(0.05, every=0.01))

    def test_stop_workloads_local_dry_run_never_uses_ssh_or_writes_state(self):
        with tempfile.TemporaryDirectory() as d:
            lp.DRY, lp.LOCAL, lp.STATE = True, True, pathlib.Path(d) / 's.json'
            try:
                with mock.patch.object(lp, 'ssh', side_effect=AssertionError('ssh used')), \
                     mock.patch.object(lp, 'volume_workloads', return_value=[{'kind': 'deployment', 'ns': 'media', 'name': 'a', 'replicas': 1}]), \
                     mock.patch.object(lp, 'volumes', return_value=[]):
                    lp.stop_workloads()
                self.assertFalse(lp.STATE.exists())
            finally:
                lp.DRY, lp.LOCAL = False, False

    def test_marker_written_before_argo_is_paused(self):
        """I1: if pausing fails, the marker must already exist so the auto-start un-pauses Argo after the reboot."""
        with tempfile.TemporaryDirectory() as d:
            lp.DRY, lp.LOCAL, lp.STATE = False, True, pathlib.Path(d) / 's.json'
            try:
                with mock.patch.object(lp, 'sh', return_value=''), mock.patch.object(lp, 'act', side_effect=SystemExit('api hiccup')), \
                     mock.patch.object(lp, 'volume_workloads', return_value=[{'kind': 'deployment', 'ns': 'media', 'name': 'a', 'replicas': 1}]):
                    with self.assertRaises(SystemExit):
                        lp.stop_workloads()
                self.assertTrue(lp.STATE.exists(), 'marker missing although Argo may already be paused')
                self.assertEqual(json.loads(lp.STATE.read_text())['workloads'][0]['name'], 'a')
            finally:
                lp.DRY, lp.LOCAL = False, False

    def test_ups_units_are_time_bounded(self):
        """I2/I3: every node-side wait is bounded so a hung API can't keep a node up or kill the auto-start midway."""
        root = HERE.parent.parent / 'hosts/nut'
        sh_ = (root / 'ups-shutdown.sh').read_text()
        for call in ('stop-workloads --local --state', 'wait-stopped --local --timeout'):
            line = next(l for l in sh_.splitlines() if call in l and 'dry-run' not in l and 'echo' not in l)
            self.assertIn('timeout ', line.split('python3')[0], line)
        # whole lines: systemd has no inline comments, 'X=1 # note' is rejected and the limit silently dropped
        self.assertIn('TimeoutStartSec=15min', (root / 'dagalab-ups-shutdown.service').read_text().splitlines())
        self.assertIn('TimeoutStartSec=7200', (root / 'dagalab-ups-autostart.service').read_text().splitlines())


if __name__ == '__main__':
    unittest.main()
