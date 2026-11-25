"""Synthetic workflows shaped like a two-system trade reconciliation (same tool kinds, the same kind of rules,
one scenario tree repeated). No real data: every value here is invented."""
from yx import WB

CHECKS = {
    "SIDE": ("Discrepancy in Side", "If [SIDE]=[SYSB_SIDE] then '' ELSE 'Discrepancy in Side' ENDIF"),
    "CCY-1": ("Discrepancy in Currency", "IF [CCY1]=[SYSB_CCY1] THEN '' ELSE 'Discrepancy in Currency' ENDIF"),
    "CCY-BOTH": ("Discrepancy in Currency", "IF [CCY1]=[SYSB_CCY1] AND [CCY2]=[SYSB_CCY2] THEN '' ELSEif [CCY1]=[SYSB_CCY2] "
                 "AND [CCY2]=[SYSB_CCY1] THEN '' ELSE 'Discrepancy in Currency' ENDIF"),
    "AMT-1": ("Discrepancy in Contract Amount", "IF ABS([SYSB_AMOUNT1])>=(ABS([AMOUNT1])*0.9) AND "
              "ABS([SYSB_AMOUNT1])<=(ABS([AMOUNT1])*1.1) THEN '' ELSE 'Discrepancy in Contract Amount' ENDIF"),
    "AMT-BOTH": ("Discrepancy in Contract Amount", "IF [CCY1]=[SYSB_CCY1] AND [CCY2]=[SYSB_CCY2] AND "
                 "ABS([SYSB_AMOUNT1])>=(ABS([AMOUNT1])*0.9) AND ABS([SYSB_AMOUNT1])<=(ABS([AMOUNT1])*1.1) AND "
                 "ABS([SYSB_AMOUNT2])>=(ABS([AMOUNT2])*0.9) AND ABS([SYSB_AMOUNT2])<=(ABS([AMOUNT2])*1.1) THEN '' "
                 "ELSEIF [CCY1]=[SYSB_CCY2] AND [CCY2]=[SYSB_CCY1] AND ABS([SYSB_AMOUNT2])>=(ABS([AMOUNT1])*0.9) AND "
                 "ABS([SYSB_AMOUNT2])<=(ABS([AMOUNT1])*1.1) AND ABS([SYSB_AMOUNT1])>=(ABS([AMOUNT2])*0.9) AND "
                 "ABS([SYSB_AMOUNT1])<=(ABS([AMOUNT2])*1.1) THEN '' ELSE 'Discrepancy in Contract Amount' ENDIF"),
    "PREMIUM": ("Discrepancy in Premium Amount", "IF [PREMIUM CCY]=[SYSB_PREMIUM CCY] AND ABS([SYSB_PREMIUM AMOUNT])>="
                "(ABS([PREMIUM AMOUNT])*0.9) AND ABS([SYSB_PREMIUM AMOUNT])<= (ABS([PREMIUM AMOUNT])*1.1) THEN '' "
                "ELSE 'Discrepancy in Premium Amount' ENDIF"),
    "NOTIONAL": ("Discrepancy in Notional Amount", "IF [NOTIONAL CCY]=[SYSB_NOTIONAL CCY] AND ABS([SYSB_NOTIONAL AMOUNT])>="
                 "(ABS([NOTIONAL AMOUNT])*0.9) AND ABS([SYSB_NOTIONAL AMOUNT])<=(ABS([NOTIONAL AMOUNT])*1.1) THEN '' "
                 "ELSE 'Discrepancy in Notional Amount' ENDIF"),
    "TRADE": ("Discrepancy in Trade Date", "If TODATE([TRADE DATE])=TODATE([SYSB_TRADE DATE]) then '' ELSE "
              "'Discrepancy in Trade Date' ENDIF"),
    "EXPIRY": ("Discrepancy in Expiry Date", "If [SYSB_EXPIRY DATE]=[EXPIRY DATE] OR DateTimeDiff([SYSB_EXPIRY DATE],"
               "[SYSB_TRADE DATE],'DAYS')<365 or (DateTimeDiff([EXPIRY DATE],[TRADE DATE],'DAYS'))>(DateTimeDiff("
               "[SYSB_EXPIRY DATE],[SYSB_TRADE DATE],'DAYS')) THEN '' ELSE 'Discrepancy in Expiry Date' ENDIF"),
    "POSITION": ("Discrepancy in Position", "If [SYSB_POSITION]=[POSITION] OR [SYSB_POSITION]=[POSITION2] or "
                 "isempty([POSITION]) or isempty([POSITION2]) then '' ELSE 'Discrepancy in Position' ENDIF"),
}
CLEANUP = r'REGEX_Replace(REGEX_Replace(REPLACE([_CurrentField_],";;",";"),"\G;",""),";$","")'
SMALL = [("2", 'Contains([SYSB_RISKCLASS],"TYPEA") OR Contains([SYSB_RISKCLASS],"TYPEB")', ["SIDE", "CCY-1"]),
         ("4", '[SYSB_RISKCLASS] = "TYPEC"', ["CCY-1", "AMT-1"]),
         ("1", None, ["SIDE", "CCY-1", "AMT-1"])]
