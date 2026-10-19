from datetime import date, timedelta
import unittest
from collector.weather import ensemble_days, rainfall_summary, quantile

class WeatherTests(unittest.TestCase):
    def test_ensemble_includes_unsuffixed_control_and_member_zero_rain(self):
        hourly={'time':[f'2026-10-19T{i:02}:00' for i in range(24)],
            'temperature_2m':[15]*24,'temperature_2m_member01':[25]*24,
            'precipitation':[0]*24,'precipitation_member01':[1]*24}
        day=ensemble_days({'hourly':hourly})[0]
        self.assertEqual(day['members'],2)
        self.assertEqual(day['median'],20)
        self.assertEqual(day['rain_probability'],50)
        self.assertEqual(day['agreement'],'Low')

    def test_null_and_partial_members_are_not_counted_as_dry(self):
        hourly={'time':[f'2026-10-19T{i:02}:00' for i in range(24)],'temperature_2m':[None]*24,'precipitation':[None]*24}
        result=ensemble_days({'hourly':hourly})[0]
        self.assertIsNone(result['rain_probability'])
        self.assertIsNone(result['median'])
        self.assertIsNone(result['agreement'])
        hourly['time']=hourly['time'][:12]
        hourly['temperature_2m']=[10]*12
        hourly['precipitation']=[0]*12
        self.assertEqual(ensemble_days({'hourly':hourly})[0]['members'],0)

    def test_missing_rain_day_makes_total_unavailable(self):
        today=date(2026,10,19)
        series={(today-timedelta(days=i)).isoformat():1 for i in range(1,15)}
        result=rainfall_summary(series,today,'test')
        self.assertEqual(result['complete_days'],14)
        self.assertIsNone(result['total'])
        self.assertEqual(result['days'][-1]['date'],'2026-10-18')

    def test_baseline_and_anomaly_cross_year(self):
        today=date(2026,1,5)
        series={}
        for year in range(1991,2021):
            for i in range(15): series[(date(year,1,4)-timedelta(days=i)).isoformat()]=2
        for i in range(1,16): series[(today-timedelta(days=i)).isoformat()]=1
        result=rainfall_summary(series,today,'test')
        self.assertEqual(result['total'],15)
        self.assertEqual(result['normal'],30)
        self.assertEqual(result['anomaly_percent'],-50)
        self.assertEqual(result['normal_years'],30)

    def test_sparse_baseline_not_presented_as_climate_normal(self):
        series={}
        for i in range(15): series[(date(2020,10,18)-timedelta(days=i)).isoformat()]=1
        result=rainfall_summary(series,date(2026,10,19),'test')
        self.assertIsNone(result['normal'])

    def test_quantile_filters_nonfinite(self):
        self.assertEqual(quantile([1,3,None,float('nan')],.5),2)

if __name__=='__main__': unittest.main()
