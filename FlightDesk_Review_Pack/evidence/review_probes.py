"""Independent review probes. Synthetic temporary roots only; no real logs/network.
Run: python review_probes.py /path/to/DroneFlightInfoApp
These document observed baseline defects, NOT a passing regression suite.
"""
import sys, tempfile, io, json, zipfile, gzip, hashlib, contextlib
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from flightdesk import db, paths, archive, backup, rawexport, demo, maintenance, capture
from flightdesk.importer import Importer
from flightdesk.parser_adapter import ParseOutcome, sample_from_frame
from flightdesk.analysis import compute_metrics

results = []
def record(name, **values):
    results.append(dict(probe=name, **values))

@contextlib.contextmanager
def sandbox():
    with tempfile.TemporaryDirectory(prefix='flightfix-review-') as d:
        root = Path(d)/'data'
        paths.ensure_layout(root)
        c = db.init_db(paths.db_path(root))
        try:
            yield root, c
        finally:
            c.close()

def outcome(start='2026-01-01T00:00:00+00:00', serial='SYN-A', duration=600, samples=None, name='Synthetic aircraft'):
    return ParseOutcome(11, 'decoded', details={'start_time_utc':start, 'aircraft_sn':serial,
        'aircraft_name':name, 'total_time_s':duration, 'total_distance_m':10}, samples=samples or [])

def ingest(c, root, raw=b'one', parsed=None, filename='FlightRecord_2026-01-01_[00-00-00].txt'):
    with patch('flightdesk.importer.parse_file', return_value=parsed or outcome()):
        return Importer(root,c).import_stream(io.BytesIO(raw),filename,source_kind='manual',source_label='SYNTHETIC REVIEW')

with sandbox() as (root,c):
    ingest(c,root)
    dest=root.parent/'exports'
    rawexport.run(c,root,dest,variants=True,group_by_year=False)
    target=next(dest.glob('*.txt'))
    target.write_bytes(b'owner changed this file')
    rawexport.run(c,root,dest,variants=True,group_by_year=False)
    record('raw_export_overwrites_modified_owned_file', resulting_bytes=target.read_text())

with sandbox() as (root,c):
    ingest(c,root)
    dest=root.parent/'exports';dest.mkdir()
    name='FlightRecord_2026-01-01_[00-00-00].txt'
    (dest/name).write_text('owner first')
    suffixed=dest/(name[:-4]+'__'+hashlib.sha256(b'one').hexdigest()[:8]+'.txt')
    suffixed.write_text('owner second')
    rawexport.run(c,root,dest,variants=True,group_by_year=False)
    record('raw_export_overwrites_suffix_collision', resulting_bytes=suffixed.read_text())

with sandbox() as (root,c):
    ingest(c,root)
    b=backup.create_backup(c,root,root.parent/'backups')
    broken=root.parent/'incomplete.zip'
    with zipfile.ZipFile(b['path']) as src, zipfile.ZipFile(broken,'w') as dst:
        for name in src.namelist():
            if not name.startswith('archive/'):
                dst.writestr(name,src.read(name))
    v=backup.validate_backup(broken)
    record('missing_archive_member_validation',ok=v['ok'],errors=v['errors'])
    omitted=root.parent/'omitted.zip'
    with zipfile.ZipFile(b['path']) as src, zipfile.ZipFile(omitted,'w') as dst:
        for name in src.namelist():
            if name.startswith('archive/'): continue
            data=src.read(name)
            if name=='manifest.json':
                m=json.loads(data);m['archive']=[];data=json.dumps(m).encode()
            dst.writestr(name,data)
    target=root.parent/'restored'
    v=backup.validate_backup(omitted); r=backup.restore_backup(omitted,target)
    rc=db.connect(paths.db_path(target),readonly=True)
    missing=sum(not (target/'archive'/row[0]).exists() for row in rc.execute('SELECT stored_rel FROM raw_files'))
    rc.close()
    record('manifest_omits_db_referenced_object',validate_ok=v['ok'],restore_ok=r['ok'],missing_originals=missing)
    staging=root.parent/'fresh.restore-tmp';staging.mkdir();(staging/'owner.txt').write_text('preserve me')
    backup.restore_backup(Path(b['path']),root.parent/'fresh')
    record('restore_deletes_preexisting_staging_directory',owner_file_survives=(staging/'owner.txt').exists())
    # Simulate a non-default active root using the actual restore API's allow_existing option.
    before=db.table_counts(c)['raw_files'];c.close()
    r=backup.restore_backup(Path(b['path']),root,allow_existing=True)
    record('nondefault_active_root_not_protected',restore_ok=r['ok'])

with sandbox() as (root,c):
    ingest(c,root,b'a',outcome(serial=None,name='Aircraft A'))
    ingest(c,root,b'b',outcome(serial=None,name='Aircraft B'))
    record('missing_serial_fingerprint_merges_different_aircraft',flights=db.table_counts(c)['flights'],raw_files=db.table_counts(c)['raw_files'])

with sandbox() as (root,c):
    ingest(c,root,b'a',outcome())
    ingest(c,root,b'b',outcome(start='2026-01-02T00:00:00+00:00'))
    with patch('flightdesk.importer.parse_file',return_value=outcome(start='2026-01-02T00:00:00+00:00')):
        Importer(root,c).reparse_raw(1)
    record('reparse_links_one_original_to_two_flights',links=[list(r) for r in c.execute('SELECT flight_id,raw_file_id FROM flight_files ORDER BY flight_id,raw_file_id')])

