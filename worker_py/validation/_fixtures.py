"""Shared X12 test fixtures for the validation test suite.

Not a test module itself (the leading underscore keeps it out of unittest
discovery). Provides a known-clean 837P plus deliberately broken variants.
"""
from __future__ import annotations

# A fully compliant 837P (005010X222A1): one claim, two balanced service lines,
# valid NPI (Luhn-checked), valid POS / frequency / filing-indicator codes, and
# a valid ICD-10-CM diagnosis. SE01 = 24 segments (ST..SE inclusive).
VALID_837P = (
    "ISA*00*          *00*          *ZZ*SUBMITTER      *ZZ*RECEIVER       "
    "*260515*1023*^*00501*000000001*0*P*:~"
    "GS*HC*SUBMITTER*RECEIVER*20260515*1023*1*X*005010X222A1~"
    "ST*837*0001*005010X222A1~"
    "BHT*0019*00*REF123*20260515*1023*CH~"
    "NM1*41*2*SUBMITTER NAME*****46*123456789~"
    "PER*IC*CONTACT*TE*5551234567~"
    "NM1*40*2*RECEIVER NAME*****46*RECV01~"
    "HL*1**20*1~"
    "NM1*85*2*BILLING CLINIC*****XX*1234567893~"
    "N3*1 MAIN ST~"
    "N4*DENVER*CO*80202~"
    "REF*EI*123456789~"
    "HL*2*1*22*0~"
    "SBR*P*18*******MB~"
    "NM1*IL*1*DOE*JANE****MI*MEM123~"
    "DMG*D8*19800101*F~"
    "NM1*PR*2*MEDICARE*****PI*MEDICARE~"
    "CLM*CLAIM001*150***11:B:1*Y*A*Y*Y~"
    "HI*ABK:E119~"
    "LX*1~"
    "SV1*HC:99213*100*UN*1***1~"
    "DTP*472*D8*20260510~"
    "LX*2~"
    "SV1*HC:85025*50*UN*1***1~"
    "DTP*472*D8*20260510~"
    "SE*24*0001~"
    "GE*1*1~"
    "IEA*1*000000001~"
)


_ISA = (
    "ISA*00*          *00*          *ZZ*SUBMITTER      *ZZ*RECEIVER       "
    "*260515*1023*^*00501*000000001*0*P*:~"
)

# A fully compliant 837I institutional claim (005010X223A2): SV2 service lines
# with revenue codes, CLM05-2 = 'A', a 4-digit type-of-bill, balanced charges.
VALID_837I = (
    _ISA
    + "GS*HC*SUBMITTER*RECEIVER*20260515*1023*1*X*005010X223A2~"
    "ST*837*0001*005010X223A2~"
    "BHT*0019*00*REF123*20260515*1023*CH~"
    "NM1*41*2*SUBMITTER NAME*****46*123456789~"
    "PER*IC*CONTACT*TE*5551234567~"
    "NM1*40*2*RECEIVER NAME*****46*RECV01~"
    "HL*1**20*1~"
    "NM1*85*2*GENERAL HOSPITAL*****XX*1234567893~"
    "N3*1 MAIN ST~"
    "N4*DENVER*CO*80202~"
    "REF*EI*123456789~"
    "HL*2*1*22*0~"
    "SBR*P*18*******MA~"
    "NM1*IL*1*DOE*JANE****MI*MEM123~"
    "DMG*D8*19800101*F~"
    "NM1*PR*2*MEDICARE*****PI*MEDICARE~"
    "CLM*ICLAIM01*1200***0111:A:1*Y*A*Y*Y~"
    "DTP*434*RD8*20260501-20260503~"
    "HI*ABK:J189~"
    "LX*1~"
    "SV2*0120*HC:99231*800*UN*3~"
    "LX*2~"
    "SV2*0250*HC:J1885*400*UN*4~"
    "SE*23*0001~"
    "GE*1*1~"
    "IEA*1*000000001~"
)

