# bots/grammars/x12/005010X221A1_835.py
syntax = {
    'version': '005010',
    'indented': False,
    'field_sep': '*',
    'record_sep': '~',
    'sfield_sep': ':',
    'envelope':'x12',
}

structure = [
    {'ID':'ISA','MIN':1,'MAX':1,'LEVEL':[
        {'ID':'GS','MIN':1,'MAX':1,'LEVEL':[
            {'ID':'ST','MIN':1,'MAX':999999,'LEVEL':[
                {'ID':'BPR','MIN':1,'MAX':1},
                {'ID':'TRN','MIN':1,'MAX':1},
                {'ID':'REF','MIN':0,'MAX':10},
                {'ID':'DTM','MIN':0,'MAX':10},
                {'ID':'N1','MIN':0,'MAX':10,'LEVEL':[
                    {'ID':'N3','MIN':0,'MAX':2},
                    {'ID':'N4','MIN':0,'MAX':1},
                    {'ID':'REF','MIN':0,'MAX':5},
                ]},
                {'ID':'PER','MIN':0,'MAX':3},
                {'ID':'LX','MIN':0,'MAX':999999,'LEVEL':[
                    {'ID':'TS3','MIN':0,'MAX':1},
                    {'ID':'TS2','MIN':0,'MAX':1},
                    {'ID':'CLP','MIN':0,'MAX':999999,'LEVEL':[
                        {'ID':'CAS','MIN':0,'MAX':99},
                        {'ID':'NM1','MIN':0,'MAX':10},
                        {'ID':'MIA','MIN':0,'MAX':1},
                        {'ID':'MOA','MIN':0,'MAX':1},
                        {'ID':'REF','MIN':0,'MAX':10},
                        {'ID':'DTM','MIN':0,'MAX':10},
                        {'ID':'SVC','MIN':0,'MAX':999,'LEVEL':[
                            {'ID':'DTM','MIN':0,'MAX':10},
                            {'ID':'CAS','MIN':0,'MAX':99},
                            {'ID':'REF','MIN':0,'MAX':10},
                        ]},
                    ]},
                ]},
                {'ID':'PLB','MIN':0,'MAX':10},
                {'ID':'SE','MIN':1,'MAX':1},
            ]},
            {'ID':'GE','MIN':1,'MAX':1},
        ]},
        {'ID':'IEA','MIN':1,'MAX':1},
    ]},
]

recorddefs = {
    'ISA':[(['AN',2,2],'ID'),(['AN',10,10],'Sender'),(['AN',10,10],'Receiver'),(['DT',6,6],'Date'),(['TM',4,4],'Time'),
           (['AN',1,1],'Repetition'),(['AN',35,35],'Control'),(['AN',5,5],'Version')],
    'GS' :[(['ID',2,2],'Code'),(['AN',15,15],'Sender'),(['AN',15,15],'Receiver'),(['DT',8,8],'Date'),(['TM',4,8],'Time'),(['AN',9,9],'GroupControl'),(['AN',2,2],'Agency'),(['AN',6,12],'Version')],
    'ST' :[(['ID',3,3],'Txn'),(['AN',4,9],'Control')],
    'BPR':[(['ID',3,3],'TransMethod'),(['N2',1,18],'Amount'),(['ID',1,3],'CreditDebit')],
    'TRN':[(['ID',3,3],'TraceType'),(['AN',1,50],'CheckTrace')],
    'REF':[(['ID',2,3],'Qual'),(['AN',1,50],'Val')],
    'DTM':[(['ID',3,3],'Qual'),(['DT',8,8],'Date')],
    'N1' :[(['ID',2,3],'Entity'),(['AN',1,60],'Name'),(['ID',2,2],'IDQual'),(['AN',2,80],'ID')],
    'N3' :[(['AN',1,55],'Addr1'),(['AN',1,55],'Addr2')],
    'N4' :[(['AN',2,30],'City'),(['AN',2,2],'State'),(['AN',3,15],'Zip'),(['AN',2,3],'Country')],
    'PER':[(['ID',2,2],'FuncCode'),(['AN',1,60],'Name'),(['ID',2,2],'CommQual1'),(['AN',1,256],'Comm1')],
    'LX' :[(['N0',1,6],'AssignNumber')],
    'TS3':[(['AN',1,30],'ProvID'),(['DT',8,8],'FiscalDate'),(['N2',1,15],'TotalBilled')],
    'TS2':[(['N2',1,15],'MonetaryAmount')],
    'CLP':[(['AN',1,20],'ClaimID'),(['ID',1,2],'Status'),(['N2',1,18],'TotalCharge'),(['N2',1,18],'Paid'),(['N2',1,18],'PatResp'),(['AN',1,38],'PayerCtrl'),(['ID',1,2],'Facility'),(['ID',1,1],'ClaimFreq')],
    'CAS':[(['ID',2,2],'Group'),(['ID',1,2],'Reason1'),(['N2',1,18],'Amt1'),(['N0',1,15],'Qty1')],
    'NM1':[(['ID',2,3],'Entity'),(['ID',1,1],'Type'),(['AN',1,60],'NameLast'),(['AN',1,35],'NameFirst'),(['ID',2,2],'IDQual'),(['AN',2,80],'ID')],
    'MIA':[(['N2',1,18],'CoveredCharges')],
    'MOA':[(['N2',1,18],'Reimbursement')],
    'SVC':[(['AN',1,80],'Composite'),(['N2',1,18],'LineCharge'),(['N2',1,18],'LinePaid')],
    'PLB':[(['AN',1,30],'ProvID'),(['DT',8,8],'FiscalDate'),(['AN',3,3],'AdjQual1'),(['AN',1,30],'RefID1'),(['N2',1,18],'Amt1')],
    'SE' :[(['N0',1,10],'Count'),(['AN',4,9],'Control')],
    'GE' :[(['N0',1,9],'Count'),(['AN',1,9],'Control')],
    'IEA':[(['N0',1,9],'Count'),(['AN',1,9],'Control')],
}
