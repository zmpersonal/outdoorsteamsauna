from pathlib import Path
import os,json,re,math,time
from datetime import date,timedelta
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/cities.json'
S=requests.Session();S.headers.update({'User-Agent':'OutdoorSteamSauna.com data updater / public-data research'})

def clamp(x,a,b): return max(a,min(b,x))
def num(v):
 try:return float(str(v).strip())
 except:return None

def month_num(rec):
 for k in ('MONTH','month'):
  if k in rec:
   try:return int(rec[k])
   except:pass
 s=str(rec.get('DATE',''))
 m=re.search(r'(?:^|[-/])(0?[1-9]|1[0-2])(?:$|[-/])',s)
 if m:return int(m.group(1))
 if re.fullmatch(r'0?[1-9]|1[0-2]',s):return int(s)
 return None

def fetch_eia(api_key):
 url='https://api.eia.gov/v2/electricity/retail-sales/data/'
 params=[('api_key',api_key),('frequency','monthly'),('data[]','price'),('facets[sectorid][]','RES'),('sort[0][column]','period'),('sort[0][direction]','desc'),('offset','0'),('length','5000')]
 r=S.get(url,params=params,timeout=45);r.raise_for_status(); rows=r.json()['response']['data']
 out={}
 for x in rows:
  st=x.get('stateid'); p=num(x.get('price'))
  if st and len(st)==2 and p is not None and st not in out: out[st]={'price':p,'period':x.get('period')}
 if len(out)<45: raise RuntimeError(f'EIA returned only {len(out)} states/DC')
 return out

def choose_station(rows,lat,lon):
 groups={}
 for r in rows: groups.setdefault(r.get('STATION','unknown'),[]).append(r)
 best=None;bestscore=-1
 for st,rs in groups.items():
  months={month_num(x) for x in rs if month_num(x)}
  vals=sum(1 for x in rs if num(x.get('MLY-TMIN-NORMAL')) is not None or num(x.get('MLY-TAVG-NORMAL')) is not None)
  score=len(months)*10+vals
  if score>bestscore:best=(st,rs);bestscore=score
 return best

def fetch_city_normals(c):
 base='https://www.ncei.noaa.gov/access/services/data/v1'
 for span in (0.35,0.75,1.3):
  bbox=f"{c['lat']+span},{c['lon']-span},{c['lat']-span},{c['lon']+span}"
  params={'dataset':'normals-monthly-1991-2020','dataTypes':'MLY-TAVG-NORMAL,MLY-TMIN-NORMAL,MLY-TMAX-NORMAL,MLY-PRCP-NORMAL,MLY-SNOW-NORMAL','bbox':bbox,'format':'json','units':'standard','includeStationName':'true','includeStationLocation':'true'}
  r=S.get(base,params=params,timeout=40)
  if r.status_code!=200: continue
  try:rows=r.json()
  except:continue
  if not rows:continue
  chosen=choose_station(rows,c['lat'],c['lon'])
  if not chosen:continue
  st,rs=chosen; by={}
  for x in rs:
   m=month_num(x)
   if not m:continue
   d=by.setdefault(m,{})
   for key,outkey in [('MLY-TAVG-NORMAL','tavg_f'),('MLY-TMIN-NORMAL','tmin_f'),('MLY-TMAX-NORMAL','tmax_f'),('MLY-PRCP-NORMAL','prcp_in'),('MLY-SNOW-NORMAL','snow_in')]:
    v=num(x.get(key));
    if v is not None:d[outkey]=v
  if len(by)>=10:
   months=[]
   for m in range(1,13):
    d=by.get(m,{})
    if 'tavg_f' not in d and 'tmin_f' in d and 'tmax_f' in d:d['tavg_f']=(d['tmin_f']+d['tmax_f'])/2
    if not all(k in d for k in ('tavg_f','tmin_f','tmax_f')): return None
    months.append({'month':m,'tmin_f':round(d['tmin_f'],1),'tmax_f':round(d['tmax_f'],1),'tavg_f':round(d['tavg_f'],1),'prcp_in':round(d.get('prcp_in',0),2),'snow_in':round(d.get('snow_in',0),1)})
   name=next((x.get('NAME') or x.get('STATION_NAME') for x in rs if x.get('NAME') or x.get('STATION_NAME')),st)
   return {'station':st,'station_name':name,'monthly':months}
 return None