# A fully compliant 837D dental claim (005010X224A2): SV3 service lines with
# CDT D-codes, CLM05-2 = 'B', balanced charges, line service dates.
VALID_837D = (
    _ISA
    + "GS*HC*SUBMITTER*RECEIVER*20260515*1023*1*X*005010X224A2~"
    "ST*837*0001*005010X224A2~"
    "BHT*0019*00*REF123*20260515*1023*CH~"
    "NM1*41*2*SUBMITTER NAME*****46*123456789~"
    "PER*IC*CONTACT*TE*5551234567~"
    "NM1*40*2*RECEIVER NAME*****46*RECV01~"
    "HL*1**20*1~"
    "NM1*85*2*DENTAL CLINIC*****XX*1234567893~"
    "N3*1 MAIN ST~"
    "N4*DENVER*CO*80202~"
    "REF*EI*123456789~"
    "HL*2*1*22*0~"
    "SBR*P*18*******CI~"
    "NM1*IL*1*DOE*JANE****MI*MEM123~"
    "DMG*D8*19800101*F~"
    "NM1*PR*2*DELTA DENTAL*****PI*DELTA~"
    "CLM*DCLAIM01*450***22:B:1*Y*A*Y*I~"
    "HI*ABK:K029~"
    "LX*1~"
    "SV3*AD:D0120*150*11~"
    "DTP*472*D8*20260510~"
    "LX*2~"
    "SV3*AD:D1110*300*11~"
    "DTP*472*D8*20260510~"
    "SE*24*0001~"
    "GE*1*1~"
    "IEA*1*000000001~"
)

# A fully balanced 835 remittance (005010X221A1): claim and service balance,
# transaction balances to BPR02.
VALID_835 = (
    _ISA.replace("*P*:~", "*T*:~")
    + "GS*HP*SUBMITTER*RECEIVER*20260515*1023*1*X*005010X221A1~"
    "ST*835*0001~"
    "BPR*I*200.00*C*ACH*CTX*01*999999999*DA*123456789*1512345678*"
    "*01*999988888*DA*987654321*20260515~"
    "TRN*1*1234567890*1512345678~"
    "DTM*405*20260515~"
    "N1*PR*PRIMARY HEALTH PLAN*XV*842610001~"
    "N3*123 HEALTH ST~"
    "N4*METROPOLIS*NY*10101~"
    "N1*PE*PAYEE CLINIC*XX*1234567893~"
    "N3*456 CLINIC AVE~"
    "N4*GOTHAM*NY*10001~"
    "REF*TJ*999988888~"
    "LX*1~"
    "CLP*CLAIM001*1*250.00*200.00*50.00*MC*PAYERCTRL01*11*1~"
    "NM1*QC*1*PATIENT*ONE****MI*MEM001~"
    "SVC*HC:99213*250.00*200.00**1~"
    "DTM*472*20260510~"
    "CAS*CO*45*25.00~"
    "CAS*PR*1*25.00~"
    "AMT*B6*200.00~"
    "SE*20*0001~"
    "GE*1*1~"
    "IEA*1*000000001~"
)

VALID_270 = (
    _ISA.replace("*P*:~", "*T*:~")
    + "GS*HS*SUBMITTER*RECEIVER*20260515*1023*1*X*005010X279A1~"
    "ST*270*0001*005010X279A1~"
    "BHT*0022*13*REF270*20260515*1023~"
    "HL*1**20*1~"
    "NM1*PR*2*PRIMARY PAYER*****PI*PAYER01~"
    "HL*2*1*21*1~"
    "NM1*1P*2*PROVIDER*****XX*1234567893~"
    "HL*3*2*22*0~"
    "TRN*1*TRACE001~"
    "NM1*IL*1*MEMBER*TEST****MI*MEM001~"
    "DMG*D8*19800101*F~"
    "EQ*30~"
    "SE*12*0001~GE*1*1~IEA*1*000000001~"
)