FULL = [("2", 'Contains([SYSB_RISKCLASS],"TYPED") OR Contains([SYSB_RISKCLASS],"TYPEB") OR Contains([SYSB_RISKCLASS],"TYPEE") '
         'OR Contains([SYSB_RISKCLASS],"TYPEF") OR Contains([SYSB_RISKCLASS],"TYPEA")', ["SIDE", "CCY-BOTH", "NOTIONAL", "TRADE", "EXPIRY"]),
        ("4", '[SYSB_RISKCLASS] = "TYPEC"', ["CCY-BOTH", "AMT-BOTH", "PREMIUM", "TRADE", "EXPIRY"]),
        ("3", '[SYSB_RISKCLASS] = "TYPEG" OR [SYSB_RISKCLASS] = "TYPEH"', ["SIDE", "CCY-1", "AMT-1", "PREMIUM", "TRADE", "EXPIRY"]),
        ("1", None, ["POSITION", "SIDE", "CCY-BOTH", "AMT-BOTH", "PREMIUM", "TRADE", "EXPIRY"])]


def tree(wb, base, src, scenarios, box=(), day="T+1"):
    """One scenario tree fed by `src` (tool id, output anchor). Returns the id of its last tool."""
    n = [base]

    def nid():
        n[0] += 1
        return n[0]

    ends, stream = [], src
    for label, test, checks in scenarios:
        if test is not None:
            f = wb.filter(nid(), test, box=box)
            wb.conn(stream[0], f, sa=stream[1])
            cur, stream = (f, "True"), (f, "False")
        else:
            cur = stream
        for c in checks:
            t = wb.formula(nid(), [CHECKS[c]], box=box)
            wb.conn(cur[0], t, sa=cur[1])
            cur = (t, "Output")
        tr = wb.transpose(nid(), ["Unique ID"], list(dict.fromkeys(CHECKS[c][0] for c in checks)), box=box)
        su = wb.summarize(nid(), [("Unique ID", "GroupBy", "Unique ID"), ("Value", "Concat", "Discrepancies", ";")], box=box)
        mf = wb.multi_field(nid(), ["Discrepancies"], CLEANUP, box=box)
        fr = wb.find_replace(nid(), "Unique ID", "Unique ID", "FindWhole", ["Discrepancies"], box=box)
        sc = wb.formula(nid(), [("Scenario", "'%s'" % label)], box=box)
        wb.conn(cur[0], tr, sa=cur[1]); wb.conn(tr, su); wb.conn(su, mf)
        wb.conn(cur[0], fr, sa=cur[1], da="Targets"); wb.conn(mf, fr, da="Source"); wb.conn(fr, sc)
        ends.append(sc)
    u = wb.union(nid(), box=box)
    for k, e in enumerate(ends, 1):
        wb.conn(e, u, name="#%d" % k)
    out = wb.formula(nid(), [("RECONCILED " + day, "IF IsEmpty([Discrepancies]) THEN 'Y' ELSE 'N' ENDIF"),
                             ("COMMENTS " + day, "IF IsEmpty([Discrepancies]) THEN 'All details matched' ELSE [Discrepancies] ENDIF")],
                     box=box)
    wb.conn(u, out)
    return out


RENAME = [("Right_" + f, True, "SYSB_" + f) for f in ("DEAL ID", "SIDE", "CCY1", "AMOUNT1", "RISKCLASS", "VKEY")]