def fetch_recent(station,monthly):
 if not station:return None
 end=date.today()-timedelta(days=2); start=end-timedelta(days=32)
 params={'dataset':'daily-summaries','stations':station,'startDate':start.isoformat(),'endDate':end.isoformat(),'dataTypes':'TAVG,TMIN,TMAX','format':'json','units':'standard'}
 try:
  r=S.get('https://www.ncei.noaa.gov/access/services/data/v1',params=params,timeout=35);r.raise_for_status();rows=r.json()
  vals=[]
  for x in rows:
   v=num(x.get('TAVG'))
   if v is None:
    lo=num(x.get('TMIN'));hi=num(x.get('TMAX'));v=(lo+hi)/2 if lo is not None and hi is not None else None
   if v is not None:vals.append(v)
  if len(vals)<10:return None
  avg=sum(vals)/len(vals); normal=monthly[end.month-1]['tavg_f']
  return {'avg_f':round(avg,1),'delta_f':round(avg-normal,1),'days':len(vals),'through':end.isoformat()}
 except:return None

def metrics(monthly,rate):
 jan=monthly[0]['tmin_f'];annual_prcp=sum(x.get('prcp_in') or 0 for x in monthly);annual_snow=sum(x.get('snow_in') or 0 for x in monthly);freeze_months=sum(1 for x in monthly if x['tmin_f']<32)
 cold=clamp((45-jan)/55,0,1);freeze=clamp(freeze_months/7,0,1);wet=clamp(annual_prcp/70,0,1);energy=clamp((rate-10)/25,0,1)
 score=round(100*(.48*cold+.22*freeze+.10*wet+.20*energy),1);warm=.95+.45*cold;kwh=9*(warm+.60);cost=round(kwh*rate/100,2)
 label='Severe planning load' if score>=75 else 'High planning load' if score>=55 else 'Moderate planning load' if score>=35 else 'Mild planning load'
 return {'score':score,'label':label,'jan_tmin_f':round(jan,1),'annual_prcp_in':round(annual_prcp,1),'annual_snow_in':round(annual_snow,1),'freeze_months':freeze_months,'rate_cents':round(rate,2),'session_cost_9kw':cost,'annual_cost_3x_week':int(round(cost*3*52))}

def main():
 key=os.environ.get('EIA_API_KEY','').strip()
 if not key:raise RuntimeError('EIA_API_KEY is not set. Add it under Settings > Secrets and variables > Actions.')
 obj=json.load(open(DATA));cities=obj['cities']
 eia=fetch_eia(key); print(f'EIA OK: {len(eia)} states/DC')
 live=0
 def one(c):
  n=fetch_city_normals(c)
  if n:
   return c['slug'],n,fetch_recent(n['station'],n['monthly'])
  return c['slug'],None,None
 with ThreadPoolExecutor(max_workers=5) as ex:
  fs=[ex.submit(one,c) for c in cities]
  results={}
  for f in as_completed(fs):
   slug,n,recent=f.result();results[slug]=(n,recent);print(slug,'NOAA OK' if n else 'NOAA preserved')
 for c in cities:
  n,recent=results.get(c['slug'],(None,None))
  if n:
   c['monthly']=n['monthly'];c['station']=f"{n['station']} — {n['station_name']}";c['climate_source']='NOAA/NCEI 1991–2020 Monthly Climate Normals';c['recent']=recent;live+=1
  e=eia.get(c['state'])
  if e:c['electricity_source']=f"EIA residential retail electricity price ({e['period']})";rate=e['price']
  else:rate=c['metrics']['rate_cents']
  c['metrics']=metrics(c['monthly'],rate)
 cities.sort(key=lambda x:x['metrics']['score'],reverse=True)
 for i,c in enumerate(cities,1):c['rank']=i
 obj['cities']=cities;obj['meta']={'generated':date.today().isoformat(),'live_noaa_cities':live,'live_eia_states':len(eia),'starter':live<max(10,len(cities)//2),'normal_period':'1991–2020','notes':'NOAA climate normals + latest EIA residential electricity rates; city records preserve prior values when a station query fails.'}
 json.dump(obj,open(DATA,'w'),indent=2)
 print(f'NOAA live: {live}/{len(cities)}')
 if live<10:raise RuntimeError('Too few NOAA city updates succeeded; refusing to mark the climate index as refreshed.')
 import subprocess,sys;subprocess.check_call([sys.executable,str(ROOT/'scripts/build_site.py')])
if __name__=='__main__':main()
