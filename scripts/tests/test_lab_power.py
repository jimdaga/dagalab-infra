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
            self.assertEqual(lp.kubectl_cmd(True), ['/var/lib/rancher/rke2/bin/kubectl', '--kubeconfig', '/etc/rancher/rke2/rke2.yaml'])
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


if __name__ == '__main__':
    unittest.main()
