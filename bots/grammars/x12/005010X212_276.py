# bots/grammars/x12/005010X212_276.py
syntax = {'version':'005010','field_sep':'*','record_sep':'~','sfield_sep':':','envelope':'x12'}

structure = [
    {'ID':'ISA','MIN':1,'MAX':1,'LEVEL':[
        {'ID':'GS','MIN':1,'MAX':1,'LEVEL':[
            {'ID':'ST','MIN':1,'MAX':999999,'LEVEL':[
                {'ID':'BHT','MIN':1,'MAX':1},
                {'ID':'HL','MIN':1,'MAX':99,'LEVEL':[
                    {'ID':'NM1','MIN':0,'MAX':10},
                    {'ID':'TRN','MIN':0,'MAX':5},
                    {'ID':'REF','MIN':0,'MAX':10},
                    {'ID':'DTP','MIN':0,'MAX':10},
                    {'ID':'SVC','MIN':0,'MAX':10},
                ]},
                {'ID':'SE','MIN':1,'MAX':1},
            ]},
            {'ID':'GE','MIN':1,'MAX':1},
        ]},
        {'ID':'IEA','MIN':1,'MAX':1},
    ]},
]

recorddefs = {
    'ISA': [(['AN',2,2],'ID'), (['AN',10,10],'Sender'), (['AN',10,10],'Receiver')],
    'GS' : [(['ID',2,2],'Code'), (['AN',15,15],'Sender'), (['AN',15,15],'Receiver'), (['DT',8,8],'Date'), (['TM',4,8],'Time'), (['AN',9,9],'GCN'), (['AN',2,2],'Agency'), (['AN',6,12],'Version')],
    'ST' : [(['ID',3,3],'Txn'), (['AN',4,9],'Control')],
    'BHT':[(['ID',4,4],'Hier'),(['ID',2,2],'Purpose'),(['DT',8,8],'Date'),(['TM',4,8],'Time')],
    'HL' :[(['N0',1,12],'ID'),(['N0',1,12],'Parent'),(['ID',1,2],'Level'),(['ID',1,1],'ChildCode')],
    'NM1':[(['ID',2,3],'Entity'),(['ID',1,1],'Type'),(['AN',1,60],'Last'),(['AN',1,35],'First'),(['ID',2,2],'IDQual'),(['AN',2,80],'ID')],
    'TRN':[(['ID',3,3],'Type'),(['AN',1,50],'Ref')],
    'REF':[(['ID',2,3],'Qual'),(['AN',1,50],'Val')],
    'DTP':[(['ID',3,3],'Qual'),(['ID',3,3],'Fmt'),(['DT',8,8],'Date')],
    'SVC':[(['AN',1,80],'Comp'),(['N2',1,18],'LineCharge'),(['N2',1,18],'LinePaid')],
    'SE' :[(['N0',1,10],'Count'),(['AN',4,9],'Control')],
    'GE' :[(['N0',1,9],'Count'),(['AN',1,9],'Control')],
    'IEA':[(['N0',1,9],'Count'),(['AN',1,9],'Control')],
}