def small():
    """Deal-ID path and validation-key path, each followed by the same scenario tree."""
    wb = WB()
    wb.container(["Recon T+1"], 900)
    box = ("Recon T+1",)
    S, D = "V_WString", "Double"
    wb.text_input(1, ["DEAL ID", "SIDE", "CCY1", "AMOUNT1", "VKEY"],
                  [["D1", "BUY", "USD", "100", "K1"], ["D2", "SELL", "EUR", "200", "K2"], ["D3", "BUY", "chf", "300", "K3"],
                   [None, "BUY", "USD", "500", "K9"]], dict(zip(["DEAL ID", "SIDE", "CCY1", "AMOUNT1", "VKEY"], [S, S, S, D, S])))
    wb.text_input(2, ["DEAL ID", "SIDE", "CCY1", "AMOUNT1", "RISKCLASS", "VKEY"],
                  [["D1", "BUY", "USD", "-105", "CURR", "K1"], ["D2", "BUY", "EUR", "200", "TYPEA", "K2"],
                   ["D3", "BUY", "CHF", "400", "TYPEC", "K3"], ["X7", "SELL", "GBP", "500", "CURR", "K9"]],
                  dict(zip(["DEAL ID", "SIDE", "CCY1", "AMOUNT1", "RISKCLASS", "VKEY"], [S, S, S, D, S, S])))
    wb.formula(3, [("SYSTEM", "'SYSA'")], box=box)
    wb.record_id(4, "RecordID", 1, "String", 6, box=box)
    wb.formula(5, [("Unique ID", "[SYSTEM]+[RecordID]")], box=box)
    wb.filter(8, "!IsEmpty([DEAL ID])", box=box)
    wb.join(10, ["DEAL ID"], ["DEAL ID"], RENAME + [("*Unknown", True)], box=box)
    wb.union(11, box=box)
    wb.join(20, ["VKEY"], ["VKEY"], RENAME + [("*Unknown", True)], box=box)
    wb.conn(1, 3); wb.conn(3, 4); wb.conn(4, 5); wb.conn(5, 8)
    wb.conn(8, 10, sa="True", da="Left"); wb.conn(2, 10, da="Right")
    wb.conn(10, 11, sa="Left", name="#1"); wb.conn(8, 11, sa="False", name="#2")
    wb.conn(11, 20, da="Left"); wb.conn(10, 20, sa="Right", da="Right")
    a = tree(wb, 100, (10, "Join"), SMALL, box)
    b = tree(wb, 200, (20, "Join"), SMALL, box)
    wb.union(300, box=box)
    wb.conn(a, 300, name="#1"); wb.conn(b, 300, name="#2")
    wb.select(301, [(f, True) for f in ("DEAL ID", "Unique ID", "Scenario", "Discrepancies", "RECONCILED T+1", "COMMENTS T+1")]
              + [("*Unknown", False)], box=box)
    wb.sort(302, [("DEAL ID", "Ascending")], box=box)
    wb.output(303, "Output\\RECON_RESULT.xlsx|||Reconciliation", box=box)
    wb.conn(300, 301); wb.conn(301, 302); wb.conn(302, 303)
    return wb