VALID_271 = (
    _ISA.replace("*P*:~", "*T*:~")
    + "GS*HB*SUBMITTER*RECEIVER*20260515*1023*1*X*005010X279A1~"
    "ST*271*0001*005010X279A1~"
    "BHT*0022*11*REF271*20260515*1023~"
    "HL*1**20*1~"
    "NM1*PR*2*PRIMARY PAYER*****PI*PAYER01~"
    "HL*2*1*21*1~"
    "NM1*1P*2*PROVIDER*****XX*1234567893~"
    "HL*3*2*22*0~"
    "TRN*1*TRACE001~"
    "NM1*IL*1*MEMBER*TEST****MI*MEM001~"
    "DMG*D8*19800101*F~"
    "EB*1*IND*30**29~"
    "SE*12*0001~GE*1*1~IEA*1*000000001~"
)

VALID_276 = (
    _ISA.replace("*P*:~", "*T*:~")
    + "GS*HR*SUBMITTER*RECEIVER*20260515*1023*1*X*005010X212~"
    "ST*276*0001*005010X212~"
    "BHT*0010*13*REF276*20260515*1023~"
    "HL*1**20*1~"
    "NM1*PR*2*RESPONSIBLE PAYER*****PI*PAYER01~"
    "HL*2*1*21*1~"
    "NM1*41*2*REQUESTER*****46*REQ01~"
    "HL*3*2*19*1~"
    "NM1*1P*2*PROVIDER*****XX*1234567893~"
    "HL*4*3*22*0~"
    "NM1*IL*1*PATIENT*TEST****MI*MEM001~"
    "TRN*1*TRACE001~"
    "REF*1K*CLAIM001~"
    "DTP*472*D8*20260510~"
    "SE*14*0001~GE*1*1~IEA*1*000000001~"
)

VALID_277 = (
    _ISA.replace("*P*:~", "*T*:~")
    + "GS*HN*SUBMITTER*RECEIVER*20260515*1023*1*X*005010X212~"
    "ST*277*0001*005010X212~"
    "BHT*0010*08*REF277*20260515*1023~"
    "HL*1**20*1~"
    "NM1*PR*2*RESPONSIBLE PAYER*****PI*PAYER01~"
    "HL*2*1*21*1~"
    "NM1*41*2*REQUESTER*****46*REQ01~"
    "HL*3*2*19*1~"
    "NM1*1P*2*PROVIDER*****XX*1234567893~"
    "HL*4*3*22*0~"
    "NM1*IL*1*PATIENT*TEST****MI*MEM001~"
    "TRN*2*TRACE001~"
    "STC*A2:20*20260515*WQ*150~"
    "REF*1K*CLAIM001~"
    "SE*14*0001~GE*1*1~IEA*1*000000001~"
)

VALID_278 = (
    _ISA.replace("*P*:~", "*T*:~")
    + "GS*HI*SUBMITTER*RECEIVER*20260515*1023*1*X*005010X217~"
    "ST*278*0001*005010X217~"
    "BHT*0007*13*REF278*20260515*1023~"
    "HL*1**20*1~"
    "NM1*X3*2*UTILIZATION MGMT ORG*****PI*UMO01~"
    "HL*2*1*21*1~"
    "NM1*1P*2*REQUESTING PROVIDER*****XX*1234567893~"
    "HL*3*2*22*0~"
    "NM1*IL*1*PATIENT*TEST****MI*MEM001~"
    "HL*4*3*EV*1~"
    "UM*HS*I*1~"
    "SE*11*0001~GE*1*1~IEA*1*000000001~"
)


def with_replacement(text: str = VALID_837P) -> str:
    """Return an 837P whose claim is a replacement (frequency 7) — which under
    SNIP 4 must then also carry a REF*F8 payer claim control number."""
    return text.replace("CLM*CLAIM001*150***11:B:1", "CLM*CLAIM001*150***11:B:7")


def with_unbalanced_claim(text: str = VALID_837P) -> str:
    """Return an 837P whose CLM02 no longer equals the service-line charge sum."""
    return text.replace("CLM*CLAIM001*150", "CLM*CLAIM001*999")
