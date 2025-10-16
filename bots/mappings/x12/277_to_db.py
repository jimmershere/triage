# bots/mappings/x12/277_to_db.py
from bots.botsconfig import *
from bots.botslib import *
import os, psycopg

def main(inn, out):
    with psycopg.connect(os.getenv("HEDI_PG_DSN","postgresql://hedi:hedi@localhost:5432/hedi"), autocommit=False) as conn:
        with conn.cursor() as cur:
            stc = inn.get({'BOTSID':'ST','Control':None})
            for hl in inn.getloop({'BOTSID':'ST'},{'BOTSID':'HL'}):
                subs = hl.get({'BOTSID':'NM1','Entity':'IL'})
                sub_id = subs.get({'BOTSID':'NM1','ID':None}) if subs else None
                # capture payer claim control if present
                ref1 = hl.get({'BOTSID':'REF','Qual':'1K','Val':None}) or hl.get({'BOTSID':'REF','Qual':'XZ','Val':None})
                for stcseg in hl.getloop({'BOTSID':'STC'}):
                    statusinfo = stcseg.get({'BOTSID':'STC','StatusInfo':None})
                    date = stcseg.get({'BOTSID':'STC','Date':None})
                    amt  = stcseg.get({'BOTSID':'STC','Amt':None})
                    qty  = stcseg.get({'BOTSID':'STC','Qty':None})
                    cur.execute("""
                        INSERT INTO claim_status_277 (st_control, subscriber_id, payer_claim_ctrl, status_info, status_date, amount, quantity)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT DO NOTHING
                    """, (stc, sub_id, ref1, statusinfo, date, amt, qty))
        conn.commit()
    out.put({'BOTSID':'ST','note':'277 persisted'})
