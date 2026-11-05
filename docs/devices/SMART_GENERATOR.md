## SMART_GENERATOR

*Sensors*
- AC Out Power (`pd.acPower`)
- DC Out Power (`pd.dcPower`)
- Total Out Power (`pd.totalPower`)
- Generator Max Output Power (`pd.oilMaxOutPower`)
- Fuel Level (`pd.oilVal`)
- Remaining Time (`pd.remainTime`)
- Motor Run Time (`pd.motorUseTime`)
- AC Out Volts (`pd.acVol`)
- DC Out Voltage (`pd.dcVol`)
- AC Out Current (`pd.acCur`)
- DC Out Current (`pd.dcCur`)
- Temperature (`pd.temp`)
- System Mode (`pd.sysMode`)
- Error Code (`pd.errCode`)
- Generator Type (`pd.type`)   _(disabled)_
- Unit Number (`pd.num`)   _(disabled)_
- Firmware Version (`pd.ver`)   _(disabled)_
- Cell ID (`pd.cellId`)   _(disabled)_
- Hours Since Oil Change (`maintenance.oil.hoursSince`)
- Hours Since Air Filter Cleaning (`maintenance.elementClean.hoursSince`)
- Hours Since Sediment Cup (`maintenance.bowl.hoursSince`)
- Hours Since Spark Plug (`maintenance.plug.hoursSince`)
- Hours Since Spark Arrester (`maintenance.arrester.hoursSince`)
- Status

*Binary sensors*
- DC Output State (`pd.dcState`)

*Switches*
- Generator Motor (`pd.motorState` -> `{"moduleType": 2, "operateType": "motorCtrl", "params": {"enable": "VALUE"}}`)
- AC Enabled (`pd.acState` -> `{"moduleType": 2, "operateType": "acCtrl", "params": {"enable": "VALUE"}}`)


