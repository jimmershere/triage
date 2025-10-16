# bots/mappings/x12/271_to_db.py
from bots.botsconfig import *
from bots.botslib import *
import os, psycopg

def main(inn, out):
    with psycopg.connect(os.getenv("HEDI_PG_DSN","postgresql://hedi:hedi@localhost:5432/hedi"), autocommit=False) as conn:
        with conn.cursor() as cur:
            stc = inn.get({'BOTSID':'ST','Control':None})
            for hl in inn.getloop({'BOTSID':'ST'},{'BOTSID':'HL'}):
                subs = hl.get({'BOTSID':'NM1','Entity':'IL'}) or hl.get({'BOTSID':'NM1','Entity':'MI'})
                payer = hl.get({'BOTSID':'NM1','Entity':'PR'})
                sub_id = subs.get({'BOTSID':'NM1','ID':None}) if subs else None
                payer_id = payer.get({'BOTSID':'NM1','ID':None}) if payer else None
                for eb in hl.getloop({'BOTSID':'EB'}):
                    eb_code = eb.get({'BOTSID':'EB','Elig':None})
                    svc_type= eb.get({'BOTSID':'EB','ServiceType':None})
                    plan    = eb.get({'BOTSID':'EB','PlanCov':None})
                    netwk   = eb.get({'BOTSID':'EB','Netwk':None})
                    descr   = eb.get({'BOTSID':'EB','Desc':None})
                    cur.execute("""
                        INSERT INTO eligibility_271 (st_control, subscriber_id, payer_id, eb_code, service_type, coverage_plan, network, description)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT DO NOTHING
                    """, (stc, sub_id, payer_id, eb_code, svc_type, plan, netwk, descr))
        conn.commit()
    out.put({'BOTSID':'ST','note':'271 persisted'})
