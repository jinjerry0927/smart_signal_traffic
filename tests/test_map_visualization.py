"""Read-only vehicle data, actual map geometry and browser math regressions."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock
from urllib.request import urlopen
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from signal_demo.visualization import VehicleFrames, VARIABLES
from signal_demo.runtime import Demo
from signal_demo.web import make_server
import traci.constants as tc


class MapVisualizationTests(unittest.TestCase):
    def test_vehicle_subscriptions_supply_real_dimensions_class_and_lane_without_control(self):
        domain = Mock()
        domain.getIDList.return_value = ['bus']
        domain.getAllSubscriptionResults.return_value = {'bus': dict(zip(VARIABLES,
            [(10.,20.),90.,0.,12.,2.5,'bus','in_0',19.]))}
        connection = Mock(vehicle=domain)
        frames = VehicleFrames()
        first = frames.snapshot(connection)
        second = frames.snapshot(connection)
        self.assertEqual(first,second)
        self.assertEqual(first[0],dict(id='bus',x=10.,y=20.,angle=90.,speed=0.,length=12.,width=2.5,
                                      vehicle_class='bus',lane_id='in_0',lane_position=19.))
        domain.subscribe.assert_called_once_with('bus',VARIABLES)
        self.assertEqual({call[0] for call in domain.mock_calls},
                         {'getIDList','subscribe','getAllSubscriptionResults'})
        domain.getIDList.return_value=[]
        self.assertEqual(frames.snapshot(connection),[])
        domain.getIDList.return_value=['bus']
        frames.snapshot(connection)
        self.assertEqual(domain.subscribe.call_count,2)

    def test_geometry_keeps_internal_lanes_actual_junction_polygons_and_movement_indices(self):
        demo=Demo()
        data=demo.boot()['map']
        self.assertEqual(data['coordinate_reference']['position'],'front_bumper_center')
        self.assertTrue(any(l['internal'] for l in data['lanes'].values()))
        self.assertTrue(all(l['width']>0 and len(l['shape'])>=2 for l in data['lanes'].values()))
        self.assertGreaterEqual(len(data['road_junctions']),len(data['junctions']))
        for signal in data['signals'].values():
            for movement in signal['movements']:
                self.assertTrue(any(c['index']==movement['index'] and c['incoming']==movement['incoming']
                                    and c['outgoing']==movement['outgoing'] for c in data['connections']))
        demo.close()

    def test_both_pages_load_the_same_local_renderer_and_asset_has_javascript_type(self):
        server=make_server(Demo(),port=0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with urlopen('http://127.0.0.1:'+str(server.server_port)+'/traffic-map.js') as response:
                self.assertIn('text/javascript',response.headers['Content-Type'])
                self.assertIn('class TrafficMap',response.read().decode('utf-8'))
            for name in ('dashboard.html','unseen_dashboard.html'):
                text=(ROOT/'signal_demo'/name).read_text(encoding='utf-8')
                self.assertIn('src="/traffic-map.js"',text)
                self.assertIn('SignalTrafficMap.TrafficMap',text)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_javascript_geometry_and_animation_invariants(self):
        result=subprocess.run(['node','--test',str(ROOT/'tests/traffic_map.test.cjs')],
                              capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':
    unittest.main()
