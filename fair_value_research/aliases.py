"""Read-only discovery / extraction of cross-market bond identities."""
from pathlib import Path
import json
from dotenv import load_dotenv
from .collect import save


def main():
    load_dotenv()
    from juyuan_update.db import connect
    out=Path("outputs/fair_value_v1/inputs")
    bonds=json.loads((out/"universe.json").read_text(encoding="utf8"))["bonds"]
    with connect() as c:
        c.call_timeout=180000
        q=c.cursor()
        q.execute("SELECT TABLE_NAME,COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE TABLE_NAME IN ('TQ_BD_NEWESTBASICINFO','TQ_BD_BASICINFO') AND COLUMN_NAME LIKE '%ISIN%'")
        columns=sorted(set((t,n) for t,n in q.fetchall() if n in {"ISIN","ISINCODE","ISIN_CODE","ISINNO"}))
        print("identity columns",columns,flush=True)
        table,isin=columns[0] if columns else (None,None)
        if not isin:
            save(out/"aliases.json",{"source":"no_isin_column","identities":{}})
            return
        values={}
        secodes=sorted({b["secode"] for b in bonds if b.get("secode")})
        for i in range(0,len(secodes),400):
            binds={f"s{j}":s for j,s in enumerate(secodes[i:i+400])}
            marks=",".join(":"+k for k in binds)
            q.execute(f"SELECT SECODE,{isin} FROM {table} WHERE SECODE IN ({marks}) AND ISVALID=1",binds)
            for sec,key in q:
                if key:
                    values[str(sec)]=str(key)
        identities={b["code"]:values[b["secode"]] for b in bonds if b.get("secode") in values}
        save(out/"aliases.json",{"source":isin,"identities":identities})
        print("identities",len(identities),"unique",len(set(identities.values())),flush=True)


if __name__=="__main__":
    main()
