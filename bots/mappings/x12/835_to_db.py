# bots/mappings/x12/835_to_db.py
from bots.botsconfig import *
from bots.botslib import *
import os, psycopg
from decimal import Decimal

def main(inn, out):
    with psycopg.connect(
        os.getenv("TRIAGE_PG_DSN", "postgresql://edi:edi@localhost:5432/edi"), autocommit=False
    ) as conn:
        with conn.cursor() as cur:
            for st in inn.getloop({'BOTSID':'ST'}):
                hdr = extract_header(st)
                cur.execute("""
                    INSERT INTO era_835_header (st_control, bpr_method, bpr_amount, trn_trace, payer_name, payer_id, payee_name, payee_id, chk_date)
                    VALUES (%(st)s,%(method)s,%(amt)s,%(trace)s,%(payer)s,%(payer_id)s,%(payee)s,%(payee_id)s,%(chkdt)s)
                    ON CONFLICT (st_control) DO NOTHING
                """, hdr)

                for lx in st.getloop({'BOTSID':'LX'}):
                    for clp in lx.getloop({'BOTSID':'CLP'}):
                        clp_row = extract_clp(clp, hdr)
                        cur.execute("""
                            INSERT INTO era_835_clp (st_control, claim_id, status, total_charge, paid, patient_resp, payer_ctrl, facility, claim_freq)
                            VALUES (%(st)s,%(claim)s,%(status)s,%(chg)s,%(paid)s,%(pat)s,%(pctrl)s,%(fac)s,%(freq)s)
                            ON CONFLICT DO NOTHING
                        """, clp_row)
                        for cas in clp.getloop({'BOTSID':'CAS'}):
                            cas_row = extract_cas(cas, hdr, clp_row)
                            cur.execute("""
                                INSERT INTO era_835_cas (st_control, claim_id, adj_group, adj_reason, amount, quantity)
                                VALUES (%(st)s,%(claim)s,%(grp)s,%(rsn)s,%(amt)s,%(qty)s)
                                ON CONFLICT DO NOTHING
                            """, cas_row)

                for plb in st.getloop({'BOTSID':'PLB'}):
                    plb_row = extract_plb(plb, hdr)
                    cur.execute("""
                        INSERT INTO era_835_plb (st_control, provider_id, fiscal_date, adj_qual, ref_id, amount)
                        VALUES (%(st)s,%(prov)s,%(fdate)s,%(qual)s,%(ref)s,%(amt)s)
                        ON CONFLICT DO NOTHING
                    """, plb_row)
        conn.commit()
    out.put({'BOTSID':'ST','note':'835 persisted'})

def _get(seg, key, default=None, cast=str):
    val = seg.get({'BOTSID':seg.ta_info['BOTSID'], key:None})
    if val in (None,''):
        return default
    try:
        return cast(val)
    except Exception:
        return default

def extract_header(st):
    stctl = st.get({'BOTSID':'ST','Control':None})
    bpr  = st.get({'BOTSID':'BPR'})
    trn  = st.get({'BOTSID':'TRN'})
    payer = st.get({'BOTSID':'N1','Entity':'PR'})
    payee = st.get({'BOTSID':'N1','Entity':'PE'})
    return dict(
        st = stctl,
        method = bpr.get({'BOTSID':'BPR','TransMethod':None}) if bpr else None,
        amt = _get(bpr,'Amount',0,Decimal) if bpr else Decimal(0),
        trace = trn.get({'BOTSID':'TRN','CheckTrace':None}) if trn else None,
        payer = payer.get({'BOTSID':'N1','Name':None}) if payer else None,
        payer_id = payer.get({'BOTSID':'N1','ID':None}) if payer else None,
        payee = payee.get({'BOTSID':'N1','Name':None}) if payee else None,
        payee_id = payee.get({'BOTSID':'N1','ID':None}) if payee else None,
        chkdt = st.get({'BOTSID':'DTM','Qual':'405','Date':None}),
    )

def extract_clp(clp, hdr):
    return dict(
        st=hdr['st'],
        claim=clp.get({'BOTSID':'CLP','ClaimID':None}),
        status=clp.get({'BOTSID':'CLP','Status':None}),
        chg=_get(clp,'TotalCharge',0,Decimal),
        paid=_get(clp,'Paid',0,Decimal),
        pat=_get(clp,'PatResp',0,Decimal),
        pctrl=clp.get({'BOTSID':'CLP','PayerCtrl':None}),
        fac=clp.get({'BOTSID':'CLP','Facility':None}),
        freq=clp.get({'BOTSID':'CLP','ClaimFreq':None}),
    )

def extract_cas(cas, hdr, clp_row):
    return dict(
        st=hdr['st'],
        claim=clp_row['claim'],
        grp=cas.get({'BOTSID':'CAS','Group':None}),
        rsn=cas.get({'BOTSID':'CAS','Reason1':None}),
        amt=_get(cas,'Amt1',0,Decimal),
        qty=_get(cas,'Qty1',0,int),
    )

def extract_plb(plb, hdr):
    return dict(
        st=hdr['st'],
        prov=plb.get({'BOTSID':'PLB','ProvID':None}),
        fdate=plb.get({'BOTSID':'PLB','FiscalDate':None}),
        qual=plb.get({'BOTSID':'PLB','AdjQual1':None}),
        ref=plb.get({'BOTSID':'PLB','RefID1':None}),
        amt=_get(plb,'Amt1',0,Decimal),
    )
