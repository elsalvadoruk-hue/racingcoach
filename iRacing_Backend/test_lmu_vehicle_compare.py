from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    import duckdb
except ImportError:
    duckdb = None
import numpy as np

from lmu_corners import compare_corners
from lmu_telemetry import LmuTelemetryReader
from lmu_vehicle import read_vehicle_native
from lmu_vehicle_compare import compare_vehicle_laps
from test_lmu_telemetry import make_comparison_file


@unittest.skipUnless(duckdb, 'LMU 전용 의존성이 없는 환경입니다.')
class VehicleCompareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'native.duckdb'
        make_comparison_file(self.path)
        self.sql("INSERT INTO metadata VALUES ('CarClass','GT3')")
        self.add_channel('TyresPressure',7,'kPa',[f'i/7.*10+{100+j*100}' for j in range(4)])
        self.add_channel('FrontRideHeight',13,'m',['i/13.*.001+.01'])
        self.add_channel('Wheel Speed',11,'m/s',[f'i/11.+{j}' for j in range(4)])
        self.reader = LmuTelemetryReader(self.path)

    def sql(self, query, params=None):
        with duckdb.connect(str(self.path)) as connection:
            connection.execute(query, params)

    def add_channel(self,name,hz,unit,formulae):
        with duckdb.connect(str(self.path)) as connection:
            connection.execute('INSERT INTO channelsList VALUES (?,?,?)',[name,hz,unit])
            fields=['value'] if len(formulae)==1 else ['value1','value2','value3','value4']
            expressions=','.join(f'CAST(({formula}) AS DOUBLE) AS {field}' for formula,field in zip(formulae,fields))
            connection.execute(f'CREATE TABLE "{name}" AS SELECT {expressions} FROM range({30*hz}) r(i)')

    def compare(self,name='TyresPressure',points=301):
        return compare_vehicle_laps(self.reader,1,self.reader,2,name,points)

    def add_geometry(self):
        hz=10
        stamps=100.0275+np.arange(30*hz)/hz
        distance=np.where(stamps<110,(stamps-100.0275)*100,np.where(stamps<120,(stamps-110)*100,(stamps-120)*1000/9))
        radius=200/(np.pi/2)
        angle=np.clip((distance-400)/radius,0,np.pi/2)
        x=np.where(distance<400,distance,400+radius*np.sin(angle))
        y=radius*(1-np.cos(angle))+np.maximum(distance-600,0)
        with duckdb.connect(str(self.path)) as connection:
            for name,values in (('GPS Latitude',45+y/6371000*180/np.pi),
                                ('GPS Longitude',9+x/(6371000*np.cos(np.pi/4))*180/np.pi)):
                connection.execute('INSERT INTO channelsList VALUES (?,?,?)',[name,hz,'deg'])
                connection.execute(f'CREATE TABLE "{name}"(value DOUBLE)')
                connection.executemany(f'INSERT INTO "{name}" VALUES (?)',[(float(v),) for v in values])

    def test_mixed_native_clocks_analytic_means_and_delta_on_common_distance(self):
        report=self.compare(points=3)
        self.assertEqual(report['status'],'PARTIAL')  # Low-rate native last sample precedes distance end.
        self.assertEqual(report['conditions'],'NOT_COMPARED')
        self.assertEqual(report['reference_status'],'VALIDITY_UNVERIFIED')
        self.assertEqual(report['channel']['wheel_order'],'UNVERIFIED')
        self.assertEqual(report['corner_comparison']['status'],'UNAVAILABLE')
        for i,component in enumerate(report['channel']['components']):
            start,end=component['start_m'],component['end_m']
            observed_start, observed_end = component['support_ranges'][0]['start_m'], component['support_ranges'][-1]['end_m']
            self.assertLess(component['supported_distance_m'],end-start)
            self.assertAlmostEqual(component['reference']['minimum'],199.725+i*100+.1*observed_start,places=8)
            self.assertAlmostEqual(component['reference']['maximum'],199.725+i*100+.1*observed_end,places=8)
            self.assertAlmostEqual(component['reference']['mean'],199.725+i*100+.1*(observed_start+observed_end)/2,places=8)
            self.assertAlmostEqual(component['target']['mean'],299.725+i*100+.09*(observed_start+observed_end)/2,places=8)
            self.assertAlmostEqual(component['delta']['mean'],100-.01*(observed_start+observed_end)/2,places=8)
            self.assertLessEqual(len(component['points']),3)

    def test_native_peak_area_and_extremes_are_independent_of_render_budget(self):
        self.sql('UPDATE TyresPressure SET value1=900 WHERE rowid=105')
        with patch('lmu_vehicle.vehicle_report',side_effect=AssertionError('render data must not be used')):
            sparse=self.compare(points=3)
            dense=self.compare(points=501)
        for a,b in zip(sparse['channel']['components'],dense['channel']['components']):
            self.assertEqual({k:v for k,v in a.items() if k!='points'}, {k:v for k,v in b.items() if k!='points'})
        component=sparse['channel']['components'][0]
        start,end=component['start_m'],component['end_m']
        a,b=component['support_ranges'][0]['start_m'],component['support_ranges'][-1]['end_m']
        expected=199.725+.1*(a+b)/2+(900-250)*100/7/component['supported_distance_m']
        self.assertAlmostEqual(component['reference']['mean'],expected,places=8)
        self.assertEqual(component['reference']['maximum'],900)
        self.assertIn(900,[point['reference'] for point in component['points']])

    def test_native_holes_exclude_same_intervals_from_both_averages_and_graph(self):
        self.sql('UPDATE TyresPressure SET value1=NULL WHERE rowid BETWEEN 100 AND 102')
        report=self.compare(points=501)
        first,second=report['channel']['components'][:2]
        self.assertEqual(first['status'],'PARTIAL')
        self.assertEqual(second['status'],'PARTIAL')
        self.assertAlmostEqual(second['supported_distance_m']-first['supported_distance_m'],4*100/7,places=7)
        self.assertEqual(len(first['support_ranges']),2)
        self.assertGreater(len({p['segment'] for p in first['points']}),1)
        weighted_mid=sum((s['end_m']**2-s['start_m']**2)/2 for s in first['support_ranges'])/first['supported_distance_m']
        self.assertAlmostEqual(first['delta']['mean'],100-.01*weighted_mid,places=8)
        self.assertAlmostEqual(first['target']['mean']-first['reference']['mean'],first['delta']['mean'],places=8)

    def test_scalar_and_vector_conversion_and_reverse_direction(self):
        scalar=self.compare('FrontRideHeight')
        self.assertEqual(scalar['channel']['unit'],'mm')
        self.assertEqual(scalar['channel']['wheel_order'],'NOT_APPLICABLE')
        component=scalar['channel']['components'][0]
        start,end=component['start_m'],component['end_m']
        self.assertAlmostEqual(component['reference']['mean'],19.9725+.00001*1000*(start+end)/2,places=8)
        speed=self.compare('Wheel Speed')
        self.assertEqual(speed['channel']['unit'],'km/h')
        forward=self.compare()
        reverse=compare_vehicle_laps(self.reader,2,self.reader,1,points=3)
        for a,b in zip(forward['channel']['components'],reverse['channel']['components']):
            self.assertAlmostEqual(a['delta']['mean'],-b['delta']['mean'],places=9)
            self.assertAlmostEqual(a['delta']['minimum'],-b['delta']['maximum'],places=9)

    def test_frozen_geometry_boundaries_and_exact_corner_statistics(self):
        self.add_geometry()
        report=self.compare(points=3)
        _,native_a=read_vehicle_native(self.reader,1)
        _,native_b=read_vehicle_native(self.reader,2)
        definition=compare_corners(native_a['lap'],native_b['lap'])['definition']
        self.assertEqual(report['corner_comparison']['definition'],definition)
        self.assertGreater(len(report['corner_comparison']['corners']),0)
        for row,bound in zip(report['corner_comparison']['corners'],definition['boundaries']):
            self.assertEqual((row['start_m'],row['end_m']),(bound['start_m'],bound['end_m']))
            component=row['components'][0]
            self.assertAlmostEqual(component['delta']['mean'],100-.01*(row['start_m']+row['end_m'])/2,places=8)
            self.assertNotIn('points',component)

    def test_metadata_unit_shape_and_memory_limits_fail_without_private_metadata(self):
        self.sql("INSERT INTO metadata VALUES ('SteamID','PRIVATE_STEAM'),('CarSetup','PRIVATE_SETUP')")
        import json
        report=self.compare()
        serialized=json.dumps(report,allow_nan=False)
        self.assertNotIn('PRIVATE',serialized)
        self.assertNotIn(str(self.path),serialized)
        other=Path(self.temp.name)/'other.duckdb'
        other.write_bytes(self.path.read_bytes())
        with duckdb.connect(str(other)) as connection:
            connection.execute("UPDATE metadata SET value='Other' WHERE key='CarName'")
        with self.assertRaises(ValueError):
            compare_vehicle_laps(self.reader,1,LmuTelemetryReader(other),2)
        self.sql("UPDATE channelsList SET unit='unknown' WHERE channelName='TyresPressure'")
        self.assertEqual(self.compare()['status'],'UNAVAILABLE')
        self.sql("UPDATE channelsList SET unit='kPa' WHERE channelName='TyresPressure'")
        with patch('lmu_vehicle_compare.MAX_KNOTS',10):
            self.assertEqual(self.compare()['status'],'UNAVAILABLE')
        self.sql('ALTER TABLE TyresPressure DROP COLUMN value4')
        self.assertEqual(self.compare()['status'],'UNAVAILABLE')

    def test_validation_and_row_order_do_not_silently_repair_observations(self):
        for points in (2,502,True):
            with self.assertRaises(ValueError):
                self.compare(points=points)
        self.sql('DELETE FROM TyresPressure WHERE rowid=90')
        self.assertEqual(self.compare()['status'],'UNAVAILABLE')

    def test_time_and_distance_rowid_gaps_are_rejected_before_summary_compression(self):
        for name in ('GPS Time', 'Lap Dist'):
            with self.subTest(name=name):
                original=Path(self.temp.name)/(name.replace(' ','_')+'.duckdb')
                original.write_bytes(self.path.read_bytes())
                with duckdb.connect(str(original)) as connection:
                    connection.execute(f'DELETE FROM "{name}" WHERE rowid=150')
                reader=LmuTelemetryReader(original)
                with patch.object(reader,'_summary',side_effect=AssertionError('must not compress row gaps')):
                    result=compare_vehicle_laps(reader,1,self.reader,2)
                self.assertEqual(result['status'],'UNAVAILABLE')
                self.assertIn('원본 행 순서',result['reason'])

    def test_nonuniform_or_missing_gps_clock_prevents_vehicle_comparison(self):
        for expression in ('value+.1', 'NULL'):
            with self.subTest(expression=expression):
                other=Path(self.temp.name)/('gps_'+str(len(expression))+'.duckdb')
                other.write_bytes(self.path.read_bytes())
                with duckdb.connect(str(other)) as connection:
                    connection.execute(f'UPDATE "GPS Time" SET value={expression} WHERE rowid=1500')
                result=compare_vehicle_laps(LmuTelemetryReader(other),1,self.reader,2)
                self.assertEqual(result['status'],'UNAVAILABLE')
                self.assertIn('GPS Time',result['reason'])

    def test_coordinate_rowid_gap_or_invalid_value_only_removes_geometry(self):
        self.add_geometry()
        for name,query in [('GPS Latitude','DELETE FROM "GPS Latitude" WHERE rowid=150'),
                           ('GPS Longitude','DELETE FROM "GPS Longitude" WHERE rowid=150'),
                           ('GPS Longitude','UPDATE "GPS Longitude" SET value=NULL WHERE rowid=150')]:
            with self.subTest(name=name,query=query):
                other=Path(self.temp.name)/('coordinate_'+str(len(query))+'.duckdb')
                other.write_bytes(self.path.read_bytes())
                with duckdb.connect(str(other)) as connection:
                    connection.execute(query)
                result=compare_vehicle_laps(LmuTelemetryReader(other),1,self.reader,2,'FrontRideHeight')
                self.assertEqual(result['status'],'AVAILABLE')
                self.assertEqual(result['corner_comparison']['status'],'UNAVAILABLE')

    def test_native_clock_gap_smaller_than_distance_sampling_is_never_bridged(self):
        from lmu_vehicle import read_vehicle_native as read_native
        self.add_channel('Engine Oil Temp',200,'C',['i/200.+80'])
        original_report,original_native=read_native(self.reader,1,'Engine Oil Temp')
        target_report,target_native=read_native(self.reader,2,'Engine Oil Temp')
        native=dict(original_native)
        index=1000  # 500m area, much shorter than a 10m distance sampling cell.
        native['time_s']=np.delete(native['time_s'],index)
        native['values']=np.delete(native['values'],index,axis=0)
        with patch('lmu_vehicle_compare.read_vehicle_native',side_effect=[(original_report,native),(target_report,target_native)]):
            result=self.compare('Engine Oil Temp',501)
        first=result['channel']['components'][0]
        self.assertEqual(first['status'],'PARTIAL')
        self.assertEqual(first['support_range_count'],2)
        gap_start=original_native['lap'].channels['distance_m'].at(original_native['time_s'][index-1])
        gap_end=original_native['lap'].channels['distance_m'].at(original_native['time_s'][index+1])
        self.assertAlmostEqual(first['support_ranges'][0]['end_m'],gap_start)
        self.assertAlmostEqual(first['support_ranges'][1]['start_m'],gap_end)
        self.assertGreater(len({point['segment'] for point in first['points']}),1)

    def test_session_clock_offset_and_supported_unit_conversion_keep_statistics(self):
        baseline=self.compare('FrontRideHeight')
        other=Path(self.temp.name)/'offset.duckdb'
        other.write_bytes(self.path.read_bytes())
        with duckdb.connect(str(other)) as connection:
            connection.execute('UPDATE "GPS Time" SET value=value+1000')
            for name in ('Lap','Lap Time','In Pits'):
                connection.execute(f'UPDATE "{name}" SET ts=ts+1000')
            connection.execute('UPDATE FrontRideHeight SET value=value*1000')
            connection.execute("UPDATE channelsList SET unit='mm' WHERE channelName='FrontRideHeight'")
        shifted=compare_vehicle_laps(self.reader,1,LmuTelemetryReader(other),2,'FrontRideHeight')
        a,b=baseline['channel']['components'][0],shifted['channel']['components'][0]
        for side in ('reference','target','delta'):
            for metric in ('minimum','maximum','mean'):
                self.assertAlmostEqual(a[side][metric],b[side][metric],places=8)


if __name__ == '__main__':
    unittest.main()