with sandbox() as (root,c):
    s=[{'t':0,'lat':1,'lon':1,'on_ground':True},{'t':10,'lat':1.00001,'lon':1,'on_ground':False}]
    ingest(c,root,b'a',outcome(samples=s))
    corrected=[dict(x,lat=2) for x in s]
    with patch('flightdesk.importer.parse_file',return_value=outcome(samples=corrected)):
        Importer(root,c).reparse_raw(1)
    f=c.execute('SELECT home_lat FROM flights').fetchone()[0]
    blob=c.execute('SELECT samples_blob FROM flight_telemetry').fetchone()[0]
    t=json.loads(gzip.decompress(blob))[0]['lat']
    record('equal_length_reparse_keeps_old_track',flight_home_lat=f,track_first_lat=t)

with sandbox() as (root,c):
    ingest(c,root,b'a',outcome())
    c.execute("UPDATE flights SET pilot='Owner pilot',tags_json='[\"Owner tag\"]'")
    demo.seed_demo(root)
    row=c.execute('SELECT pilot,tags_json FROM flights WHERE id=1').fetchone()
    record('demo_seed_mutates_existing_history',pilot=row['pilot'],tags=row['tags_json'])

samples=[sample_from_frame({'osd':{'flyTime':t,'latitude':1,'longitude':1,'height':0,'isMotorOn':False}}) for t in (0,10)]
m=compute_metrics(samples)
record('absent_ground_flag_becomes_airborne',on_ground=samples[0]['on_ground'],air_time=m.airborne_time_s,flags=m.flags)

with sandbox() as (root,c):
    ingest(c,root)
    now=db.now_utc()
    eq=c.execute("INSERT INTO equipment(kind,name,created_at,updated_at) VALUES('aircraft','SYN',?,?)",(now,now)).lastrowid
    c.execute('UPDATE flights SET aircraft_equipment_id=?,duration_s=600,airborne_time_s=60',(eq,))
    rid=c.execute("INSERT INTO rules(equipment_id,code,label,air_minutes_limit,source,created_at,updated_at) VALUES(?,'basic','SYN',5,'AirData',?,?)",(eq,now,now)).lastrowid
    status=maintenance.rule_status(c,db.one(c,'SELECT * FROM rules WHERE id=?',(rid,)))
    record('maintenance_counts_log_time_as_air_time',status=status['status'],minutes=next(x['value'] for x in status['counters'] if x['metric']=='minutes'))
    bat=c.execute("INSERT INTO equipment(kind,name,created_at,updated_at) VALUES('battery','SYN BAT',?,?)",(now,now)).lastrowid
    assigned=capture.reassign_flight(c,1,'aircraft',bat)
    record('aircraft_reassigned_to_battery',ok=assigned['ok'],aircraft_equipment_id=c.execute('SELECT aircraft_equipment_id FROM flights').fetchone()[0],battery_id=bat)

with sandbox() as (root,c):
    ingest(c,root)
    from tools import reparse_all
    output=io.StringIO()
    with patch('flightdesk.importer.parse_file',return_value=outcome()),contextlib.redirect_stdout(output):
        code=reparse_all.main(['--root',str(root)])
    record('reparse_dry_run_writes_readonly_db',exit=code,readonly_error='readonly' in output.getvalue())

with sandbox() as (root,c):
    c.execute('PRAGMA user_version=999');c.close()
    newer=db.init_db(paths.db_path(root))
    record('future_schema_silently_downgraded',user_version=newer.execute('PRAGMA user_version').fetchone()[0]);newer.close()

with sandbox() as (root,c):
    ingest(c,root)
    raw=db.one(c,'SELECT * FROM raw_files')
    obj=archive.archive_path(root,raw['stored_rel']);obj.write_bytes(b'bad')
    again=ingest(c,root)
    record('archive_same_size_corruption_accepted_as_duplicate',duplicate_count=again.totals['already_imported'],archive_bytes=obj.read_text())

with sandbox() as (root,c):
    from flightdesk import server
    app=server.App(root)
    ctx=server.Ctx(app=app,handler=None,params={},query={},body=b'{"enabled":true}',conn=c)
    with patch.object(server,'_send_json'):
        server.api_usb_watcher(ctx)
    record('watcher_enabled_without_worker',enabled=db.get_setting(c,'usb_watcher')['enabled'],worker_exists=app.watcher_thread is not None)

with sandbox() as (root,c):
    repo=Path(sys.argv[1]).resolve()
    text=(repo/'fixtures/synthetic/AirData-Equipment-Capture-SYNTHETIC.md').read_text()
    doc=capture.parse_capture(text)
    snap=next(x for x in doc.snapshots if x.counters and any(y['metric']=='flights' for y in x.counters))
    counter=next(x for x in snap.counters if x['metric']=='flights');counter['limit']=7
    capture.apply_capture(c,doc,'SYNTHETIC REVIEW')
    eq=capture._find_equipment(c,snap.equipment_name,snap.serial)
    rule=db.one(c,'SELECT flights_limit FROM rules WHERE equipment_id=? AND code=?',(eq['id'],snap.rule))
    record('first_capture_ignores_captured_rule_limit',captured_limit=7,stored_limit=rule['flights_limit'])

from flightdesk import usb
blob=bytearray(120);blob[10]=14;blob[100]=0
record('usb_accepts_v14_header_without_records',accepted=usb.validate_flight_bytes(bytes(blob))[0])

print(json.dumps(results,indent=2))
