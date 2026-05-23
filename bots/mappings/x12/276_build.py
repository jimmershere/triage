# bots/mappings/x12/276_build.py
from bots.botsconfig import *
from bots.botslib import *
import json, datetime, os, psycopg

# Minimal profile loader; expects a 'partner_profiles' table.
# You can seed a 'default' row; payload can pass {"partner_key":"medicare_nh"} to select others.
PROFILE_SQL = """
SELECT
  partner_key,
  payer_id,
  payer_id_qual,
  provider_id,
  provider_id_qual,
  isa_sender_id,
  isa_receiver_id,
  gs_sender_code,
  gs_receiver_code,
  usage_indicator
FROM partner_profiles
WHERE partner_key = %s
LIMIT 1
"""

def load_profile(partner_key: str):
    dsn = os.getenv("TRIAGE_PG_DSN", "postgresql://edi:edi@localhost:5432/edi")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(PROFILE_SQL, (partner_key,))
            row = cur.fetchone()
            if not row:
                return {}
            cols = [d.name for d in cur.description]
            return dict(zip(cols, row))

def merge(a, b):
    c = dict(a or {})
    c.update({k:v for k,v in (b or {}).items() if v not in (None, "")})
    return c

def set_envelope_from_profile(out, prof):
    # Only set if supplied; bots will otherwise use partner config.
    if prof.get('isa_sender_id'): out.ta_info['ISA06'] = prof['isa_sender_id']
    if prof.get('isa_receiver_id'): out.ta_info['ISA08'] = prof['isa_receiver_id']
    if prof.get('gs_sender_code'): out.ta_info['GS02'] = prof['gs_sender_code']
    if prof.get('gs_receiver_code'): out.ta_info['GS03'] = prof['gs_receiver_code']
    if prof.get('usage_indicator'): out.ta_info['ISA15'] = prof['usage_indicator']  # T=Test, P=Prod

def main(inn, out):
    # Expect JSON body with at least: subscriber_id, dos; optionally partner_key, target_st
    content = inn.ta_info.get('content')
    payload = json.loads(content) if content else {}

    partner_key = payload.get('partner_key', 'default')
    prof = load_profile(partner_key)

    # Defaults from profile with payload overrides
    st = payload.get('target_st') or '276'
    ctl = payload.get('control') or datetime.datetime.now().strftime('%H%M%S')
    payer_id    = payload.get('payer_id')    or prof.get('payer_id')
    payer_id_qual = payload.get('payer_id_qual') or prof.get('payer_id_qual') or 'PI'
    provider_id = payload.get('provider_id') or prof.get('provider_id')
    provider_id_qual = payload.get('provider_id_qual') or prof.get('provider_id_qual') or 'XX'
    subscriber  = payload.get('subscriber_id')
    claim_id    = payload.get('claim_id')
    trace       = payload.get('trace') or f"{partner_key.upper()}-{ctl}"
    dos         = payload.get('dos')  # YYYYMMDD

    # Validate minimums
    if not (payer_id and provider_id and subscriber and dos):
        raise TranslationError(
            "Missing required fields: need payer_id, provider_id, subscriber_id, dos (YYYYMMDD). Provide via partner profile + payload."
        )

    # Envelope hints from profile (optional; bots partner config still applies)
    set_envelope_from_profile(out, prof)

    # Build transaction
    out.put({'BOTSID':'ST','Txn':st,'Control':ctl})
    out.put({'BOTSID':'BHT','Hier':'0022','Purpose':'13','Date':datetime.date.today().strftime('%Y%m%d'),'Time':datetime.datetime.now().strftime('%H%M')})
    # HL payer
    out.put({'BOTSID':'HL','ID':'1','Parent':'','Level':'20','ChildCode':'1'})
    out.put({'BOTSID':'NM1','Entity':'PR','Type':'2','Last':'PAYER','IDQual':payer_id_qual,'ID':payer_id})
    # HL provider
    out.put({'BOTSID':'HL','ID':'2','Parent':'1','Level':'21','ChildCode':'1'})
    out.put({'BOTSID':'NM1','Entity':'1P','Type':'2','Last':'PROVIDER','IDQual':provider_id_qual,'ID':provider_id})
    # HL subscriber
    out.put({'BOTSID':'HL','ID':'3','Parent':'2','Level':'22','ChildCode':'0'})
    out.put({'BOTSID':'NM1','Entity':'IL','Type':'1','Last':'SUBSCRIBER','First':'TEST','IDQual':'MI','ID':subscriber})
    out.put({'BOTSID':'TRN','Type':'1','Ref':trace})
    out.put({'BOTSID':'DTP','Qual':'472','Fmt':'D8','Date':dos})

    if st == '276':
        if claim_id:
            out.put({'BOTSID':'REF','Qual':'1K','Val':claim_id})
        # Optional SVC probe to scope status
        out.put({'BOTSID':'SVC','Comp':'HC:99213','LineCharge':'0','LinePaid':'0'})

    out.put({'BOTSID':'SE','Count':'10','Control':ctl})