def big(target=938, trees=4):
    """About `target` tools with a production workflow's mix of kinds: `trees` copies of the full scenario tree, the
    rest unique preparation chains (every rule different, so nothing in them can be factored)."""
    wb = WB()
    nxt = [5000]

    def nid():
        nxt[0] += 1
        return nxt[0]

    days = ["T+1", "T+1", "T+2", "T+2"]
    for k in range(trees):
        box = ("Reconciliation " + days[k % 4],)
        wb.container(list(box), 9000 + k) if box not in wb.boxes else None
        j = wb.join(1000 + 200 * k, ["DEAL ID" if k % 2 == 0 else "Match Key " + days[k % 4]],
                    ["DEAL ID" if k % 2 == 0 else "Match Key " + days[k % 4]], RENAME + [("*Unknown", True)], box=box)
        a = wb.input(nid(), "Source\\side_a_%d.xlsx|||`Sheet1$`" % k, box=box)
        b = wb.input(nid(), "Source\\side_b_%d.xlsx|||`Sheet1$`" % k, box=box)
        wb.conn(a, j, da="Left"); wb.conn(b, j, da="Right")
        end = tree(wb, 1000 + 200 * k, (j, "Join"), FULL, box, days[k % 4])
        o = wb.output(nid(), "Output\\REC_%d.xlsx|||Reconciliation" % k, box=box)
        wb.conn(end, o)
    used = len(wb.items)
    # counts by kind of a 938-tool production workflow, less what the trees used, spread over unique chains
    want = {"Formula": 297, "Filter": 83, "Select": 75, "Summarize": 64, "TextBox": 58, "FindReplace": 51, "Input": 47,
            "PBUD": 43, "AppendFields": 34, "Union": 33, "MultiFieldFormula": 33, "Output": 22, "Transpose": 22,
            "RunCommand": 15, "Unique": 12, "DynamicInput": 12, "Join": 8, "Sort": 7, "Sample": 6, "Cleanse": 4,
            "Message": 4, "RecordID": 3, "Directory": 3, "DateTime": 1}
    per_tree = {"Formula": 28, "Filter": 3, "Summarize": 4, "FindReplace": 4, "MultiFieldFormula": 4, "Transpose": 4,
                "Union": 1, "Join": 1, "Input": 2, "Output": 1}
    left = {k: max(v - per_tree.get(k, 0) * trees, 0) for k, v in want.items()}
    cols = ["Deal Id", "Long/Short", "Ccy 1", "Ccy 2", "Branch", "Account", "Trade Date",
            "Expiration", "Premium", "PremiumType", "RISKCLASS", "PayAmount", "RecAmount"]
    chain_no, prev_end, prev_src = 0, None, None
    starts = left["Input"] + left["Directory"]
    while sum(left.values()) > 0 and len(wb.items) < target and (left["Input"] or left["Directory"]):
        chain_no += 1
        u = chain_no
        todo = max(starts - chain_no + 1, 1)
        quota = {k: -(-v // todo) for k, v in left.items()}            # this chain's share of each kind
        box = ("Preparation %d" % ((chain_no - 1) // 6 + 1),)
        if box not in wb.boxes:
            wb.container(list(box), 9100 + chain_no)

        def take(kind):
            if left.get(kind, 0) > 0 and quota.get(kind, 0) > 0 and len(wb.items) < target:
                left[kind] -= 1
                quota[kind] -= 1
                return True
            return False

        cur = None

        def link(t, da="Input"):
            nonlocal cur
            if cur is not None:
                wb.conn(cur[0], t, sa=cur[1], da=da)
            cur = (t, "Output")

        if left["Directory"] > 0:
            left["Directory"] -= 1
            link(wb.directory(nid(), "Output", box=box))
            first_src = None
            if take("Filter"):
                link(wb.filter(nid(), 'Contains([FileName],"Consolidated %d") and !Contains([FileName],".bak")' % u, box=box))
                cur = (cur[0], "True")
            if take("Sort"):
                link(wb.sort(nid(), [("CreationTime", "Descending")], box=box))
            if take("Sample"):
                link(wb.sample(nid(), "First", 1, box=box))
            if take("Formula"):
                link(wb.formula(nid(), [("Sheet %d" % u, "[FullPath]+'|||Sheet%d$'" % u)], box=box))
            if take("DynamicInput"):
                link(wb.dynamic_input(nid(), "Output\\template_%d.xlsx|||`Sheet%d$`" % (u, u), "Sheet %d" % u, box=box))
        else:
            left["Input"] -= 1
            link(wb.input(nid(), "Source\\Extracts\\extract_%d.csv" % u, box=box, CodePage=28591, Delimeter=",", HeaderRow="True"))
            first_src = cur
            if take("DynamicInput"):
                if take("Formula"):
                    link(wb.formula(nid(), [("File %d" % u, "'Source\\Extracts\\FX_%d_'+replace([T],'-','')+'.csv'" % u)], box=box))
                link(wb.dynamic_input(nid(), "Source\\template_%d.csv" % u, "File %d" % u, box=box, CodePage=28591, Delimeter=","))
        if take("Cleanse"):
            link(wb.cleanse(nid(), cols[:6], box=box))
        for r in range(12):
            w = "%d_%d" % (u, r)
            if take("TextBox"):
                wb.text_box(nid(), "Section %s: prepares extract %d before matching" % (w, u), box=box)
            if take("Select"):
                link(wb.select(nid(), [(cols[(u + r + i) % len(cols)], True) for i in range(4)] +
                               [("Comment %s" % w, False), ("*Unknown", True)], box=box))       # later steps read what it keeps
            for i in range(3):
                if take("Formula"):
                    c1, c2 = cols[(u + r + i) % len(cols)], cols[(u + r + i + 3) % len(cols)]
                    ex = ["If [%s]='Long' then 'BUY' elseif [%s]='Short' then 'SELL' else 'K%s' endif" % (c1, c1, w),
                          "If [%s]='XAU' OR [%s]='XAG' OR [%s]='XPT' OR [%s]='XPD' THEN [%s] ELSE 'N%s' ENDIF" % (c1, c1, c1, c1, c2, w),
                          "IF [%s]=[%s] then REGEX_Replace([%s],' [A-Z]{3}','') else REGEX_Replace([%s],' %s','') endif" % (c1, c2, c1, c2, w)][i]
                    link(wb.formula(nid(), [("F%s_%d" % (w, i), ex)], box=box))
            if take("Filter"):
                link(wb.filter(nid(), "[%s] = 'V%s' OR IsEmpty([%s])" % (cols[(u + r) % len(cols)], w, cols[(u + r + 1) % len(cols)]), box=box))
                cur = (cur[0], "False")
            if take("Summarize"):
                keep, key = cur, cols[(u + r) % len(cols)]
                su = wb.summarize(nid(), [(key, "GroupBy", key), (key, "Count", "No. of %s" % w)], box=box)
                wb.conn(keep[0], su, sa=keep[1])
                if take("FindReplace"):
                    fr = wb.find_replace(nid(), key, key, "FindAny", ["No. of %s" % w], box=box)
                    wb.conn(keep[0], fr, sa=keep[1], da="Targets"); wb.conn(su, fr, da="Source")
                    cur = (fr, "Output")
            if prev_end and take("AppendFields"):
                ap = wb.append_fields(nid(), [("Source_T", True, "T_%s" % w), ("*Unknown", False)], box=box)
                wb.conn(cur[0], ap, sa=cur[1], da="Targets"); wb.conn(prev_end[0], ap, sa=prev_end[1], da="Source")
                cur = (ap, "Output")
            if take("MultiFieldFormula"):
                link(wb.multi_field(nid(), cols[(u + r) % 5:(u + r) % 5 + 3], "Uppercase(Trim([_CurrentField_])) + '%s'" % w, box=box))
            if take("Transpose"):
                keep = cur
                tr = wb.transpose(nid(), [cols[0]], ["F%s_0" % w, "F%s_1" % w], box=box)
                wb.conn(keep[0], tr, sa=keep[1])
                if take("Summarize"):
                    su = wb.summarize(nid(), [(cols[0], "GroupBy", cols[0]), ("Value", "Concat", "All_%s" % w, ";")], box=box)
                    wb.conn(tr, su)
                    if take("FindReplace"):
                        fr = wb.find_replace(nid(), cols[0], cols[0], "FindWhole", ["All_%s" % w], box=box)
                        wb.conn(keep[0], fr, sa=keep[1], da="Targets"); wb.conn(su, fr, da="Source")
                        cur = (fr, "Output")
            if take("Unique"):
                link(wb.unique(nid(), [cols[0], cols[(u + r) % len(cols)]], box=box))
                cur = (cur[0], "Unique")
            if prev_src and take("Join"):
                j = wb.join(nid(), [cols[0]], [cols[0]], [("Right_" + cols[0], False), ("Right_" + cols[1], True, "P%s_%s" % (w, cols[1])),
                                                         ("*Unknown", True)], box=box)
                wb.conn(cur[0], j, sa=cur[1], da="Left"); wb.conn(prev_src[0], j, sa=prev_src[1], da="Right")   # a lookup into another extract
                cur = (j, "Join")
            if take("RecordID"):
                link(wb.record_id(nid(), "RecordID", 1, "String", 6, box=box))
            if take("DateTime"):
                link(wb.datetime(nid(), "Expiration", "dd/MM/yyyy", "EXPIRY DATE", box=box))
            if prev_end and take("Union"):
                un = wb.union(nid(), box=box)
                wb.conn(cur[0], un, sa=cur[1], name="#1"); wb.conn(prev_end[0], un, sa=prev_end[1], name="#2")
                cur = (un, "Output")
            if take("Message"):
                link(wb.message(nid(), "Filter", "Error", '"Missing branch in extract %s"' % w, "IsNull([Branch])", box=box))
            if take("PBUD"):
                pb = wb.pbud(nid(), box=box)
                wb.conn(cur[0], pb, sa=cur[1], da="Input1")
                if take("Output"):
                    o = wb.output(nid(), "Output\\prep_%s.xlsx|||Sheet1" % w, box=box)
                    wb.conn(pb, o, sa="Output1")
                if take("RunCommand"):
                    rc = wb.run_command(nid(), "cscript.exe", "RunMacro.vbs Configuration.xlsm Macro%s" % w, box=box)
                    wb.conn(pb, rc, sa="Output2")
                cur = (pb, "Output1")
        if take("Output"):
            o = wb.output(nid(), "Output\\prep_%d.csv" % u, box=box)
            wb.conn(cur[0], o, sa=cur[1])
        prev_end = cur
        prev_src = first_src
    return wb


if __name__ == "__main__":
    import sys
    wb = big(int(sys.argv[2]) if len(sys.argv) > 2 else 938)
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        fh.write(wb.xml())
    print("%d tools, %d connections" % (len(wb.items) + len(wb.boxes), len(wb.conns)))
